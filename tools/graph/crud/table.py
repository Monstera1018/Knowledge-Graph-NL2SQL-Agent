from typing import Any, Optional, List, Tuple

from neo4j import AsyncManagedTransaction

from tools.common import normalize_key, normalize_text
from tools.graph.internal import create_graph_node_tx, build_stable_table_uuid, build_stable_column_uuid, \
    build_stable_enum_uuid, commit_graph_then_vectors, _run_read, graph_node_exists_tx, upsert_graph_node_tx, \
    upsert_graph_edge_tx, _run_write, _collect_incoming_describe_edges_tx, _collect_incoming_uses_edges_tx
from tools.graph.model import NodeType, TableNode, EdgeType, ConflictError, NotFoundError, PageResult, _ColumnSnapshot, \
    _JoinEdgeSnapshot, ColumnNode, EnumNode, JoinEdge, DescribeEdge, UseEdge, TableJoinRelation, TableJoinSpec
from tools.vector import delete_vector_records_by_node_uuids, upsert_vector_records
from tools.graph.crud.column import _add_column_node_tx, _sync_column_vector_records
from tools.graph.crud.enum import _add_enum_node_tx, _sync_enum_vector_records


async def _add_table_node_tx(tx: AsyncManagedTransaction, node: TableNode) -> None:
    await upsert_graph_node_tx(tx, node)


async def _get_table_row(node_uuid: str) -> Optional[dict[str, Any]]:
    rows = await _run_read(
        f"MATCH (t:{NodeType.TABLE} {{uuid: $uuid}}) RETURN t",
        uuid=node_uuid,
    )
    if not rows:
        return None
    return dict(rows[0]["t"])


def _to_table_node(data: dict[str, Any]) -> TableNode:
    return TableNode.model_validate(data)


async def _sync_table_vector_records(workspace_id: str, table_uuid: str, comment: str, description: str) -> None:
    await delete_vector_records_by_node_uuids([table_uuid])
    texts = [text for text in (comment, description) if text.strip()]
    if texts:
        await upsert_vector_records(workspace_id, texts, NodeType.TABLE, table_uuid)


async def _sync_table_subtree_vectors(workspace_id: str, table_node: TableNode, columns: list[_ColumnSnapshot]) -> None:
    await _sync_table_vector_records(workspace_id, table_node.uuid, table_node.comment, table_node.description)
    for column in columns:
        column_uuid = build_stable_column_uuid(workspace_id, table_node.name, column.name)
        await _sync_column_vector_records(workspace_id, column_uuid, column.comment)
        for enum_value in column.enums:
            enum_uuid = build_stable_enum_uuid(workspace_id, table_node.name, column.name, enum_value)
            await _sync_enum_vector_records(workspace_id, enum_uuid, enum_value)


async def _load_table_snapshot(table_uuid: str) -> Tuple[TableNode, List[_ColumnSnapshot]]:
    table_row = await _get_table_row(table_uuid)
    if table_row is None:
        raise NotFoundError(f"表不存在: {table_uuid}")

    rows = await _run_read(
        f"""
        MATCH (t:{NodeType.TABLE} {{uuid: $uuid}})-[:{EdgeType.HAS}]->(c:{NodeType.COLUMN})
        OPTIONAL MATCH (c)-[:{EdgeType.HAS}]->(e:{NodeType.ENUM})
        WITH c, collect(e.value) AS enum_values
        RETURN c, enum_values
        ORDER BY c.name
        """,
        uuid=table_uuid,
    )

    columns: List[_ColumnSnapshot] = []
    for row in rows:
        column = dict(row["c"])
        enum_values = [v for v in row["enum_values"] if v]
        columns.append(_ColumnSnapshot(
            name=column["name"],
            comment=column.get("comment", ""),
            dtype=column.get("dtype", ""),
            enums=enum_values,
        ))

    return _to_table_node(table_row), columns


def _table_subtree_uuid_map(
        workspace_id: str,
        old_table_name: str,
        new_table_name: str,
        columns: List[_ColumnSnapshot],
) -> dict[str, str]:
    mapping: dict[str, str] = {
        build_stable_table_uuid(workspace_id, old_table_name): build_stable_table_uuid(workspace_id, new_table_name),
    }
    for column in columns:
        old_column_uuid = build_stable_column_uuid(workspace_id, old_table_name, column.name)
        new_column_uuid = build_stable_column_uuid(workspace_id, new_table_name, column.name)
        mapping[old_column_uuid] = new_column_uuid
        for enum_value in column.enums:
            mapping[
                build_stable_enum_uuid(workspace_id, old_table_name, column.name, enum_value)] = build_stable_enum_uuid(
                workspace_id,
                new_table_name,
                column.name,
                enum_value,
            )
    return mapping


async def _collect_table_join_edges_tx(
        tx: AsyncManagedTransaction,
        table_uuids: List[str],
) -> List[_JoinEdgeSnapshot]:
    if not table_uuids:
        return []
    result = await tx.run(
        f"""
        MATCH (a)-[r:{EdgeType.JOINS}]->(b)
        WHERE a.uuid IN $uuids OR b.uuid IN $uuids
        RETURN a.uuid AS from_uuid, b.uuid AS to_uuid, coalesce(r.condition, '') AS condition
        """,
        uuids=table_uuids,
    )
    return [
        _JoinEdgeSnapshot(
            from_uuid=record["from_uuid"],
            to_uuid=record["to_uuid"],
            condition=record["condition"],
        )
        async for record in result
    ]


async def _collect_table_subtree_uuids_tx(
        tx: AsyncManagedTransaction,
        table_uuid: str,
) -> List[str]:
    result = await tx.run(
        f"""
        MATCH (t:{NodeType.TABLE} {{uuid: $uuid}})
        OPTIONAL MATCH (t)-[:{EdgeType.HAS}]->(c:{NodeType.COLUMN})
        OPTIONAL MATCH (c)-[:{EdgeType.HAS}]->(e:{NodeType.ENUM})
        RETURN collect(DISTINCT t.uuid) + collect(DISTINCT c.uuid) + collect(DISTINCT e.uuid) AS uuids
        """,
        uuid=table_uuid,
    )
    row = await result.single()
    if row is None:
        return []
    uuids = [u for u in row["uuids"] if u]
    return list(dict.fromkeys(uuids))


async def _delete_table_subtree_tx(tx: AsyncManagedTransaction, table_uuid: str) -> None:
    await tx.run(
        f"""
        MATCH (t:{NodeType.TABLE} {{uuid: $uuid}})
        OPTIONAL MATCH (t)-[:{EdgeType.HAS}]->(c:{NodeType.COLUMN})
        OPTIONAL MATCH (c)-[:{EdgeType.HAS}]->(e:{NodeType.ENUM})
        DETACH DELETE t, c, e
        """,
        uuid=table_uuid,
    )


async def _write_table_subtree_tx(
        tx: AsyncManagedTransaction,
        workspace_id: str,
        name: str,
        comment: str,
        description: str,
        columns: list[_ColumnSnapshot],
) -> TableNode:
    table_node = TableNode(
        uuid=build_stable_table_uuid(workspace_id, name),
        type=NodeType.TABLE,
        name=normalize_key(name),
        comment=comment,
        description=description,
        workspace_id=workspace_id
    )
    await _add_table_node_tx(tx, table_node)

    for column in columns:
        column_node = ColumnNode(
            uuid=build_stable_column_uuid(workspace_id, name, column.name),
            type=NodeType.COLUMN,
            name=normalize_key(column.name),
            comment=column.comment,
            dtype=column.dtype,
            workspace_id=workspace_id
        )
        await _add_column_node_tx(tx, table_node.uuid, column_node)

        for enum_value in column.enums:
            enum_node = EnumNode(
                uuid=build_stable_enum_uuid(workspace_id, name, column.name, enum_value),
                type=NodeType.ENUM,
                value=normalize_text(enum_value),
                workspace_id=workspace_id
            )
            await _add_enum_node_tx(tx, column_node.uuid, enum_node)

    return table_node


async def _restore_incoming_table_edges_tx(
        tx: AsyncManagedTransaction,
        describe_edges: List[tuple[str, str]],
        uses_edges: List[tuple[str, str]],
        uuid_map: dict[str, str],
        join_edges: List[_JoinEdgeSnapshot] | None = None,
) -> None:
    seen: set[tuple[str, str, str, str]] = set()
    for from_uuid, old_to_uuid in describe_edges:
        new_to_uuid = uuid_map.get(old_to_uuid)
        if new_to_uuid is None:
            continue
        key = (EdgeType.DESCRIBES.value, from_uuid, new_to_uuid, "")
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
        key = (EdgeType.USES.value, from_uuid, new_to_uuid, "")
        if key in seen:
            continue
        seen.add(key)
        await upsert_graph_edge_tx(tx, UseEdge(
            type=EdgeType.USES,
            from_uuid=from_uuid,
            to_uuid=new_to_uuid,
        ))
    for edge in join_edges or []:
        new_from_uuid = uuid_map.get(edge.from_uuid, edge.from_uuid)
        new_to_uuid = uuid_map.get(edge.to_uuid, edge.to_uuid)
        key = (EdgeType.JOINS.value, new_from_uuid, new_to_uuid, edge.condition)
        if key in seen:
            continue
        seen.add(key)
        await upsert_graph_edge_tx(tx, JoinEdge(
            type=EdgeType.JOINS,
            from_uuid=new_from_uuid,
            to_uuid=new_to_uuid,
            condition=edge.condition,
        ))


async def create_table(workspace_id: str, name: str, comment: str = "", description: str = "") -> TableNode:
    name = normalize_key(name)
    if not name:
        raise ValueError("表名不能为空")

    table_uuid = build_stable_table_uuid(workspace_id, name)
    node = TableNode(
        uuid=table_uuid,
        type=NodeType.TABLE,
        name=name,
        comment=comment,
        description=description,
        workspace_id=workspace_id
    )

    async def work(tx: AsyncManagedTransaction) -> None:
        try:
            await create_graph_node_tx(tx, node)
        except ConflictError:
            raise ConflictError(f"表已存在: {name}") from None

    await commit_graph_then_vectors(
        work,
        lambda _: _sync_table_vector_records(workspace_id, table_uuid, comment, description),
        compensate_graph=lambda tx, _: _delete_table_subtree_tx(tx, table_uuid),
    )
    return node


async def get_table(node_uuid: str) -> TableNode:
    row = await _get_table_row(node_uuid)
    if row is None:
        raise NotFoundError(f"表不存在: {node_uuid}")
    return _to_table_node(row)


async def list_tables(
        workspace_id: str,
        page: int = 1,
        page_size: int = 20,
        q: str | None = None,
) -> PageResult[TableNode]:
    page = max(page, 1)
    page_size = max(page_size, 1)
    skip = (page - 1) * page_size
    keyword = (q or "").strip()

    total_rows = await _run_read(
        f"""
        MATCH (t:{NodeType.TABLE})
        WHERE t.workspace_id = $workspace_id
          AND ($q = '' OR t.name CONTAINS $q OR t.comment CONTAINS $q OR t.description CONTAINS $q)
        RETURN count(t) AS total
        """,
        workspace_id=workspace_id,
        q=keyword,
    )
    total = int(total_rows[0]["total"]) if total_rows else 0

    rows = await _run_read(
        f"""
        MATCH (t:{NodeType.TABLE})
        WHERE t.workspace_id = $workspace_id
          AND ($q = '' OR t.name CONTAINS $q OR t.comment CONTAINS $q OR t.description CONTAINS $q)
        RETURN t
        ORDER BY t.name
        SKIP $skip LIMIT $limit
        """,
        workspace_id=workspace_id,
        q=keyword,
        skip=skip,
        limit=page_size,
    )
    items = [_to_table_node(dict(row["t"])) for row in rows]
    return PageResult(items=items, total=total, page=page, page_size=page_size)


async def list_table_join_relations(workspace_id: str, table_uuid: str) -> list[TableJoinRelation]:
    rows = await _run_read(
        f"""
        MATCH (from:{NodeType.TABLE})-[r:{EdgeType.JOINS}]->(to:{NodeType.TABLE})
        WHERE (from.uuid = $table_uuid OR to.uuid = $table_uuid)
          AND from.workspace_id = $workspace_id
          AND to.workspace_id = $workspace_id
        RETURN from.uuid AS from_table_uuid, from.name AS from_table_name,
               to.uuid AS to_table_uuid, to.name AS to_table_name,
               coalesce(r.condition, '') AS condition
        ORDER BY from.name, to.name
        """,
        workspace_id=workspace_id,
        table_uuid=table_uuid,
    )
    return [
        TableJoinRelation(
            from_table_uuid=row["from_table_uuid"],
            from_table_name=row["from_table_name"],
            to_table_uuid=row["to_table_uuid"],
            to_table_name=row["to_table_name"],
            condition=row["condition"],
        )
        for row in rows
    ]


async def list_all_table_join_relations(
        workspace_id: str,
        page: int = 1,
        page_size: int = 20,
        q: str | None = None,
) -> PageResult[TableJoinRelation]:
    page = max(page, 1)
    page_size = max(page_size, 1)
    skip = (page - 1) * page_size
    keyword = (q or "").strip()

    where_clause = f"""
        from.workspace_id = $workspace_id
        AND to.workspace_id = $workspace_id
        AND (
            $q = ''
            OR from.name CONTAINS $q
            OR to.name CONTAINS $q
            OR coalesce(from.comment, '') CONTAINS $q
            OR coalesce(to.comment, '') CONTAINS $q
            OR coalesce(r.condition, '') CONTAINS $q
        )
    """

    total_rows = await _run_read(
        f"""
        MATCH (from:{NodeType.TABLE})-[r:{EdgeType.JOINS}]->(to:{NodeType.TABLE})
        WHERE {where_clause}
        RETURN count(r) AS total
        """,
        workspace_id=workspace_id,
        q=keyword,
    )
    total = int(total_rows[0]["total"]) if total_rows else 0

    rows = await _run_read(
        f"""
        MATCH (from:{NodeType.TABLE})-[r:{EdgeType.JOINS}]->(to:{NodeType.TABLE})
        WHERE {where_clause}
        RETURN from.uuid AS from_table_uuid, from.name AS from_table_name,
               to.uuid AS to_table_uuid, to.name AS to_table_name,
               coalesce(r.condition, '') AS condition
        ORDER BY from.name, to.name, condition
        SKIP $skip LIMIT $limit
        """,
        workspace_id=workspace_id,
        q=keyword,
        skip=skip,
        limit=page_size,
    )
    items = [
        TableJoinRelation(
            from_table_uuid=row["from_table_uuid"],
            from_table_name=row["from_table_name"],
            to_table_uuid=row["to_table_uuid"],
            to_table_name=row["to_table_name"],
            condition=row["condition"],
        )
        for row in rows
    ]
    return PageResult(items=items, total=total, page=page, page_size=page_size)


async def replace_table_join_relations(
        workspace_id: str,
        table_uuid: str,
        relations: list[TableJoinSpec],
) -> list[TableJoinRelation]:
    normalized = [
        TableJoinSpec(
            from_table_name=normalize_key(relation.from_table_name),
            to_table_name=normalize_key(relation.to_table_name),
            condition=normalize_text(relation.condition),
        )
        for relation in relations
        if normalize_key(relation.from_table_name) and normalize_key(relation.to_table_name)
    ]

    async def work(tx: AsyncManagedTransaction) -> None:
        if not await graph_node_exists_tx(tx, NodeType.TABLE, table_uuid):
            raise NotFoundError(f"表不存在: {table_uuid}")
        await tx.run(
            f"""
            MATCH (from:{NodeType.TABLE})-[r:{EdgeType.JOINS}]->(to:{NodeType.TABLE})
            WHERE (from.uuid = $table_uuid OR to.uuid = $table_uuid)
              AND from.workspace_id = $workspace_id
              AND to.workspace_id = $workspace_id
            DELETE r
            """,
            workspace_id=workspace_id,
            table_uuid=table_uuid,
        )
        for relation in normalized:
            await upsert_graph_edge_tx(tx, JoinEdge(
                type=EdgeType.JOINS,
                from_uuid=build_stable_table_uuid(workspace_id, relation.from_table_name),
                to_uuid=build_stable_table_uuid(workspace_id, relation.to_table_name),
                condition=relation.condition,
            ))

    await _run_write(work)
    return await list_table_join_relations(workspace_id, table_uuid)


async def update_table(
        workspace_id: str,
        node_uuid: str,
        name: str | None = None,
        comment: str | None = None,
        description: str | None = None,
) -> TableNode:
    table_node, columns = await _load_table_snapshot(node_uuid)

    new_name = normalize_key(name) if name is not None else table_node.name
    new_comment = normalize_text(comment) if comment is not None else table_node.comment
    new_description = normalize_text(description) if description is not None else table_node.description

    if not new_name:
        raise ValueError("表名不能为空")

    if new_name != table_node.name:
        uuid_map = _table_subtree_uuid_map(workspace_id, table_node.name, new_name, columns)
        new_table_uuid = build_stable_table_uuid(workspace_id, new_name)
        uuid_map[node_uuid] = new_table_uuid

        async def work(tx: AsyncManagedTransaction) -> tuple[list[str], TableNode]:
            if not await graph_node_exists_tx(tx, NodeType.TABLE, node_uuid):
                raise NotFoundError(f"表不存在: {node_uuid}")
            describe_edges = await _collect_incoming_describe_edges_tx(tx, list(uuid_map.keys()))
            uses_edges = await _collect_incoming_uses_edges_tx(tx, list(uuid_map.keys()))
            join_edges = await _collect_table_join_edges_tx(tx, [node_uuid])
            _old_uuids = await _collect_table_subtree_uuids_tx(tx, node_uuid)
            await _delete_table_subtree_tx(tx, node_uuid)
            _new_table_node = await _write_table_subtree_tx(
                tx,
                name=new_name,
                comment=new_comment,
                description=new_description,
                columns=columns,
                workspace_id=workspace_id
            )
            await _restore_incoming_table_edges_tx(
                tx,
                describe_edges,
                uses_edges,
                uuid_map,
                join_edges,
            )
            return _old_uuids, _new_table_node

        async def sync_vectors(result: Tuple[List[str], TableNode]) -> None:
            _old_uuids, _new_table_node = result
            await delete_vector_records_by_node_uuids(_old_uuids)
            await _sync_table_subtree_vectors(workspace_id, _new_table_node, columns)

        old_uuids, new_table_node = await commit_graph_then_vectors(work, sync_vectors)
        return new_table_node

    table_node = TableNode(
        uuid=node_uuid,
        type=NodeType.TABLE,
        name=new_name,
        comment=new_comment,
        description=new_description,
        workspace_id=workspace_id
    )

    async def work(tx: AsyncManagedTransaction) -> None:
        if not await graph_node_exists_tx(tx, NodeType.TABLE, node_uuid):
            raise NotFoundError(f"表不存在: {node_uuid}")
        await _add_table_node_tx(tx, table_node)

    await commit_graph_then_vectors(
        work,
        lambda _: _sync_table_vector_records(workspace_id, node_uuid, new_comment, new_description),
    )
    return table_node


async def delete_table(table_uuid: str) -> None:
    async def work(tx: AsyncManagedTransaction) -> list[str]:
        if not await graph_node_exists_tx(tx, NodeType.TABLE, table_uuid):
            raise NotFoundError(f"表不存在: {table_uuid}")
        _uuids = await _collect_table_subtree_uuids_tx(tx, table_uuid)
        await _delete_table_subtree_tx(tx, table_uuid)
        return _uuids

    uuids = await _run_write(work)
    await delete_vector_records_by_node_uuids(uuids)
