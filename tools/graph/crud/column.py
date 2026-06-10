from typing import Any, Optional

from neo4j import AsyncManagedTransaction

from tools.common import normalize_key, normalize_text
from tools.graph.internal import create_graph_node_tx, build_stable_column_uuid, \
    build_stable_enum_uuid, commit_graph_then_vectors, _run_read, graph_node_exists_tx, upsert_graph_node_tx, \
    upsert_graph_edge_tx, _collect_incoming_describe_edges_tx, _collect_incoming_uses_edges_tx, _run_write
from tools.graph.model import NodeType, EdgeType, ConflictError, NotFoundError, PageResult, \
    ColumnNode, HasEdge, EnumNode, DescribeEdge, UseEdge
from tools.vector import delete_vector_records_by_node_uuids, upsert_vector_records
from tools.graph.crud.enum import _add_enum_node_tx, _sync_enum_vector_records


async def _get_table_row(node_uuid: str) -> Optional[dict[str, Any]]:
    rows = await _run_read(
        f"MATCH (t:{NodeType.TABLE} {{uuid: $uuid}}) RETURN t",
        uuid=node_uuid,
    )
    if not rows:
        return None
    return dict(rows[0]["t"])


async def _get_column_row(node_uuid: str) -> Optional[dict[str, Any]]:
    rows = await _run_read(
        f"MATCH (c:{NodeType.COLUMN} {{uuid: $uuid}}) RETURN c",
        uuid=node_uuid,
    )
    if not rows:
        return None
    return dict(rows[0]["c"])


def _to_column_node(data: dict[str, Any]) -> ColumnNode:
    return ColumnNode.model_validate(data)


async def _add_column_node_tx(
        tx: AsyncManagedTransaction,
        table_uuid: str,
        node: ColumnNode,
) -> None:
    await upsert_graph_node_tx(tx, node)
    await upsert_graph_edge_tx(tx, HasEdge(
        type=EdgeType.HAS,
        from_uuid=table_uuid,
        to_uuid=node.uuid,
    ))


async def _sync_column_vector_records(workspace_id: str, column_uuid: str, comment: str) -> None:
    await delete_vector_records_by_node_uuids([column_uuid])
    if comment.strip():
        await upsert_vector_records(workspace_id, [comment], NodeType.COLUMN, column_uuid)


async def _sync_column_subtree_vectors(
        workspace_id: str,
        table_name: str,
        column: ColumnNode,
        enum_values: list[str],
) -> None:
    await _sync_column_vector_records(workspace_id, column.uuid, column.comment)
    for enum_value in enum_values:
        enum_uuid = build_stable_enum_uuid(workspace_id, table_name, column.name, enum_value)
        await _sync_enum_vector_records(workspace_id, enum_uuid, enum_value)


async def _delete_column_subtree_tx(tx: AsyncManagedTransaction, column_uuid: str) -> None:
    await tx.run(
        f"""
        MATCH (c:{NodeType.COLUMN} {{uuid: $uuid}})
        OPTIONAL MATCH (c)-[:{EdgeType.HAS}]->(e:{NodeType.ENUM})
        DETACH DELETE c, e
        """,
        uuid=column_uuid,
    )


async def _get_column_context(column_uuid: str) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = await _run_read(
        f"""
        MATCH (t:{NodeType.TABLE})-[:{EdgeType.HAS}]->(c:{NodeType.COLUMN} {{uuid: $uuid}})
        RETURN t, c
        """,
        uuid=column_uuid,
    )
    if not rows:
        raise NotFoundError(f"列不存在: {column_uuid}")
    return dict(rows[0]["t"]), dict(rows[0]["c"])


def _column_subtree_uuid_map(
        workspace_id: str,
        table_name: str,
        old_column_name: str,
        new_column_name: str,
        enum_values: list[str],
) -> dict[str, str]:
    mapping: dict[str, str] = {
        build_stable_column_uuid(workspace_id, table_name, old_column_name): build_stable_column_uuid(workspace_id,
                                                                                                      table_name,
                                                                                                      new_column_name),
    }
    for enum_value in enum_values:
        mapping[build_stable_enum_uuid(workspace_id, table_name, old_column_name, enum_value)] = build_stable_enum_uuid(
            workspace_id,
            table_name,
            new_column_name,
            enum_value,
        )
    return mapping


async def _collect_column_subtree_uuids_tx(
        tx: AsyncManagedTransaction,
        column_uuid: str,
) -> list[str]:
    result = await tx.run(
        f"""
        MATCH (c:{NodeType.COLUMN} {{uuid: $uuid}})
        OPTIONAL MATCH (c)-[:{EdgeType.HAS}]->(e:{NodeType.ENUM})
        RETURN collect(DISTINCT c.uuid) + collect(DISTINCT e.uuid) AS uuids
        """,
        uuid=column_uuid,
    )
    row = await result.single()
    if row is None:
        return []
    uuids = [u for u in row["uuids"] if u]
    return list(dict.fromkeys(uuids))


async def _write_column_subtree_tx(
        tx: AsyncManagedTransaction,
        workspace_id: str,
        table_uuid: str,
        table_name: str,
        column_name: str,
        comment: str,
        dtype: str,
        enum_values: list[str],
) -> ColumnNode:
    column_node = ColumnNode(
        uuid=build_stable_column_uuid(workspace_id, table_name, column_name),
        type=NodeType.COLUMN,
        name=normalize_key(column_name),
        comment=comment,
        dtype=dtype,
        workspace_id=workspace_id,
    )
    await _add_column_node_tx(tx, table_uuid, column_node)

    for enum_value in enum_values:
        enum_node = EnumNode(
            uuid=build_stable_enum_uuid(workspace_id, table_name, column_name, enum_value),
            type=NodeType.ENUM,
            value=normalize_text(enum_value),
            workspace_id=workspace_id,
        )
        await _add_enum_node_tx(tx, column_node.uuid, enum_node)

    return column_node


async def _restore_incoming_column_edges_tx(
        tx: AsyncManagedTransaction,
        describe_edges: list[tuple[str, str]],
        uses_edges: list[tuple[str, str]],
        uuid_map: dict[str, str]
) -> None:
    seen: set[tuple[str, str, str]] = set()
    for from_uuid, old_to_uuid in describe_edges:
        new_to_uuid = uuid_map.get(old_to_uuid)
        if new_to_uuid is None:
            continue
        key = (EdgeType.DESCRIBES.value, from_uuid, new_to_uuid)
        if key in seen:
            continue
        seen.add(key)
        await upsert_graph_edge_tx(tx, DescribeEdge(
            type=EdgeType.DESCRIBES,
            from_uuid=from_uuid,
            to_uuid=new_to_uuid,
        ))
    for from_uuid, old_to_uuid in uses_edges:
        new_to_uuid = uuid_map.get(old_to_uuid)
        if new_to_uuid is None:
            continue
        key = (EdgeType.USES.value, from_uuid, new_to_uuid)
        if key in seen:
            continue
        seen.add(key)
        await upsert_graph_edge_tx(tx, UseEdge(
            type=EdgeType.USES,
            from_uuid=from_uuid,
            to_uuid=new_to_uuid,
        ))


async def create_column(
        workspace_id: str,
        table_uuid: str,
        name: str,
        comment: str = "",
        dtype: str = "",
) -> ColumnNode:
    table_row = await _get_table_row(table_uuid)
    if table_row is None:
        raise NotFoundError(f"表不存在: {table_uuid}")

    name = normalize_key(name)
    if not name:
        raise ValueError("列名不能为空")

    table_name = table_row["name"]
    col_uuid = build_stable_column_uuid(workspace_id, table_name, name)
    node = ColumnNode(
        uuid=col_uuid,
        type=NodeType.COLUMN,
        name=name,
        comment=comment,
        dtype=dtype,
        workspace_id=workspace_id
    )

    async def work(tx: AsyncManagedTransaction) -> None:
        if not await graph_node_exists_tx(tx, NodeType.TABLE, table_uuid):
            raise NotFoundError(f"表不存在: {table_uuid}")
        try:
            await create_graph_node_tx(tx, node)
        except ConflictError:
            raise ConflictError(f"列已存在: {table_name}.{name}") from None
        await upsert_graph_edge_tx(tx, HasEdge(
            type=EdgeType.HAS,
            from_uuid=table_uuid,
            to_uuid=node.uuid,
        ))

    await commit_graph_then_vectors(
        work,
        lambda _: _sync_column_vector_records(workspace_id, col_uuid, comment),
        compensate_graph=lambda tx, _: _delete_column_subtree_tx(tx, col_uuid),
    )
    return node


async def get_column(uuid: str) -> ColumnNode:
    row = await _get_column_row(uuid)
    if row is None:
        raise NotFoundError(f"列不存在: {uuid}")
    return _to_column_node(row)


async def list_columns(
        workspace_id: str,
        table_uuid: str | None = None,
        page: int = 1,
        page_size: int = 20,
        q: str | None = None,
) -> PageResult[ColumnNode]:
    page = max(page, 1)
    page_size = max(page_size, 1)
    skip = (page - 1) * page_size
    keyword = (q or "").strip()

    total_rows = await _run_read(
        f"""
        MATCH (t:{NodeType.TABLE})-[:{EdgeType.HAS}]->(c:{NodeType.COLUMN})
        WHERE t.workspace_id = $workspace_id
          AND c.workspace_id = $workspace_id
          AND ($table_uuid = '' OR t.uuid = $table_uuid)
          AND ($q = '' OR c.name CONTAINS $q OR c.comment CONTAINS $q OR c.dtype CONTAINS $q)
        RETURN count(c) AS total
        """,
        workspace_id=workspace_id,
        table_uuid=table_uuid or "",
        q=keyword,
    )
    total = int(total_rows[0]["total"]) if total_rows else 0

    rows = await _run_read(
        f"""
        MATCH (t:{NodeType.TABLE})-[:{EdgeType.HAS}]->(c:{NodeType.COLUMN})
        WHERE t.workspace_id = $workspace_id
          AND c.workspace_id = $workspace_id
          AND ($table_uuid = '' OR t.uuid = $table_uuid)
          AND ($q = '' OR c.name CONTAINS $q OR c.comment CONTAINS $q OR c.dtype CONTAINS $q)
        RETURN c
        ORDER BY c.name
        SKIP $skip LIMIT $limit
        """,
        workspace_id=workspace_id,
        table_uuid=table_uuid or "",
        q=keyword,
        skip=skip,
        limit=page_size,
    )
    items = [_to_column_node(dict(row["c"])) for row in rows]
    return PageResult(items=items, total=total, page=page, page_size=page_size)


async def update_column(
        workspace_id: str,
        column_uuid: str,
        name: str | None = None,
        comment: str | None = None,
        dtype: str | None = None,
) -> ColumnNode:
    table_row, column_row = await _get_column_context(column_uuid)
    table_name = table_row["name"]
    table_uuid = table_row["uuid"]

    new_name = normalize_key(name) if name is not None else column_row["name"]
    new_comment = normalize_text(comment) if comment is not None else column_row.get("comment", "")
    new_dtype = dtype if dtype is not None else column_row.get("dtype", "")

    if not new_name:
        raise ValueError("列名不能为空")

    if new_name != column_row["name"]:
        enum_rows = await _run_read(
            f"""
            MATCH (c:{NodeType.COLUMN} {{uuid: $uuid}})-[:{EdgeType.HAS}]->(e:{NodeType.ENUM})
            RETURN e.value AS value
            ORDER BY e.value
            """,
            uuid=column_uuid,
        )
        enum_values = [row["value"] for row in enum_rows]

        old_column_name = column_row["name"]
        uuid_map = _column_subtree_uuid_map(workspace_id, table_name, old_column_name, new_name, enum_values)

        async def work(tx: AsyncManagedTransaction) -> tuple[list[str], ColumnNode]:
            if not await graph_node_exists_tx(tx, NodeType.COLUMN, column_uuid):
                raise NotFoundError(f"列不存在: {column_uuid}")
            describe_edges = await _collect_incoming_describe_edges_tx(tx, list(uuid_map.keys()))
            uses_edges = await _collect_incoming_uses_edges_tx(tx, list(uuid_map.keys()))
            _old_uuids = await _collect_column_subtree_uuids_tx(tx, column_uuid)
            await _delete_column_subtree_tx(tx, column_uuid)
            _new_column_node = await _write_column_subtree_tx(
                tx,
                table_uuid=table_uuid,
                table_name=table_name,
                column_name=new_name,
                comment=new_comment,
                dtype=new_dtype,
                enum_values=enum_values,
                workspace_id=workspace_id
            )
            await _restore_incoming_column_edges_tx(tx, describe_edges, uses_edges, uuid_map)
            return _old_uuids, _new_column_node

        async def sync_vectors(result: tuple[list[str], ColumnNode]) -> None:
            _old_uuids, _new_column_node = result
            await delete_vector_records_by_node_uuids(_old_uuids)
            await _sync_column_subtree_vectors(workspace_id, table_name, _new_column_node, enum_values)

        old_uuids, new_column_node = await commit_graph_then_vectors(work, sync_vectors)
        return new_column_node

    column_node = ColumnNode(
        uuid=column_uuid,
        type=NodeType.COLUMN,
        name=new_name,
        comment=new_comment,
        dtype=new_dtype,
        workspace_id=workspace_id,
    )

    async def work(tx: AsyncManagedTransaction) -> None:
        if not await graph_node_exists_tx(tx, NodeType.COLUMN, column_uuid):
            raise NotFoundError(f"列不存在: {column_uuid}")
        await upsert_graph_node_tx(tx, column_node)

    await commit_graph_then_vectors(
        work,
        lambda _: _sync_column_vector_records(workspace_id, column_uuid, new_comment),
    )
    return column_node


async def delete_column(uuid: str) -> None:
    async def work(tx: AsyncManagedTransaction) -> list[str]:
        if not await graph_node_exists_tx(tx, NodeType.COLUMN, uuid):
            raise NotFoundError(f"列不存在: {uuid}")
        _uuids = await _collect_column_subtree_uuids_tx(tx, uuid)
        await _delete_column_subtree_tx(tx, uuid)
        return _uuids

    uuids = await _run_write(work)
    await delete_vector_records_by_node_uuids(uuids)
