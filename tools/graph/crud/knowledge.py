import logging
from typing import Any, List

from neo4j import AsyncManagedTransaction

from tools.common import normalize_text, utc_now_iso
from tools.graph.internal import create_graph_node_tx, build_stable_table_uuid, build_stable_column_uuid, \
    commit_graph_then_vectors, _run_read, graph_node_exists_tx, upsert_graph_node_tx, \
    upsert_graph_edge_tx, _run_write, \
    build_stable_knowledge_uuid
from tools.graph.model import NodeType, EdgeType, ConflictError, NotFoundError, PageResult, \
    DescribeEdge, KnowledgeNode, KnowledgeRelation, KnowledgeReviewStatus, KnowledgeSource, \
    normalize_knowledge_enabled, normalize_knowledge_review_status, normalize_knowledge_source
from tools.knowledge_similarity import (
    find_best_similar_knowledge,
    merge_knowledge_description,
    merge_knowledge_relations,
)
from tools.vector import delete_vector_records_by_node_uuids, upsert_vector_records

logger = logging.getLogger(__name__)


async def _sync_knowledge_vector_records(
        workspace_id: str,
        knowledge_uuid: str,
        name: str,
        description: str,
        enabled: bool,
) -> None:
    await delete_vector_records_by_node_uuids([knowledge_uuid])
    if not enabled:
        return
    texts = [text for text in (name, description) if text.strip()]
    if texts:
        await upsert_vector_records(workspace_id, texts, NodeType.KNOWLEDGE, knowledge_uuid)


async def _delete_knowledge_node_tx(tx: AsyncManagedTransaction, knowledge_uuid: str) -> None:
    await tx.run(
        f"MATCH (k:{NodeType.KNOWLEDGE} {{uuid: $uuid}}) DETACH DELETE k",
        uuid=knowledge_uuid,
    )


async def _add_knowledge_describe_edges_tx(
        tx: AsyncManagedTransaction,
        workspace_id: str,
        knowledge_uuid: str,
        relations: List[KnowledgeRelation],
) -> None:
    for relation in relations:
        table_uuid = build_stable_table_uuid(workspace_id, relation.table_name)
        for column_name in relation.column_names:
            await upsert_graph_edge_tx(tx, DescribeEdge(
                type=EdgeType.DESCRIBES,
                from_uuid=knowledge_uuid,
                to_uuid=build_stable_column_uuid(workspace_id, relation.table_name, column_name),
            ))
        await upsert_graph_edge_tx(tx, DescribeEdge(
            type=EdgeType.DESCRIBES,
            from_uuid=knowledge_uuid,
            to_uuid=table_uuid,
        ))


_KNOWLEDGE_SORT_TIME_DESC = "time_desc"
_KNOWLEDGE_SORT_TIME_ASC = "time_asc"
_KNOWLEDGE_SORT_NAME = "name"
_KNOWLEDGE_TIME_SORT_FALLBACK = "1970-01-01T00:00:00Z"


def _to_knowledge_node(data: dict[str, Any]) -> KnowledgeNode:
    payload = dict(data or {})
    payload["source"] = normalize_knowledge_source(payload.get("source"))
    payload["enabled"] = normalize_knowledge_enabled(payload.get("enabled"))
    payload["review_status"] = normalize_knowledge_review_status(
        payload.get("review_status"),
        source=payload.get("source"),
        enabled=payload.get("enabled"),
    )
    payload["source_sql_uuid"] = str(payload.get("source_sql_uuid") or "").strip()
    payload["created_at"] = str(payload.get("created_at") or "").strip()
    payload["updated_at"] = str(payload.get("updated_at") or "").strip()
    return KnowledgeNode.model_validate(payload)


def _build_knowledge_node(
        workspace_id: str,
        *,
        name: str,
        description: str,
        source: KnowledgeSource,
        enabled: bool,
        review_status: KnowledgeReviewStatus,
        source_sql_uuid: str = "",
        created_at: str = "",
        updated_at: str = "",
        uuid: str | None = None,
) -> KnowledgeNode:
    now = utc_now_iso()
    created = (created_at or "").strip() or now
    updated = (updated_at or "").strip() or now
    return KnowledgeNode(
        uuid=uuid or build_stable_knowledge_uuid(workspace_id, name, description),
        type=NodeType.KNOWLEDGE,
        name=name,
        description=description,
        workspace_id=workspace_id,
        source=source,
        enabled=enabled,
        review_status=review_status,
        source_sql_uuid=str(source_sql_uuid or "").strip(),
        created_at=created,
        updated_at=updated,
    )


def _knowledge_list_order_clause(sort: str) -> str:
    if sort == _KNOWLEDGE_SORT_NAME:
        return "k.name ASC"
    if sort == _KNOWLEDGE_SORT_TIME_ASC:
        return f"coalesce(k.updated_at, k.created_at, '{_KNOWLEDGE_TIME_SORT_FALLBACK}') ASC, k.name ASC"
    return f"coalesce(k.updated_at, k.created_at, '{_KNOWLEDGE_TIME_SORT_FALLBACK}') DESC, k.name ASC"


def _normalize_knowledge_sort(sort: str | None) -> str:
    value = (sort or "").strip().lower()
    if value in {_KNOWLEDGE_SORT_TIME_ASC, _KNOWLEDGE_SORT_NAME, _KNOWLEDGE_SORT_TIME_DESC}:
        return value
    return _KNOWLEDGE_SORT_TIME_DESC


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


async def _load_knowledge_relations(knowledge_uuid: str) -> list[KnowledgeRelation]:
    source_type = NodeType.KNOWLEDGE
    source_uuid = knowledge_uuid
    edge_type = EdgeType.DESCRIBES

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
        KnowledgeRelation(
            table_name=table_name,
            column_names=sorted(set(column_names)),
        )
        for table_name, column_names in sorted(relations.items())
    ]


async def _clear_knowledge_describe_edges_tx(tx: AsyncManagedTransaction, knowledge_uuid: str) -> None:
    await tx.run(
        f"MATCH (k:{NodeType.KNOWLEDGE} {{uuid: $uuid}})-[r:{EdgeType.DESCRIBES}]->() DELETE r",
        uuid=knowledge_uuid,
    )


async def create_knowledge(
        workspace_id: str,
        name: str,
        description: str = "",
        relations: list[KnowledgeRelation] | None = None,
        source: KnowledgeSource | str = KnowledgeSource.IMPORT,
        enabled: bool | None = None,
        review_status: KnowledgeReviewStatus | str | None = None,
        source_sql_uuid: str = "",
) -> KnowledgeNode:
    name = normalize_text(name)
    if not name:
        raise ValueError("业务知识名称不能为空")

    source_value = normalize_knowledge_source(source)
    if source_value == KnowledgeSource.LLM:
        status = (
            KnowledgeReviewStatus.PENDING
            if review_status is None
            else normalize_knowledge_review_status(review_status, source=source_value, enabled=False)
        )
        enabled_value = False if enabled is None else normalize_knowledge_enabled(enabled)
        if status == KnowledgeReviewStatus.PENDING:
            enabled_value = False
    else:
        status = (
            KnowledgeReviewStatus.APPROVED
            if review_status is None
            else normalize_knowledge_review_status(review_status, source=source_value, enabled=enabled)
        )
        enabled_value = True if enabled is None else normalize_knowledge_enabled(enabled)

    node = _build_knowledge_node(
        workspace_id,
        name=name,
        description=description,
        source=source_value,
        enabled=enabled_value,
        review_status=status,
        source_sql_uuid=source_sql_uuid,
    )
    knowledge_uuid = node.uuid
    relation_list = relations or []

    async def work(tx: AsyncManagedTransaction) -> None:
        try:
            await create_graph_node_tx(tx, node)
        except ConflictError:
            raise ConflictError(f"业务知识已存在: {name}") from None
        await _add_knowledge_describe_edges_tx(tx, workspace_id, knowledge_uuid, relation_list)

    await commit_graph_then_vectors(
        work,
        lambda _: _sync_knowledge_vector_records(
            workspace_id, knowledge_uuid, name, description, enabled_value
        ),
        compensate_graph=lambda tx, _: _delete_knowledge_node_tx(tx, knowledge_uuid),
    )
    return node


async def get_knowledge(node_uuid: str) -> KnowledgeNode:
    rows = await _run_read(
        f"MATCH (k:{NodeType.KNOWLEDGE} {{uuid: $uuid}}) RETURN k",
        uuid=node_uuid,
    )
    if not rows:
        raise NotFoundError(f"业务知识不存在: {node_uuid}")
    return _to_knowledge_node(dict(rows[0]["k"]))


async def list_knowledge_relations(knowledge_uuid: str) -> list[KnowledgeRelation]:
    return await _load_knowledge_relations(knowledge_uuid)


async def _list_workspace_knowledge_nodes(workspace_id: str) -> list[KnowledgeNode]:
    rows = await _run_read(
        f"MATCH (k:{NodeType.KNOWLEDGE}) WHERE k.workspace_id = $workspace_id RETURN k",
        workspace_id=workspace_id,
    )
    return [_to_knowledge_node(dict(row["k"])) for row in rows]


async def list_knowledge_with_relations(
        workspace_id: str,
) -> list[tuple[KnowledgeNode, list[KnowledgeRelation]]]:
    items: list[tuple[KnowledgeNode, list[KnowledgeRelation]]] = []
    for node in await _list_workspace_knowledge_nodes(workspace_id):
        items.append((node, await _load_knowledge_relations(node.uuid)))
    return items


async def list_knowledge(
        workspace_id: str,
        page: int = 1,
        page_size: int = 20,
        q: str | None = None,
        source: str | None = None,
        enabled: bool | None = None,
        review_status: str | None = None,
        sort: str | None = None,
) -> PageResult[KnowledgeNode]:
    page = max(page, 1)
    page_size = max(page_size, 1)
    skip = (page - 1) * page_size
    keyword = (q or "").strip()
    source_filter = (source or "").strip().lower()
    if source_filter not in {"", KnowledgeSource.IMPORT.value, KnowledgeSource.LLM.value}:
        source_filter = ""
    review_filter = (review_status or "").strip().lower()
    if review_filter not in {
        "",
        KnowledgeReviewStatus.PENDING.value,
        KnowledgeReviewStatus.APPROVED.value,
    }:
        review_filter = ""
    sort_key = _normalize_knowledge_sort(sort)
    order_clause = _knowledge_list_order_clause(sort_key)
    enabled_filter = ""
    if enabled is True:
        enabled_filter = "true"
    elif enabled is False:
        enabled_filter = "false"

    where_clause = """
        WHERE k.workspace_id = $workspace_id
          AND ($q = '' OR k.name CONTAINS $q OR k.description CONTAINS $q)
          AND (
            $source = ''
            OR ($source = 'import' AND coalesce(k.source, 'import') = 'import')
            OR ($source = 'llm' AND k.source = 'llm')
          )
          AND (
            $enabled = ''
            OR ($enabled = 'true' AND coalesce(k.enabled, true) = true)
            OR ($enabled = 'false' AND coalesce(k.enabled, true) = false)
          )
          AND (
            $review_status = ''
            OR (
              $review_status = 'pending'
              AND (
                k.review_status = 'pending'
                OR (
                  k.review_status IS NULL
                  AND k.source = 'llm'
                  AND coalesce(k.enabled, true) = false
                )
              )
            )
            OR (
              $review_status = 'approved'
              AND (
                k.review_status = 'approved'
                OR (
                  k.review_status IS NULL
                  AND NOT (
                    k.source = 'llm'
                    AND coalesce(k.enabled, true) = false
                  )
                )
              )
            )
          )
    """

    total_rows = await _run_read(
        f"""
        MATCH (k:{NodeType.KNOWLEDGE})
        {where_clause}
        RETURN count(k) AS total
        """,
        workspace_id=workspace_id,
        q=keyword,
        source=source_filter,
        enabled=enabled_filter,
        review_status=review_filter,
    )
    total = int(total_rows[0]["total"]) if total_rows else 0

    rows = await _run_read(
        f"""
        MATCH (k:{NodeType.KNOWLEDGE})
        {where_clause}
        RETURN k
        ORDER BY {order_clause}
        SKIP $skip LIMIT $limit
        """,
        workspace_id=workspace_id,
        q=keyword,
        source=source_filter,
        enabled=enabled_filter,
        review_status=review_filter,
        skip=skip,
        limit=page_size,
    )
    items = [_to_knowledge_node(dict(row["k"])) for row in rows]
    return PageResult(items=items, total=total, page=page, page_size=page_size)


async def update_knowledge(
        workspace_id: str,
        node_uuid: str,
        name: str | None = None,
        description: str | None = None,
        relations: list[KnowledgeRelation] | None = None,
        source: KnowledgeSource | str | None = None,
        enabled: bool | None = None,
        review_status: KnowledgeReviewStatus | str | None = None,
        source_sql_uuid: str | None = None,
        preserve_uuid: bool = False,
) -> KnowledgeNode:
    current = await get_knowledge(node_uuid)
    current_relations = await _load_knowledge_relations(node_uuid)

    new_name = normalize_text(name) if name is not None else current.name
    new_description = normalize_text(description) if description is not None else current.description
    new_relations = relations if relations is not None else current_relations
    new_source = normalize_knowledge_source(source) if source is not None else current.source
    new_review = current.review_status
    if review_status is not None:
        requested = normalize_knowledge_review_status(
            review_status,
            source=new_source,
            enabled=enabled if enabled is not None else current.enabled,
        )
        if current.review_status == KnowledgeReviewStatus.APPROVED and requested == KnowledgeReviewStatus.PENDING:
            new_review = KnowledgeReviewStatus.APPROVED
        else:
            new_review = requested
    elif enabled is not None and normalize_knowledge_enabled(enabled) and current.review_status == KnowledgeReviewStatus.PENDING:
        new_review = KnowledgeReviewStatus.APPROVED
    if new_review == KnowledgeReviewStatus.PENDING:
        new_enabled = False
    elif current.review_status == KnowledgeReviewStatus.PENDING and new_review == KnowledgeReviewStatus.APPROVED:
        new_enabled = True
    elif enabled is not None:
        new_enabled = normalize_knowledge_enabled(enabled)
    else:
        new_enabled = current.enabled
    new_source_sql_uuid = (
        str(source_sql_uuid).strip()
        if source_sql_uuid is not None
        else current.source_sql_uuid
    )
    now = utc_now_iso()
    created_at = (current.created_at or "").strip() or now

    if not new_name:
        raise ValueError("业务知识名称不能为空")

    new_uuid = node_uuid if preserve_uuid else build_stable_knowledge_uuid(workspace_id, new_name, new_description)
    node = _build_knowledge_node(
        workspace_id,
        name=new_name,
        description=new_description,
        source=new_source,
        enabled=new_enabled,
        review_status=new_review,
        source_sql_uuid=new_source_sql_uuid,
        created_at=created_at,
        updated_at=now,
        uuid=new_uuid,
    )

    if new_uuid != node_uuid:
        async def work(tx: AsyncManagedTransaction) -> str:
            if not await graph_node_exists_tx(tx, NodeType.KNOWLEDGE, node_uuid):
                raise NotFoundError(f"业务知识不存在: {node_uuid}")
            await _delete_knowledge_node_tx(tx, node_uuid)
            try:
                await create_graph_node_tx(tx, node)
            except ConflictError:
                raise ConflictError(f"业务知识已存在: {new_name}") from None
            await _add_knowledge_describe_edges_tx(tx, workspace_id, new_uuid, new_relations)
            return node_uuid

        async def sync_vectors(old_uuid: str) -> None:
            await delete_vector_records_by_node_uuids([old_uuid])
            await _sync_knowledge_vector_records(
                workspace_id, new_uuid, new_name, new_description, new_enabled
            )

        await commit_graph_then_vectors(work, sync_vectors)
        return node

    async def work(tx: AsyncManagedTransaction) -> None:
        if not await graph_node_exists_tx(tx, NodeType.KNOWLEDGE, node_uuid):
            raise NotFoundError(f"业务知识不存在: {node_uuid}")
        await upsert_graph_node_tx(tx, node)
        await _clear_knowledge_describe_edges_tx(tx, node_uuid)
        await _add_knowledge_describe_edges_tx(tx, workspace_id, node_uuid, new_relations)

    await commit_graph_then_vectors(
        work,
        lambda _: _sync_knowledge_vector_records(
            workspace_id, node_uuid, new_name, new_description, new_enabled
        ),
    )
    return node


async def upsert_extracted_knowledge(
        workspace_id: str,
        *,
        name: str,
        description: str,
        relations: list[KnowledgeRelation] | None = None,
        source_sql_uuid: str = "",
) -> tuple[KnowledgeNode, str]:
    """抽取知识写入：与现存条目相似则合并，人工导入只跳过新建。"""
    name = normalize_text(name)
    description = normalize_text(description)
    relation_list = relations or []
    if not name or not description:
        raise ValueError("业务知识名称和说明不能为空")

    candidates = await list_knowledge_with_relations(workspace_id)
    similar = find_best_similar_knowledge(
        name=name,
        description=description,
        relations=relation_list,
        candidates=candidates,
    )
    if similar is None:
        node = await create_knowledge(
            workspace_id,
            name=name,
            description=description,
            relations=relation_list,
            source=KnowledgeSource.LLM,
            enabled=False,
            review_status="pending",
            source_sql_uuid=source_sql_uuid,
        )
        return node, "created"

    match, existing_relations, score = similar
    if match.source == KnowledgeSource.IMPORT:
        logger.info("跳过抽取：与人工导入知识 %s 相似 (score=%.3f)", match.uuid, score)
        return match, "skipped"

    merged_description = merge_knowledge_description(match.description, description)
    merged_relations = merge_knowledge_relations(existing_relations, relation_list)
    logger.info("合并相似 LLM 知识 %s <- %s (score=%.3f)", match.uuid, name, score)
    node = await update_knowledge(
        workspace_id,
        match.uuid,
        name=match.name,
        description=merged_description,
        relations=merged_relations,
        source=KnowledgeSource.LLM,
        enabled=match.enabled,
        review_status=match.review_status,
        source_sql_uuid=match.source_sql_uuid or source_sql_uuid,
        preserve_uuid=True,
    )
    return node, "merged"


def _knowledge_canonical_sort_key(node: KnowledgeNode) -> tuple:
    return (
        0 if node.source == KnowledgeSource.IMPORT else 1,
        0 if node.review_status == KnowledgeReviewStatus.APPROVED else 1,
        0 if node.enabled else 1,
        node.created_at or "",
        node.uuid,
    )


async def collapse_similar_knowledge(workspace_id: str) -> int:
    """把工作区里已有的相似 LLM 知识合并到更早/已审核的条目上。"""
    items = await list_knowledge_with_relations(workspace_id)
    items.sort(key=lambda pair: _knowledge_canonical_sort_key(pair[0]))
    kept: list[tuple[KnowledgeNode, list[KnowledgeRelation]]] = []
    removed = 0
    for node, relations in items:
        similar = find_best_similar_knowledge(
            name=node.name,
            description=node.description,
            relations=relations,
            candidates=kept,
            exclude_uuid=node.uuid,
        )
        if similar is None:
            kept.append((node, relations))
            continue
        match, existing_relations, score = similar
        if match.source == KnowledgeSource.IMPORT:
            if node.source == KnowledgeSource.LLM:
                logger.info("删除与人工导入重复的抽取知识 %s (score=%.3f)", node.uuid, score)
                await delete_knowledge(node.uuid)
                removed += 1
            else:
                kept.append((node, relations))
            continue
        if node.source == KnowledgeSource.IMPORT:
            kept.append((node, relations))
            continue
        merged_description = merge_knowledge_description(match.description, node.description)
        merged_relations = merge_knowledge_relations(existing_relations, relations)
        logger.info("合并已有相似知识 %s <- %s (score=%.3f)", match.uuid, node.name, score)
        updated = await update_knowledge(
            workspace_id,
            match.uuid,
            name=match.name,
            description=merged_description,
            relations=merged_relations,
            source=KnowledgeSource.LLM,
            enabled=match.enabled,
            review_status=match.review_status,
            source_sql_uuid=match.source_sql_uuid or node.source_sql_uuid,
            preserve_uuid=True,
        )
        await delete_knowledge(node.uuid)
        for index, (kept_node, _) in enumerate(kept):
            if kept_node.uuid == match.uuid:
                kept[index] = (updated, merged_relations)
                break
        removed += 1
    return removed


async def delete_knowledge(uuid: str) -> None:
    async def work(tx: AsyncManagedTransaction) -> None:
        if not await graph_node_exists_tx(tx, NodeType.KNOWLEDGE, uuid):
            raise NotFoundError(f"业务知识不存在: {uuid}")
        await _delete_knowledge_node_tx(tx, uuid)

    await _run_write(work)
    await delete_vector_records_by_node_uuids([uuid])
