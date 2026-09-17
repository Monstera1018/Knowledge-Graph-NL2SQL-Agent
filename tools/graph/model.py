from enum import StrEnum
from typing import Any, List
from dataclasses import dataclass

from pydantic import BaseModel, field_validator


class NodeType(StrEnum):
    TABLE = "TABLE"
    COLUMN = "COLUMN"
    ENUM = "ENUM"
    KNOWLEDGE = "KNOWLEDGE"
    SQL = "SQL"


class BaseNode(BaseModel):
    uuid: str
    workspace_id: str
    type: NodeType


class TableNode(BaseNode):
    name: str
    comment: str
    description: str


class ColumnNode(BaseNode):
    name: str
    comment: str
    dtype: str


class EnumNode(BaseNode):
    value: str


class EnumListItem(EnumNode):
    table_uuid: str = ""
    table_name: str = ""
    table_comment: str = ""
    column_uuid: str = ""
    column_name: str = ""
    column_comment: str = ""
    column_dtype: str = ""


class KnowledgeNode(BaseNode):
    name: str
    description: str


class SQLSource(StrEnum):
    IMPORT = "import"
    LLM = "llm"


def normalize_sql_source(value: Any) -> SQLSource:
    text = str(value or "").strip().lower()
    if text in {SQLSource.LLM.value, "generated", "runtime", "llm生成"}:
        return SQLSource.LLM
    return SQLSource.IMPORT


def normalize_sql_enabled(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


class SQLNode(BaseNode):
    name: str
    logic: str
    content: str
    dialect: str
    source: SQLSource = SQLSource.IMPORT
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""

    @field_validator("source", mode="before")
    @classmethod
    def _coerce_source(cls, value: Any) -> SQLSource:
        return normalize_sql_source(value)

    @field_validator("enabled", mode="before")
    @classmethod
    def _coerce_enabled(cls, value: Any) -> bool:
        return normalize_sql_enabled(value)

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _coerce_timestamp(cls, value: Any) -> str:
        return str(value or "").strip()


class EdgeType(StrEnum):
    HAS = "HAS"
    USES = "USES"
    DESCRIBES = "DESCRIBES"
    JOINS = "JOINS"


class BaseEdge(BaseModel):
    type: EdgeType
    from_uuid: str
    to_uuid: str


class HasEdge(BaseEdge):
    pass


class DescribeEdge(BaseEdge):
    pass


class UseEdge(BaseEdge):
    pass


class JoinEdge(BaseEdge):
    condition: str


class NotFoundError(LookupError):
    pass


class ConflictError(ValueError):
    pass


class TableJoinRelation(BaseModel):
    from_table_uuid: str
    from_table_name: str
    to_table_uuid: str
    to_table_name: str
    condition: str


class TableJoinSpec(BaseModel):
    from_table_name: str
    to_table_name: str
    condition: str = ""


class KnowledgeRelation(BaseModel):
    table_name: str
    column_names: List[str]


class SQLRelation(BaseModel):
    table_name: str
    column_names: List[str]


class PageResult[T](BaseModel):
    items: List[T]
    total: int
    page: int
    page_size: int


@dataclass
class _ColumnSnapshot:
    name: str
    comment: str
    dtype: str
    enums: List[str]


@dataclass
class _JoinEdgeSnapshot:
    from_uuid: str
    to_uuid: str
    condition: str
