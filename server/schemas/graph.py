from pydantic import BaseModel, Field

from tools.graph.model import EdgeType, NodeType


class GraphNodeView(BaseModel):
    uuid: str
    type: NodeType
    label: str


class GraphEdgeView(BaseModel):
    type: EdgeType
    source: str
    target: str


class GraphSnapshot(BaseModel):
    focus_uuid: str
    nodes: list[GraphNodeView]
    edges: list[GraphEdgeView]


class GraphQuery(BaseModel):
    depth: int = Field(default=1, ge=1, le=2)
