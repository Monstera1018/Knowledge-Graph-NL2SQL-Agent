import asyncio
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from io import BytesIO
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from server.auth.deps import get_workspace_id
from tools.ingest import IMPORT_JOBS, run_import
from tools.progress import ImportProgressEvent, ImportStage, ProgressCallback

router = APIRouter(prefix="/api/ingest", tags=["ingest"])

_JOB_BY_STAGE = {job.stage: job for job in IMPORT_JOBS}


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

class JobStatus(StrEnum):
    PENDING = "Pending"
    RUNNING = "Running"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"


class JobSnapshot(BaseModel):
    job_id: str
    workspace_id: str
    type: ImportStage
    label: str
    filename: str
    status: JobStatus
    percent: float = Field(ge=0, le=100, default=0)
    message: str = ""
    progress: Optional[ImportProgressEvent] = None
    error: Optional[str] = None
    created_at: str
    updated_at: str
    finished_at: Optional[str] = None


class JobCreatedResponse(BaseModel):
    job_id: str


# ---------------------------------------------------------------------------
# 导入执行
# ---------------------------------------------------------------------------

def _read_bytes_as_df(content: bytes, filename: str) -> pd.DataFrame:
    if not content:
        raise ValueError(f"上传文件为空: {filename or 'unknown'}")

    name = (filename or "").lower()
    if name and not name.endswith((".xlsx", ".xls")):
        raise ValueError(f"仅支持 Excel 文件 (.xlsx/.xls): {filename}")

    return pd.read_excel(BytesIO(content))


async def _run_import_job(
        workspace_id: str,
        stage: ImportStage,
        content: bytes,
        filename: str,
        *,
        on_progress: ProgressCallback | None = None,
) -> str:
    job = _JOB_BY_STAGE.get(stage)
    if job is None:
        raise ValueError(f"未知导入类型: {stage.value}")

    df = _read_bytes_as_df(content, filename)
    await run_import(workspace_id, job, df, on_progress=on_progress)
    return job.label


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stage_label(stage: ImportStage) -> str:
    for job in IMPORT_JOBS:
        if job.stage == stage:
            return job.label
    return stage.value


# ---------------------------------------------------------------------------
# 任务存储
# ---------------------------------------------------------------------------

class _JobState:
    __slots__ = (
        "job_id",
        "workspace_id",
        "stage",
        "label",
        "filename",
        "status",
        "percent",
        "message",
        "progress",
        "error",
        "created_at",
        "updated_at",
        "finished_at",
    )

    def __init__(
            self,
            *,
            job_id: str,
            workspace_id: str,
            stage: ImportStage,
            label: str,
            filename: str,
    ) -> None:
        now = _utc_now()
        self.job_id = job_id
        self.workspace_id = workspace_id
        self.stage = stage
        self.label = label
        self.filename = filename
        self.status = JobStatus.PENDING
        self.percent = 0.0
        self.message = "等待开始"
        self.progress: Optional[ImportProgressEvent] = None
        self.error: Optional[str] = None
        self.created_at = now
        self.updated_at = now
        self.finished_at: Optional[str] = None

    def to_snapshot(self) -> JobSnapshot:
        progress = None
        if self.progress is not None:
            progress = ImportProgressEvent.model_validate(
                self.progress.model_dump(mode="json")
            )
        return JobSnapshot(
            job_id=self.job_id,
            workspace_id=self.workspace_id,
            type=self.stage,
            label=self.label,
            filename=self.filename,
            status=self.status,
            percent=self.percent,
            message=self.message,
            progress=progress,
            error=self.error,
            created_at=self.created_at,
            updated_at=self.updated_at,
            finished_at=self.finished_at,
        )


class _JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, _JobState] = {}
        self._lock = asyncio.Lock()

    async def create_and_start(
            self,
            workspace_id: str,
            stage: ImportStage,
            content: bytes,
            filename: str,
    ) -> str:
        _read_bytes_as_df(content, filename)

        job_id = uuid.uuid4().hex
        state = _JobState(
            job_id=job_id,
            workspace_id=workspace_id,
            stage=stage,
            label=_stage_label(stage),
            filename=filename,
        )
        async with self._lock:
            self._jobs[job_id] = state

        asyncio.create_task(self._run(job_id, workspace_id, stage, content, filename))
        return job_id

    async def get(self, workspace_id: str, job_id: str) -> Optional[JobSnapshot]:
        async with self._lock:
            state = self._jobs.get(job_id)
            if state is None or state.workspace_id != workspace_id:
                return None
            return state.to_snapshot()

    async def list_all(self, workspace_id: str) -> list[JobSnapshot]:
        async with self._lock:
            snapshots = [
                state.to_snapshot()
                for state in self._jobs.values()
                if state.workspace_id == workspace_id
            ]
        snapshots.sort(key=lambda item: item.created_at, reverse=True)
        return snapshots

    async def _run(
            self,
            job_id: str,
            workspace_id: str,
            stage: ImportStage,
            content: bytes,
            filename: str,
    ) -> None:
        async def on_progress(event: ImportProgressEvent) -> None:
            async with self._lock:
                state = self._jobs.get(job_id)
                if state is None:
                    return
                state.status = JobStatus.RUNNING
                state.progress = event
                state.percent = event.percent
                state.message = event.message
                state.updated_at = _utc_now()

        async with self._lock:
            state = self._jobs.get(job_id)
            if state is None:
                return
            state.status = JobStatus.RUNNING
            state.message = "导入中"
            state.updated_at = _utc_now()

        try:
            label = await _run_import_job(
                workspace_id,
                stage,
                content,
                filename,
                on_progress=on_progress,
            )
        except Exception as exc:
            async with self._lock:
                state = self._jobs.get(job_id)
                if state is None:
                    return
                state.status = JobStatus.FAILED
                state.error = str(exc)
                state.message = "导入失败"
                state.updated_at = _utc_now()
                state.finished_at = _utc_now()
            return

        async with self._lock:
            state = self._jobs.get(job_id)
            if state is None:
                return
            state.status = JobStatus.SUCCEEDED
            state.label = label
            state.percent = 100.0
            state.message = f"{label} 已更新"
            state.error = None
            state.updated_at = _utc_now()
            state.finished_at = _utc_now()


_job_store = _JobStore()


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@router.post("", response_model=JobCreatedResponse)
async def create_import_job(
        type: ImportStage = Form(..., description="导入类型，如 IMPORT_TABLE_SCHEMA"),
        file: UploadFile = File(..., description="Excel 文件 (.xlsx)"),
        workspace_id: str = Depends(get_workspace_id),
) -> JobCreatedResponse:
    """上传 Excel 并创建后台导入任务，立即返回 job_id。"""
    content = await file.read()
    filename = file.filename or ""

    try:
        job_id = await _job_store.create_and_start(workspace_id, type, content, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return JobCreatedResponse(job_id=job_id)


@router.get("", response_model=list[JobSnapshot])
async def list_import_jobs(
        workspace_id: str = Depends(get_workspace_id),
) -> list[JobSnapshot]:
    """返回所有导入任务，按创建时间倒序。"""
    return await _job_store.list_all(workspace_id)


@router.get("/{job_id}", response_model=JobSnapshot)
async def get_import_job(
        job_id: str,
        workspace_id: str = Depends(get_workspace_id),
) -> JobSnapshot:
    """查询导入任务状态与最新进度。"""
    snapshot = await _job_store.get(workspace_id, job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
    return snapshot
