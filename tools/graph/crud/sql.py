from typing import Any, List

from neo4j import AsyncManagedTransaction

from tools.common import normalize_text
from tools.graph.internal import create_graph_node_tx, build_stable_table_uuid, build_stable_column_uuid, \
    commit_graph_then_vectors, _run_read, graph_node_exists_tx, upsert_graph_node_tx, \
    upsert_graph_edge_tx, _run_write, \
    build_stable_sql_uuid
from tools.graph.model import NodeType, EdgeType, ConflictError, NotFoundError, PageResult, \
    SQLNode, UseEdge, SQLRelation
from tools.sql import find_all_sources


async def _clear_sql_uses_edges_tx(tx: AsyncManagedTransaction, sql_uuid: str) -> None:
    await tx.run(
        f"MATCH (s:{NodeType.SQL} {{uuid: $uuid}})-[r:{EdgeType.USES}]->() DELETE r",
        uuid=sql_uuid,
    )


def _node_type_from_row(data: dict[str, Any], labels: list[str]) -> NodeType:
    raw = data.get("type")
    if raw:
        return NodeType(raw)
    for label in labels:
        try:
            return NodeType(label)
        except ValueError:
            continue
    raise ValueError(f"无法识别节点类型: {labels}")


def _to_sql_node(data: dict[str, Any]) -> SQLNode:
    return SQLNode.model_validate(data)


async def _delete_sql_node_tx(tx: AsyncManagedTransaction, sql_uuid: str) -> None:
    await tx.run(
        f"MATCH (s:{NodeType.SQL} {{uuid: $uuid}}) DETACH DELETE s",
        uuid=sql_uuid,
    )


async def _load_sql_relations(sql_uuid: str) -> List[SQLRelation]:
    source_type = NodeType.SQL
    source_uuid = sql_uuid
    edge_type = EdgeType.USES

    rows = await _run_read(
        f"""
            MATCH (source:{source_type} {{uuid: $uuid}})-[:{edge_type}]->(target)
            RETURN target, labels(target) AS target_labels
            """,
        uuid=source_uuid,
    )

    relations: dict[str, list[str]] = {}
    column_targets: list[tuple[str, str]] = []

    for row in rows:
        target = dict(row["target"])
        labels = list(row["target_labels"])
        node_type = _node_type_from_row(target, labels)
        if node_type == NodeType.TABLE:
            relations.setdefault(target["name"], [])
        elif node_type == NodeType.COLUMN:
            column_targets.append((target["name"], target["uuid"]))

    if column_targets:
        parent_rows = await _run_read(
            f"""
                UNWIND $pairs AS pair
                MATCH (t:{NodeType.TABLE})-[:{EdgeType.HAS}]->(c:{NodeType.COLUMN} {{uuid: pair.uuid}})
                RETURN pair.name AS column_name, t.name AS table_name
                """,
            pairs=[{"name": name, "uuid": uuid} for name, uuid in column_targets],
        )
        for row in parent_rows:
            table_name = row["table_name"]
            column_name = row["column_name"]
            relations.setdefault(table_name, []).append(column_name)

    return [
        SQLRelation(
            table_name=table_name,
            column_names=sorted(set(column_names)),
        )
        for table_name, column_names in sorted(relations.items())
    ]


async def _add_sql_uses_edges_tx(
        tx: AsyncManagedTransaction,
        workspace_id: str,
        sql_uuid: str,
        content: str,
        dialect: str,
) -> None:
    table_and_columns, error = find_all_sources(content, dialect.lower())
    if table_and_columns is None:
        raise ValueError(error or "无法解析 SQL")

    for table_name, column_names in table_and_columns.items():
        for column_name in column_names:
            await upsert_graph_edge_tx(tx, UseEdge(
                type=EdgeType.USES,
                from_uuid=sql_uuid,
                to_uuid=build_stable_column_uuid(workspace_id, table_name, column_name),
            ))
        await upsert_graph_edge_tx(tx, UseEdge(
            type=EdgeType.USES,
            from_uuid=sql_uuid,
            to_uuid=build_stable_table_uuid(workspace_id, table_name),
        ))


async def create_sql(
        workspace_id: str,
        name: str,
        logic: str = "",
        content: str = "",
        dialect: str = "",
) -> SQLNode:
    name = normalize_text(name)
    if not name:
        raise ValueError("SQL 名称不能为空")

    sql_uuid = build_stable_sql_uuid(workspace_id, name, content)
    node = SQLNode(
        uuid=sql_uuid,
        type=NodeType.SQL,
        name=name,
        logic=logic,
        content=content,
        dialect=dialect,
        workspace_id=workspace_id,
    )

    async def work(tx: AsyncManagedTransaction) -> None:
        try:
            await create_graph_node_tx(tx, node)
        except ConflictError:
            raise ConflictError(f"SQL 已存在: {name}") from None
        if content.strip():
            await _add_sql_uses_edges_tx(tx, workspace_id, sql_uuid, content, dialect)

    await commit_graph_then_vectors(
        work,
        lambda _: None,
        compensate_graph=lambda tx, _: _delete_sql_node_tx(tx, sql_uuid),
    )
    return node


async def get_sql(uuid: str) -> SQLNode:
    rows = await _run_read(
        f"MATCH (s:{NodeType.SQL} {{uuid: $uuid}}) RETURN s",
        uuid=uuid,
    )
    if not rows:
        raise NotFoundError(f"SQL 不存在: {uuid}")
    return _to_sql_node(dict(rows[0]["s"]))


async def list_sql_relations(sql_uuid: str) -> list[SQLRelation]:
    return await _load_sql_relations(sql_uuid)


async def list_sqls(
        workspace_id: str,
        page: int = 1,
        page_size: int = 20,
        q: str | None = None,
) -> PageResult[SQLNode]:
    page = max(page, 1)
    page_size = max(page_size, 1)
    skip = (page - 1) * page_size
    keyword = (q or "").strip()

    total_rows = await _run_read(
        f"""
        MATCH (s:{NodeType.SQL})
        WHERE s.workspace_id = $workspace_id
          AND ($q = '' OR s.name CONTAINS $q OR s.logic CONTAINS $q
          OR s.content CONTAINS $q OR s.dialect CONTAINS $q)
        RETURN count(s) AS total
        """,
        workspace_id=workspace_id,
        q=keyword,
    )
    total = int(total_rows[0]["total"]) if total_rows else 0

    rows = await _run_read(
        f"""
        MATCH (s:{NodeType.SQL})
        WHERE s.workspace_id = $workspace_id
          AND ($q = '' OR s.name CONTAINS $q OR s.logic CONTAINS $q
          OR s.content CONTAINS $q OR s.dialect CONTAINS $q)
        RETURN s
        ORDER BY s.name
        SKIP $skip LIMIT $limit
        """,
        workspace_id=workspace_id,
        q=keyword,
        skip=skip,
        limit=page_size,
    )
    items = [_to_sql_node(dict(row["s"])) for row in rows]
    return PageResult(items=items, total=total, page=page, page_size=page_size)


async def update_sql(
        workspace_id: str,
        node_uuid: str,
        name: str | None = None,
        logic: str | None = None,
        content: str | None = None,
        dialect: str | None = None,
) -> SQLNode:
    current = await get_sql(node_uuid)

    new_name = normalize_text(name) if name is not None else current.name
    new_logic = logic if logic is not None else current.logic
    new_content = content if content is not None else current.content
    new_dialect = dialect if dialect is not None else current.dialect

    if not new_name:
        raise ValueError("SQL 名称不能为空")

    new_uuid = build_stable_sql_uuid(workspace_id, new_name, new_content)
    node = SQLNode(
        uuid=new_uuid,
        type=NodeType.SQL,
        name=new_name,
        logic=new_logic,
        content=new_content,
        dialect=new_dialect,
        workspace_id=workspace_id
    )

    if new_uuid != node_uuid:
        async def work(tx: AsyncManagedTransaction) -> str:
            if not await graph_node_exists_tx(tx, NodeType.SQL, node_uuid):
                raise NotFoundError(f"SQL 不存在: {node_uuid}")
            await _delete_sql_node_tx(tx, node_uuid)
            try:
                await create_graph_node_tx(tx, node)
            except ConflictError:
                raise ConflictError(f"SQL 已存在: {new_name}") from None
            if new_content.strip():
                await _add_sql_uses_edges_tx(tx, workspace_id, new_uuid, new_content, new_dialect)
            return node_uuid

        await _run_write(work)
        return node

    async def work(tx: AsyncManagedTransaction) -> None:
        if not await graph_node_exists_tx(tx, NodeType.SQL, node_uuid):
            raise NotFoundError(f"SQL 不存在: {node_uuid}")
        await upsert_graph_node_tx(tx, node)
        await _clear_sql_uses_edges_tx(tx, node_uuid)
        if new_content.strip():
            await _add_sql_uses_edges_tx(tx, workspace_id, node_uuid, new_content, new_dialect)

    await _run_write(work)
    return node


async def delete_sql(node_uuid: str) -> None:
    async def work(tx: AsyncManagedTransaction) -> None:
        if not await graph_node_exists_tx(tx, NodeType.SQL, node_uuid):
            raise NotFoundError(f"SQL 不存在: {node_uuid}")
        await _delete_sql_node_tx(tx, node_uuid)

    await _run_write(work)
