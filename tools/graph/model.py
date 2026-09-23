from enum import StrEnum
from typing import Any, List
from dataclasses import dataclass

from pydantic import BaseModel, field_validator, model_validator


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


class KnowledgeSource(StrEnum):
    IMPORT = "import"
    LLM = "llm"


def normalize_knowledge_source(value: Any) -> KnowledgeSource:
    text = str(value or "").strip().lower()
    if text in {KnowledgeSource.LLM.value, "generated", "extracted", "llm生成", "llm抽取"}:
        return KnowledgeSource.LLM
    return KnowledgeSource.IMPORT


class KnowledgeReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"


def normalize_knowledge_enabled(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def normalize_knowledge_review_status(
        value: Any,
        *,
        source: Any = None,
        enabled: Any = None,
) -> KnowledgeReviewStatus:
    text = str(value or "").strip().lower()
    if text in {KnowledgeReviewStatus.PENDING.value, "待审核"}:
        return KnowledgeReviewStatus.PENDING
    if text in {KnowledgeReviewStatus.APPROVED.value, "已审核", "passed", "通过"}:
        return KnowledgeReviewStatus.APPROVED
    if normalize_knowledge_source(source) == KnowledgeSource.LLM and not normalize_knowledge_enabled(enabled):
        return KnowledgeReviewStatus.PENDING
    return KnowledgeReviewStatus.APPROVED


class KnowledgeNode(BaseNode):
    name: str
    description: str
    source: KnowledgeSource = KnowledgeSource.IMPORT
    enabled: bool = True
    review_status: KnowledgeReviewStatus = KnowledgeReviewStatus.APPROVED
    source_sql_uuid: str = ""
    created_at: str = ""
    updated_at: str = ""

    @field_validator("source", mode="before")
    @classmethod
    def _coerce_source(cls, value: Any) -> KnowledgeSource:
        return normalize_knowledge_source(value)

    @field_validator("enabled", mode="before")
    @classmethod
    def _coerce_enabled(cls, value: Any) -> bool:
        return normalize_knowledge_enabled(value)

    @model_validator(mode="before")
    @classmethod
    def _coerce_review_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        payload["review_status"] = normalize_knowledge_review_status(
            payload.get("review_status"),
            source=payload.get("source"),
            enabled=payload.get("enabled"),
        )
        return payload

    @field_validator("source_sql_uuid", "created_at", "updated_at", mode="before")
    @classmethod
    def _coerce_text(cls, value: Any) -> str:
        return str(value or "").strip()


class SQLSource(StrEnum):
    IMPORT = "import"
    LLM = "llm"


class SQLReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"


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


def normalize_sql_review_status(
        value: Any,
        *,
        source: Any = None,
        enabled: Any = None,
) -> SQLReviewStatus:
    text = str(value or "").strip().lower()
    if text in {SQLReviewStatus.PENDING.value, "待审核"}:
        return SQLReviewStatus.PENDING
    if text in {SQLReviewStatus.APPROVED.value, "已审核", "passed", "通过"}:
        return SQLReviewStatus.APPROVED
    if normalize_sql_source(source) == SQLSource.LLM and not normalize_sql_enabled(enabled):
        return SQLReviewStatus.PENDING
    return SQLReviewStatus.APPROVED


def normalize_sql_knowledge_extracted(value: Any, review_status: SQLReviewStatus) -> bool:
    if value is None:
        return review_status == SQLReviewStatus.APPROVED
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


class SQLNode(BaseNode):
    name: str
    logic: str
    content: str
    dialect: str
    source: SQLSource = SQLSource.IMPORT
    enabled: bool = True
    review_status: SQLReviewStatus = SQLReviewStatus.APPROVED
    knowledge_extracted: bool = True
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

    @model_validator(mode="before")
    @classmethod
    def _coerce_review_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        status = normalize_sql_review_status(
            payload.get("review_status"),
            source=payload.get("source"),
            enabled=payload.get("enabled"),
        )
        payload["review_status"] = status
        payload["knowledge_extracted"] = normalize_sql_knowledge_extracted(
            payload.get("knowledge_extracted"),
            status,
        )
        return payload

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
