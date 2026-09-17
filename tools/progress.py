import sys
from enum import StrEnum
from typing import Dict, List, Awaitable, Callable

import tqdm
from pydantic import BaseModel, Field


class ImportStage(StrEnum):
    IMPORT_TABLE_SCHEMA = "IMPORT_TABLE_SCHEMA"
    IMPORT_ENUM_VALUE = "IMPORT_ENUM_VALUE"
    IMPORT_KNOWLEDGE = "IMPORT_KNOWLEDGE"
    IMPORT_SQL = "IMPORT_SQL"
    IMPORT_TABLE_RELATION = "IMPORT_TABLE_RELATION"


class ProgressEntity(StrEnum):
    TABLE = "Table"
    COLUMN = "Column"
    ENUM = "Enum"
    KNOWLEDGE = "Knowledge"
    SQL = "SQL"
    RELATION = "Relation"


STAGE_ENTITY_HIERARCHY: Dict[ImportStage, list[ProgressEntity]] = {
    ImportStage.IMPORT_TABLE_SCHEMA: [ProgressEntity.TABLE, ProgressEntity.COLUMN],
    ImportStage.IMPORT_ENUM_VALUE: [
        ProgressEntity.TABLE,
        ProgressEntity.COLUMN,
        ProgressEntity.ENUM,
    ],
    ImportStage.IMPORT_KNOWLEDGE: [ProgressEntity.KNOWLEDGE],
    ImportStage.IMPORT_SQL: [ProgressEntity.SQL],
    ImportStage.IMPORT_TABLE_RELATION: [ProgressEntity.RELATION],
}

_ENTITY_LABELS: Dict[ProgressEntity, str] = {
    ProgressEntity.TABLE: "表",
    ProgressEntity.COLUMN: "列",
    ProgressEntity.ENUM: "枚举",
    ProgressEntity.KNOWLEDGE: "业务知识",
    ProgressEntity.SQL: "SQL",
    ProgressEntity.RELATION: "表关系",
}

_STAGE_LABELS: Dict[ImportStage, str] = {
    ImportStage.IMPORT_TABLE_SCHEMA: "表结构",
    ImportStage.IMPORT_ENUM_VALUE: "枚举值",
    ImportStage.IMPORT_KNOWLEDGE: "业务知识",
    ImportStage.IMPORT_SQL: "历史SQL",
    ImportStage.IMPORT_TABLE_RELATION: "表关系",
}


class ProgressLevel(BaseModel):
    entity: ProgressEntity
    name: str
    current: int = Field(ge=0)
    total: int = Field(ge=0)


class ImportProgressEvent(BaseModel):
    stage: ImportStage
    scope: ProgressEntity
    levels: List[ProgressLevel]
    overall_current: int = Field(ge=0)
    overall_total: int = Field(ge=0)
    percent: float = Field(ge=0, le=100)
    message: str


ProgressCallback = Callable[[ImportProgressEvent], Awaitable[None] | None]


def calc_percent(overall_current: int, overall_total: int) -> float:
    if overall_total == 0:
        return 100.0
    return round(overall_current / overall_total * 100, 1)


def build_progress_message(levels: list[ProgressLevel]) -> str:
    if not levels:
        return "处理中"

    parts = []
    for level in levels:
        label = _ENTITY_LABELS[level.entity]
        parts.append(f"{label} {level.name} ({level.current}/{level.total})")
    return " · ".join(parts)


def build_import_progress(
        stage: ImportStage,
        scope: ProgressEntity,
        levels: list[ProgressLevel],
        overall_current: int,
        overall_total: int,
        message: str | None = None,
) -> ImportProgressEvent:
    return ImportProgressEvent(
        stage=stage,
        scope=scope,
        levels=levels,
        overall_current=overall_current,
        overall_total=overall_total,
        percent=calc_percent(overall_current, overall_total),
        message=message or build_progress_message(levels),
    )


async def emit_progress(
        on_progress: ProgressCallback | None,
        event: ImportProgressEvent,
) -> None:
    if on_progress is None:
        return

    result = on_progress(event)
    if result is not None:
        await result


class CliProgressRenderer:
    """命令行进度渲染：单条 tqdm，postfix 明确显示当前表/列名称。"""

    def __init__(self, stage: ImportStage) -> None:
        self._bar = tqdm.tqdm(
            total=1,
            desc=_STAGE_LABELS.get(stage, stage.value),
            unit="项",
            dynamic_ncols=True,
            mininterval=0.15,
            leave=True,
            file=sys.stderr,
            bar_format="{desc}: {percentage:3.0f}%|{bar}| {n}/{total} [{elapsed}<{remaining}] {postfix}",
        )

    @staticmethod
    def _shorten(name: str, max_len: int = 32) -> str:
        if len(name) <= max_len:
            return name
        return f"{name[: max_len - 3]}..."

    @classmethod
    def _find_level(cls, event: ImportProgressEvent, entity: ProgressEntity) -> ProgressLevel | None:
        for level in event.levels:
            if level.entity == entity:
                return level
        return None

    @classmethod
    def _format_level(cls, entity: ProgressEntity, level: ProgressLevel, *, name_max_len: int) -> str:
        label = _ENTITY_LABELS[entity]
        name = cls._shorten(level.name, name_max_len)
        return f"{label}[{name}] {level.current}/{level.total}"

    @classmethod
    def _format_postfix(cls, event: ImportProgressEvent) -> str:
        parts: list[str] = []

        table = cls._find_level(event, ProgressEntity.TABLE)
        if table is not None:
            parts.append(cls._format_level(ProgressEntity.TABLE, table, name_max_len=40))

        column = cls._find_level(event, ProgressEntity.COLUMN)
        if column is not None:
            parts.append(cls._format_level(ProgressEntity.COLUMN, column, name_max_len=32))

        enum = cls._find_level(event, ProgressEntity.ENUM)
        if enum is not None:
            parts.append(cls._format_level(ProgressEntity.ENUM, enum, name_max_len=24))

        for entity in (
                ProgressEntity.KNOWLEDGE,
                ProgressEntity.SQL,
                ProgressEntity.RELATION,
        ):
            level = cls._find_level(event, entity)
            if level is not None:
                parts.append(cls._format_level(entity, level, name_max_len=36))

        return " | ".join(parts) if parts else event.message

    async def __call__(self, event: ImportProgressEvent) -> None:
        if event.overall_total > 0:
            self._bar.total = event.overall_total
        self._bar.n = event.overall_current
        self._bar.set_postfix_str(self._format_postfix(event), refresh=False)
        self._bar.refresh()

    def close(self) -> None:
        self._bar.close()
