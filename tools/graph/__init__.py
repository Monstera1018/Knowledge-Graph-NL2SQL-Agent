from dataclasses import dataclass

from tools.graph.crud.column import create_column, delete_column, get_column, list_columns, update_column
from tools.graph.crud.enum import create_enum, delete_enum, get_enum, list_enum_items, list_enums, update_enum
from tools.graph.crud.knowledge import (
    create_knowledge,
    delete_knowledge,
    get_knowledge,
    list_knowledge,
    list_knowledge_relations,
    update_knowledge,
)
from tools.graph.crud.sql import create_sql, delete_sql, get_sql, list_sql_relations, list_sqls, update_sql, \
    upsert_llm_generated_sql
from tools.graph.crud.table import (
    create_table,
    delete_table,
    get_table,
    list_all_table_join_relations,
    list_table_join_relations,
    list_tables,
    replace_table_join_relations,
    update_table,
)
from tools.graph.internal import _run_read, close_graph_driver
from tools.graph.model import (
    ConflictError,
    EdgeType,
    KnowledgeRelation,
    NotFoundError,
    NodeType,
    TableJoinSpec,
)

MAX_LIST_PAGE_SIZE = 10_000


@dataclass(frozen=True)
class GraphNode:
    uuid: str
    type: NodeType
    label: str


@dataclass(frozen=True)
class GraphEdge:
    type: EdgeType
    source: str
    target: str


@dataclass(frozen=True)
class GraphSnapshot:
    focus_uuid: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]


def _node_label(node: dict) -> str:
    return str(node.get("name") or node.get("value") or node.get("uuid") or "")


async def get_node_subgraph_snapshot(workspace_id: str, node_uuid: str, depth: int = 1) -> GraphSnapshot:
    """返回焦点节点、``depth`` 跳内的所有节点，以及这些节点之间的所有边。"""
    depth = max(1, min(depth, 2))
    rows = await _run_read(
        f"""
        MATCH (focus {{uuid: $uuid, workspace_id: $workspace_id}})
        OPTIONAL MATCH p=(focus)-[*1..{depth}]-(n)
        WHERE p IS NOT NULL AND all(node IN nodes(p) WHERE node.workspace_id = $workspace_id)
        WITH focus, [x IN collect(DISTINCT n) WHERE x IS NOT NULL] AS neighbors
        WITH [focus] + neighbors AS raw_list
        UNWIND raw_list AS node
        WITH collect(DISTINCT node) AS nodes
        OPTIONAL MATCH (a)-[r]->(b)
        WHERE a IN nodes AND b IN nodes
          AND a.workspace_id = $workspace_id
          AND b.workspace_id = $workspace_id
        WITH nodes, [rel IN collect(DISTINCT r) WHERE rel IS NOT NULL] AS rels
        RETURN [node IN nodes | properties(node)] AS nodes,
               [edge IN rels | {{
                   type: type(edge),
                   source: startNode(edge).uuid,
                   target: endNode(edge).uuid
               }}] AS edges
        """,
        uuid=node_uuid,
        workspace_id=workspace_id,
    )
    if not rows:
        raise NotFoundError(f"节点不存在: {node_uuid}")

    node_by_uuid: dict[str, GraphNode] = {}
    for raw in rows[0]["nodes"]:
        if not raw or raw.get("workspace_id") != workspace_id:
            continue
        raw_type = raw.get("type")
        if not raw_type:
            continue
        node = GraphNode(
            uuid=str(raw["uuid"]),
            type=NodeType(raw_type),
            label=_node_label(raw),
        )
        node_by_uuid[node.uuid] = node

    edge_by_key: dict[tuple[str, str, str], GraphEdge] = {}
    for raw in rows[0]["edges"]:
        if not raw:
            continue
        edge = GraphEdge(
            type=EdgeType(raw["type"]),
            source=str(raw["source"]),
            target=str(raw["target"]),
        )
        if edge.source in node_by_uuid and edge.target in node_by_uuid:
            edge_by_key[(edge.type.value, edge.source, edge.target)] = edge

    if node_uuid not in node_by_uuid:
        raise NotFoundError(f"节点不存在: {node_uuid}")

    return GraphSnapshot(
        focus_uuid=node_uuid,
        nodes=sorted(node_by_uuid.values(), key=lambda item: (item.type.value, item.label, item.uuid)),
        edges=sorted(edge_by_key.values(), key=lambda item: (item.type.value, item.source, item.target)),
    )
