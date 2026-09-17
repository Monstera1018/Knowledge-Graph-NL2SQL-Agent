from typing import Any, Optional, List, Tuple

from neo4j import AsyncManagedTransaction

from tools.common import normalize_text
from tools.graph.internal import create_graph_node_tx, \
    build_stable_enum_uuid, commit_graph_then_vectors, _run_read, graph_node_exists_tx, upsert_graph_node_tx, \
    upsert_graph_edge_tx, _run_write
from tools.graph.model import NodeType, EdgeType, ConflictError, NotFoundError, PageResult, \
    HasEdge, EnumListItem, EnumNode
from tools.vector import delete_vector_records_by_node_uuids, upsert_vector_records


async def _add_enum_node_tx(
        tx: AsyncManagedTransaction,
        column_uuid: str,
        node: EnumNode,
) -> None:
    await upsert_graph_node_tx(tx, node)
    await upsert_graph_edge_tx(tx, HasEdge(
        type=EdgeType.HAS,
        from_uuid=column_uuid,
        to_uuid=node.uuid,
    ))


async def _delete_enum_subtree_tx(tx: AsyncManagedTransaction, enum_uuid: str) -> None:
    await tx.run(
        f"MATCH (e:{NodeType.ENUM} {{uuid: $uuid}}) DETACH DELETE e",
        uuid=enum_uuid,
    )


async def _sync_enum_vector_records(workspace_id: str, enum_uuid: str, value: str) -> None:
    await delete_vector_records_by_node_uuids([enum_uuid])
    if value.strip():
        await upsert_vector_records(workspace_id, [value], NodeType.ENUM, enum_uuid)


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


def _to_enum_node(data: dict[str, Any]) -> EnumNode:
    return EnumNode.model_validate(data)


async def create_enum(workspace_id: str, column_uuid: str, value: str) -> EnumNode:
    table_row, column_row = await _get_column_context(column_uuid)
    value = normalize_text(value)
    if not value:
        raise ValueError("枚举值不能为空")

    table_name = table_row["name"]
    column_name = column_row["name"]
    enum_uuid = build_stable_enum_uuid(workspace_id, table_name, column_name, value)
    node = EnumNode(
        uuid=enum_uuid,
        type=NodeType.ENUM,
        value=value,
        workspace_id=workspace_id,
    )

    async def work(tx: AsyncManagedTransaction) -> None:
        if not await graph_node_exists_tx(tx, NodeType.COLUMN, column_uuid):
            raise NotFoundError(f"列不存在: {column_uuid}")
        try:
            await create_graph_node_tx(tx, node)
        except ConflictError:
            raise ConflictError(f"枚举值已存在: {value}") from None
        await upsert_graph_edge_tx(tx, HasEdge(
            type=EdgeType.HAS,
            from_uuid=column_uuid,
            to_uuid=node.uuid,
        ))

    await commit_graph_then_vectors(
        work,
        lambda _: _sync_enum_vector_records(workspace_id, enum_uuid, value),
        compensate_graph=lambda tx, _: _delete_enum_subtree_tx(tx, enum_uuid),
    )
    return node


async def get_enum(uuid: str) -> EnumNode:
    rows = await _run_read(
        f"MATCH (e:{NodeType.ENUM} {{uuid: $uuid}}) RETURN e",
        uuid=uuid,
    )
    if not rows:
        raise NotFoundError(f"枚举不存在: {uuid}")
    return _to_enum_node(dict(rows[0]["e"]))


async def list_enums(
        workspace_id: str,
        column_uuid: str | None = None,
        page: int = 1,
        page_size: int = 20,
        q: str | None = None,
) -> PageResult[EnumNode]:
    page = max(page, 1)
    page_size = max(page_size, 1)
    skip = (page - 1) * page_size
    keyword = (q or "").strip()

    total_rows = await _run_read(
        f"""
        MATCH (c:{NodeType.COLUMN})-[:{EdgeType.HAS}]->(e:{NodeType.ENUM})
        WHERE c.workspace_id = $workspace_id
          AND e.workspace_id = $workspace_id
          AND ($column_uuid = '' OR c.uuid = $column_uuid)
          AND ($q = '' OR e.value CONTAINS $q)
        RETURN count(e) AS total
        """,
        workspace_id=workspace_id,
        column_uuid=column_uuid or "",
        q=keyword,
    )
    total = int(total_rows[0]["total"]) if total_rows else 0

    rows = await _run_read(
        f"""
        MATCH (c:{NodeType.COLUMN})-[:{EdgeType.HAS}]->(e:{NodeType.ENUM})
        WHERE c.workspace_id = $workspace_id
          AND e.workspace_id = $workspace_id
          AND ($column_uuid = '' OR c.uuid = $column_uuid)
          AND ($q = '' OR e.value CONTAINS $q)
        RETURN e
        ORDER BY e.value
        SKIP $skip LIMIT $limit
        """,
        workspace_id=workspace_id,
        column_uuid=column_uuid or "",
        q=keyword,
        skip=skip,
        limit=page_size,
    )
    items = [_to_enum_node(dict(row["e"])) for row in rows]
    return PageResult(items=items, total=total, page=page, page_size=page_size)


async def list_enum_items(
        workspace_id: str,
        column_uuid: str | None = None,
        page: int = 1,
        page_size: int = 20,
        q: str | None = None,
) -> PageResult[EnumListItem]:
    page = max(page, 1)
    page_size = max(page_size, 1)
    skip = (page - 1) * page_size
    keyword = (q or "").strip()

    where_clause = f"""
        t.workspace_id = $workspace_id
        AND c.workspace_id = $workspace_id
        AND e.workspace_id = $workspace_id
        AND ($column_uuid = '' OR c.uuid = $column_uuid)
        AND (
            $q = ''
            OR e.value CONTAINS $q
            OR c.name CONTAINS $q
            OR coalesce(c.comment, '') CONTAINS $q
            OR t.name CONTAINS $q
            OR coalesce(t.comment, '') CONTAINS $q
        )
    """

    total_rows = await _run_read(
        f"""
        MATCH (t:{NodeType.TABLE})-[:{EdgeType.HAS}]->(c:{NodeType.COLUMN})-[:{EdgeType.HAS}]->(e:{NodeType.ENUM})
        WHERE {where_clause}
        RETURN count(e) AS total
        """,
        workspace_id=workspace_id,
        column_uuid=column_uuid or "",
        q=keyword,
    )
    total = int(total_rows[0]["total"]) if total_rows else 0

    rows = await _run_read(
        f"""
        MATCH (t:{NodeType.TABLE})-[:{EdgeType.HAS}]->(c:{NodeType.COLUMN})-[:{EdgeType.HAS}]->(e:{NodeType.ENUM})
        WHERE {where_clause}
        RETURN e, c, t
        ORDER BY t.name, c.name, e.value
        SKIP $skip LIMIT $limit
        """,
        workspace_id=workspace_id,
        column_uuid=column_uuid or "",
        q=keyword,
        skip=skip,
        limit=page_size,
    )
    items: list[EnumListItem] = []
    for row in rows:
        enum = dict(row["e"])
        column = dict(row["c"])
        table = dict(row["t"])
        items.append(EnumListItem(
            uuid=enum["uuid"],
            workspace_id=enum.get("workspace_id", workspace_id),
            type=enum.get("type", NodeType.ENUM),
            value=enum.get("value", ""),
            table_uuid=table.get("uuid", ""),
            table_name=table.get("name", ""),
            table_comment=table.get("comment", ""),
            column_uuid=column.get("uuid", ""),
            column_name=column.get("name", ""),
            column_comment=column.get("comment", ""),
            column_dtype=column.get("dtype", ""),
        ))
    return PageResult(items=items, total=total, page=page, page_size=page_size)


async def update_enum(workspace_id: str, uuid: str, value: str) -> EnumNode:
    rows = await _run_read(
        f"""
        MATCH (t:{NodeType.TABLE})-[:{EdgeType.HAS}]->(c:{NodeType.COLUMN})-[:{EdgeType.HAS}]->(e:{NodeType.ENUM} {{uuid: $uuid}})
        RETURN t, c, e
        """,
        uuid=uuid,
    )
    if not rows:
        raise NotFoundError(f"枚举不存在: {uuid}")

    table_row = dict(rows[0]["t"])
    column_row = dict(rows[0]["c"])
    enum_row = dict(rows[0]["e"])
    column_uuid = column_row["uuid"]

    new_value = normalize_text(value)
    if not new_value:
        raise ValueError("枚举值不能为空")

    if new_value == enum_row["value"]:
        return _to_enum_node(enum_row)

    node = EnumNode(
        uuid=build_stable_enum_uuid(workspace_id, table_row["name"], column_row["name"], new_value),
        type=NodeType.ENUM,
        value=new_value,
        workspace_id=workspace_id,
    )

    async def work(tx: AsyncManagedTransaction) -> str:
        if not await graph_node_exists_tx(tx, NodeType.ENUM, uuid):
            raise NotFoundError(f"枚举不存在: {uuid}")
        await _delete_enum_subtree_tx(tx, uuid)
        try:
            await create_graph_node_tx(tx, node)
        except ConflictError:
            raise ConflictError(f"枚举值已存在: {new_value}") from None
        await upsert_graph_edge_tx(tx, HasEdge(
            type=EdgeType.HAS,
            from_uuid=column_uuid,
            to_uuid=node.uuid,
        ))
        return uuid

    async def sync_vectors(old_uuid: str) -> None:
        await delete_vector_records_by_node_uuids([old_uuid])
        await _sync_enum_vector_records(workspace_id, node.uuid, new_value)

    _ = await commit_graph_then_vectors(work, sync_vectors)
    return node


async def delete_enum(uuid: str) -> None:
    async def work(tx: AsyncManagedTransaction) -> None:
        if not await graph_node_exists_tx(tx, NodeType.ENUM, uuid):
            raise NotFoundError(f"枚举不存在: {uuid}")
        await _delete_enum_subtree_tx(tx, uuid)

    await _run_write(work)
    await delete_vector_records_by_node_uuids([uuid])
