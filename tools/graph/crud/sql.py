import logging
from typing import Any, List

from neo4j import AsyncManagedTransaction

from tools.common import normalize_text, utc_now_iso
from tools.graph.internal import create_graph_node_tx, build_stable_table_uuid, build_stable_column_uuid, \
    commit_graph_then_vectors, _run_read, graph_node_exists_tx, upsert_graph_node_tx, \
    upsert_graph_edge_tx, _run_write, \
    build_stable_sql_uuid
from tools.graph.model import NodeType, EdgeType, ConflictError, NotFoundError, PageResult, \
    SQLNode, SQLSource, UseEdge, SQLRelation, normalize_sql_enabled, normalize_sql_source
from tools.sql import find_all_sources
from tools.sql_similarity import find_best_similar_sql, merge_sql_logic
from tools.vector import delete_vector_records_by_node_uuids, upsert_vector_records

logger = logging.getLogger(__name__)

_SQL_SORT_TIME_DESC = "time_desc"
_SQL_SORT_TIME_ASC = "time_asc"
_SQL_SORT_NAME = "name"
_SQL_TIME_SORT_FALLBACK = "1970-01-01T00:00:00Z"


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
    payload = dict(data or {})
    payload["source"] = normalize_sql_source(payload.get("source"))
    payload["enabled"] = normalize_sql_enabled(payload.get("enabled"))
    payload["created_at"] = str(payload.get("created_at") or "").strip()
    payload["updated_at"] = str(payload.get("updated_at") or "").strip()
    return SQLNode.model_validate(payload)


def _build_sql_node(
        workspace_id: str,
        *,
        name: str,
        logic: str,
        content: str,
        dialect: str,
        source: SQLSource,
        enabled: bool,
        created_at: str = "",
        updated_at: str = "",
        uuid: str | None = None,
) -> SQLNode:
    now = utc_now_iso()
    created = (created_at or "").strip() or now
    updated = (updated_at or "").strip() or now
    return SQLNode(
        uuid=uuid or build_stable_sql_uuid(workspace_id, name, content),
        type=NodeType.SQL,
        name=name,
        logic=logic,
        content=content,
        dialect=dialect,
        workspace_id=workspace_id,
        source=source,
        enabled=enabled,
        created_at=created,
        updated_at=updated,
    )


def _sql_list_order_clause(sort: str) -> str:
    if sort == _SQL_SORT_NAME:
        return "s.name ASC"
    if sort == _SQL_SORT_TIME_ASC:
        return f"coalesce(s.updated_at, s.created_at, '{_SQL_TIME_SORT_FALLBACK}') ASC, s.name ASC"
    return f"coalesce(s.updated_at, s.created_at, '{_SQL_TIME_SORT_FALLBACK}') DESC, s.name ASC"


def _normalize_sql_sort(sort: str | None) -> str:
    value = (sort or "").strip().lower()
    if value in {_SQL_SORT_TIME_ASC, _SQL_SORT_NAME, _SQL_SORT_TIME_DESC}:
        return value
    return _SQL_SORT_TIME_DESC


async def _sync_sql_vector_records(
        workspace_id: str,
        sql_uuid: str,
        name: str,
        logic: str,
        content: str,
        enabled: bool,
) -> None:
    await delete_vector_records_by_node_uuids([sql_uuid])
    if not enabled:
        return
    texts = [text for text in (name, logic, content) if str(text).strip()]
    if texts:
        await upsert_vector_records(workspace_id, texts, NodeType.SQL, sql_uuid)


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
        source: SQLSource | str = SQLSource.IMPORT,
        enabled: bool = True,
) -> SQLNode:
    name = normalize_text(name)
    if not name:
        raise ValueError("SQL 名称不能为空")

    node = _build_sql_node(
        workspace_id,
        name=name,
        logic=logic,
        content=content,
        dialect=dialect,
        source=normalize_sql_source(source),
        enabled=normalize_sql_enabled(enabled),
    )
    sql_uuid = node.uuid

    async def work(tx: AsyncManagedTransaction) -> None:
        try:
            await create_graph_node_tx(tx, node)
        except ConflictError:
            raise ConflictError(f"SQL 已存在: {name}") from None
        if content.strip():
            await _add_sql_uses_edges_tx(tx, workspace_id, sql_uuid, content, dialect)

    await commit_graph_then_vectors(
        work,
        lambda _: _sync_sql_vector_records(
            workspace_id, sql_uuid, node.name, node.logic, node.content, node.enabled
        ),
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
        source: str | None = None,
        enabled: bool | None = None,
        sort: str | None = None,
) -> PageResult[SQLNode]:
    page = max(page, 1)
    page_size = max(page_size, 1)
    skip = (page - 1) * page_size
    keyword = (q or "").strip()
    source_filter = (source or "").strip().lower()
    if source_filter not in {"", SQLSource.IMPORT.value, SQLSource.LLM.value}:
        source_filter = ""
    sort_key = _normalize_sql_sort(sort)
    order_clause = _sql_list_order_clause(sort_key)
    enabled_filter = ""
    if enabled is True:
        enabled_filter = "true"
    elif enabled is False:
        enabled_filter = "false"

    where_clause = """
        WHERE s.workspace_id = $workspace_id
          AND ($q = '' OR s.name CONTAINS $q OR s.logic CONTAINS $q
          OR s.content CONTAINS $q OR s.dialect CONTAINS $q)
          AND (
            $source = ''
            OR ($source = 'import' AND coalesce(s.source, 'import') = 'import')
            OR ($source = 'llm' AND s.source = 'llm')
          )
          AND (
            $enabled = ''
            OR ($enabled = 'true' AND coalesce(s.enabled, true) = true)
            OR ($enabled = 'false' AND coalesce(s.enabled, true) = false)
          )
    """

    total_rows = await _run_read(
        f"""
        MATCH (s:{NodeType.SQL})
        {where_clause}
        RETURN count(s) AS total
        """,
        workspace_id=workspace_id,
        q=keyword,
        source=source_filter,
        enabled=enabled_filter,
    )
    total = int(total_rows[0]["total"]) if total_rows else 0

    rows = await _run_read(
        f"""
        MATCH (s:{NodeType.SQL})
        {where_clause}
        RETURN s
        ORDER BY {order_clause}
        SKIP $skip LIMIT $limit
        """,
        workspace_id=workspace_id,
        q=keyword,
        source=source_filter,
        enabled=enabled_filter,
        skip=skip,
        limit=page_size,
    )
    items = [_to_sql_node(dict(row["s"])) for row in rows]
    return PageResult(items=items, total=total, page=page, page_size=page_size)


async def _list_workspace_sql_nodes(workspace_id: str) -> list[SQLNode]:
    rows = await _run_read(
        f"MATCH (s:{NodeType.SQL}) WHERE s.workspace_id = $workspace_id RETURN s",
        workspace_id=workspace_id,
    )
    return [_to_sql_node(dict(row["s"])) for row in rows]


async def update_sql(
        workspace_id: str,
        node_uuid: str,
        name: str | None = None,
        logic: str | None = None,
        content: str | None = None,
        dialect: str | None = None,
        source: SQLSource | str | None = None,
        enabled: bool | None = None,
        preserve_uuid: bool = False,
) -> SQLNode:
    current = await get_sql(node_uuid)

    new_name = normalize_text(name) if name is not None else current.name
    new_logic = logic if logic is not None else current.logic
    new_content = content if content is not None else current.content
    new_dialect = dialect if dialect is not None else current.dialect
    new_source = normalize_sql_source(source) if source is not None else current.source
    new_enabled = normalize_sql_enabled(enabled) if enabled is not None else current.enabled
    now = utc_now_iso()
    created_at = (current.created_at or "").strip() or now

    if not new_name:
        raise ValueError("SQL 名称不能为空")

    node = _build_sql_node(
        workspace_id,
        name=new_name,
        logic=new_logic,
        content=new_content,
        dialect=new_dialect,
        source=new_source,
        enabled=new_enabled,
        created_at=created_at,
        updated_at=now,
        uuid=node_uuid if preserve_uuid else None,
    )

    if node.uuid != node_uuid:
        async def work(tx: AsyncManagedTransaction) -> str:
            if not await graph_node_exists_tx(tx, NodeType.SQL, node_uuid):
                raise NotFoundError(f"SQL 不存在: {node_uuid}")
            await _delete_sql_node_tx(tx, node_uuid)
            try:
                await create_graph_node_tx(tx, node)
            except ConflictError:
                raise ConflictError(f"SQL 已存在: {new_name}") from None
            if new_content.strip():
                await _add_sql_uses_edges_tx(tx, workspace_id, node.uuid, new_content, new_dialect)
            return node_uuid

        async def sync_vectors(old_uuid: str) -> None:
            await delete_vector_records_by_node_uuids([old_uuid])
            await _sync_sql_vector_records(
                workspace_id, node.uuid, new_name, new_logic, new_content, new_enabled
            )

        await commit_graph_then_vectors(work, sync_vectors)
        return node

    async def work(tx: AsyncManagedTransaction) -> None:
        if not await graph_node_exists_tx(tx, NodeType.SQL, node_uuid):
            raise NotFoundError(f"SQL 不存在: {node_uuid}")
        await upsert_graph_node_tx(tx, node)
        await _clear_sql_uses_edges_tx(tx, node_uuid)
        if new_content.strip():
            await _add_sql_uses_edges_tx(tx, workspace_id, node_uuid, new_content, new_dialect)

    await commit_graph_then_vectors(
        work,
        lambda _: _sync_sql_vector_records(
            workspace_id, node_uuid, new_name, new_logic, new_content, new_enabled
        ),
    )
    return node


async def upsert_llm_generated_sql(
        workspace_id: str,
        *,
        question: str,
        logic: str,
        content: str,
        dialect: str,
) -> SQLNode | None:
    """把问数过程中校验并跑出数据的 SQL 沉淀为 LLM 生成样例。

    新样例默认关闭「用于 SQL 生成」，需在 SQL 页启用后才会进入召回。
    已存在的人工导入条目不会被覆盖。
    """
    name = normalize_text(question)
    sql_content = (content or "").strip()
    if not name or not sql_content:
        return None

    table_and_columns, error = find_all_sources(sql_content, (dialect or "").strip().lower() or "mysql")
    if table_and_columns is None:
        raise ValueError(error or "无法解析 SQL 血缘，跳过沉淀")

    sql_uuid = build_stable_sql_uuid(workspace_id, name, sql_content)
    existing: SQLNode | None = None
    try:
        existing = await get_sql(sql_uuid)
    except NotFoundError:
        existing = None

    if existing is not None and existing.source == SQLSource.IMPORT:
        return existing

    enabled = existing.enabled if existing is not None else False
    logic_text = normalize_text(logic) or (existing.logic if existing is not None else "")
    if existing is not None:
        return await update_sql(
            workspace_id,
            existing.uuid,
            name=name,
            logic=logic_text,
            content=sql_content,
            dialect=dialect,
            source=SQLSource.LLM,
            enabled=enabled,
            preserve_uuid=True,
        )

    similar = find_best_similar_sql(
        name=name,
        content=sql_content,
        dialect=dialect,
        candidates=await _list_workspace_sql_nodes(workspace_id),
    )
    if similar is not None:
        match, score = similar
        if match.source == SQLSource.IMPORT:
            logger.info("跳过沉淀：与人工导入 SQL %s 相似 (score=%.3f)", match.uuid, score)
            return match
        merged_logic = merge_sql_logic(match.logic, logic_text)
        logger.info("合并相似 LLM SQL %s <- %s (score=%.3f)", match.uuid, name, score)
        return await update_sql(
            workspace_id,
            match.uuid,
            name=match.name,
            logic=merged_logic,
            content=sql_content,
            dialect=dialect or match.dialect,
            source=SQLSource.LLM,
            enabled=match.enabled,
            preserve_uuid=True,
        )

    return await create_sql(
        workspace_id,
        name=name,
        logic=logic_text,
        content=sql_content,
        dialect=dialect,
        source=SQLSource.LLM,
        enabled=False,
    )


async def delete_sql(node_uuid: str) -> None:
    async def work(tx: AsyncManagedTransaction) -> None:
        if not await graph_node_exists_tx(tx, NodeType.SQL, node_uuid):
            raise NotFoundError(f"SQL 不存在: {node_uuid}")
        await _delete_sql_node_tx(tx, node_uuid)

    await _run_write(work)
    await delete_vector_records_by_node_uuids([node_uuid])
