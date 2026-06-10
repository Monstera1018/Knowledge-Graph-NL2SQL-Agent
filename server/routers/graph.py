from fastapi import APIRouter, Depends, Query

from server.schemas.graph import GraphSnapshot
from server.auth.deps import get_workspace_id
from tools.graph import GraphSnapshot as GraphSnapshotDataclass
from tools.graph import get_node_subgraph_snapshot

graph_router = APIRouter(prefix="/api/graph", tags=["graph"])


@graph_router.get("/{uuid}", response_model=GraphSnapshot)
async def get_graph_subgraph(
        uuid: str,
        depth: int = Query(1, ge=1, le=2, description="子图跳数"),
        workspace_id: str = Depends(get_workspace_id),
) -> GraphSnapshot:
    snapshot = await get_node_subgraph_snapshot(workspace_id, uuid, depth=depth)
    return _to_schema(snapshot)


def _to_schema(snapshot: GraphSnapshotDataclass) -> GraphSnapshot:
    return GraphSnapshot(
        focus_uuid=snapshot.focus_uuid,
        nodes=[
            {"uuid": node.uuid, "type": node.type, "label": node.label}
            for node in snapshot.nodes
        ],
        edges=[
            {"type": edge.type, "source": edge.source, "target": edge.target}
            for edge in snapshot.edges
        ],
    )
