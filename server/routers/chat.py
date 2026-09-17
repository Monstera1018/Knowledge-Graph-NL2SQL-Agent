from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from server.auth.deps import get_current_user, get_workspace_id
from server.auth.models import AuthUser
from server.chat.service import chat_service
from server.schemas.chat import (
    AgentListResponse,
    ChatProtocolInfo,
    SendMessageRequest,
)
from adk_agents.copilot.report import resolve_report_file

router = APIRouter(prefix="/api/chat", tags=["chat"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@router.get("/protocol", response_model=ChatProtocolInfo)
async def get_chat_protocol() -> ChatProtocolInfo:
    """返回当前服务端实现的流式协议版本。"""
    return ChatProtocolInfo()


@router.get("/agents", response_model=AgentListResponse)
async def list_chat_agents(
        _: Annotated[AuthUser, Depends(get_current_user)],
) -> AgentListResponse:
    """列出可用 agent。"""
    return AgentListResponse(agents=chat_service.list_agents())


@router.post("/sessions/{session_id}/messages")
async def send_chat_message(
    session_id: str,
    body: SendMessageRequest,
    user: Annotated[AuthUser, Depends(get_current_user)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> StreamingResponse:
    """发送用户消息并以 SSE 流式返回 agent 回合事件。"""
    if not session_id.strip():
        raise HTTPException(status_code=400, detail="session_id 不能为空")

    if not chat_service.has_agent(body.agent_id):
        raise HTTPException(status_code=404, detail=f"未知 agent: {body.agent_id}")

    async def body_stream():
        async for line in chat_service.stream_sse(
            session_id.strip(),
            body.agent_id,
            body.content,
            user_id=user.id,
            workspace_id=workspace_id,
        ):
            yield line

    return StreamingResponse(
        body_stream(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.get("/reports/{file_id}")
async def download_analysis_report(
        file_id: str,
        _: Annotated[AuthUser, Depends(get_current_user)],
        filename: str = Query(default="分析报告.docx"),
) -> FileResponse:
    path = resolve_report_file(file_id)
    if path is None:
        raise HTTPException(status_code=404, detail="报告文件不存在或已过期")
    download_name = (filename or "").strip() or "分析报告.docx"
    if not download_name.lower().endswith(".docx"):
        download_name += ".docx"
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=download_name,
    )
