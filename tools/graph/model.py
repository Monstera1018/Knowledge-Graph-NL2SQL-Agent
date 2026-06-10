from enum import StrEnum
from typing import List
from dataclasses import dataclass

from pydantic import BaseModel


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


class KnowledgeNode(BaseNode):
    name: str
    description: str


class SQLNode(BaseNode):
    name: str
    logic: str
    content: str
    dialect: str


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
