from typing import Any, List

from neo4j import AsyncManagedTransaction

from tools.common import normalize_text
from tools.graph.internal import create_graph_node_tx, build_stable_table_uuid, build_stable_column_uuid, \
    commit_graph_then_vectors, _run_read, graph_node_exists_tx, upsert_graph_node_tx, \
    upsert_graph_edge_tx, _run_write, \
    build_stable_knowledge_uuid
from tools.graph.model import NodeType, EdgeType, ConflictError, NotFoundError, PageResult, \
    DescribeEdge, KnowledgeNode, KnowledgeRelation
from tools.vector import delete_vector_records_by_node_uuids, upsert_vector_records


async def _sync_knowledge_vector_records(
        workspace_id: str,
        knowledge_uuid: str,
        name: str,
        description: str,
) -> None:
    await delete_vector_records_by_node_uuids([knowledge_uuid])
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


def _to_knowledge_node(data: dict[str, Any]) -> KnowledgeNode:
    return KnowledgeNode.model_validate(data)


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
) -> KnowledgeNode:
    name = normalize_text(name)
    if not name:
        raise ValueError("业务知识名称不能为空")

    knowledge_uuid = build_stable_knowledge_uuid(workspace_id, name, description)
    node = KnowledgeNode(
        uuid=knowledge_uuid,
        type=NodeType.KNOWLEDGE,
        name=name,
        description=description,
        workspace_id=workspace_id
    )
    relation_list = relations or []

    async def work(tx: AsyncManagedTransaction) -> None:
        try:
            await create_graph_node_tx(tx, node)
        except ConflictError:
            raise ConflictError(f"业务知识已存在: {name}") from None
        await _add_knowledge_describe_edges_tx(tx, workspace_id, knowledge_uuid, relation_list)

    await commit_graph_then_vectors(
        work,
        lambda _: _sync_knowledge_vector_records(workspace_id, knowledge_uuid, name, description),
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


async def list_knowledge(
        workspace_id: str,
        page: int = 1,
        page_size: int = 20,
        q: str | None = None,
) -> PageResult[KnowledgeNode]:
    page = max(page, 1)
    page_size = max(page_size, 1)
    skip = (page - 1) * page_size
    keyword = (q or "").strip()

    total_rows = await _run_read(
        f"""
        MATCH (k:{NodeType.KNOWLEDGE})
        WHERE k.workspace_id = $workspace_id
          AND ($q = '' OR k.name CONTAINS $q OR k.description CONTAINS $q)
        RETURN count(k) AS total
        """,
        workspace_id=workspace_id,
        q=keyword,
    )
    total = int(total_rows[0]["total"]) if total_rows else 0

    rows = await _run_read(
        f"""
        MATCH (k:{NodeType.KNOWLEDGE})
        WHERE k.workspace_id = $workspace_id
          AND ($q = '' OR k.name CONTAINS $q OR k.description CONTAINS $q)
        RETURN k
        ORDER BY k.name
        SKIP $skip LIMIT $limit
        """,
        workspace_id=workspace_id,
        q=keyword,
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
) -> KnowledgeNode:
    current = await get_knowledge(node_uuid)
    current_relations = await _load_knowledge_relations(node_uuid)

    new_name = normalize_text(name) if name is not None else current.name
    new_description = normalize_text(description) if description is not None else current.description
    new_relations = relations if relations is not None else current_relations

    if not new_name:
        raise ValueError("业务知识名称不能为空")

    new_uuid = build_stable_knowledge_uuid(workspace_id, new_name, new_description)
    node = KnowledgeNode(
        uuid=new_uuid,
        type=NodeType.KNOWLEDGE,
        name=new_name,
        description=new_description,
        workspace_id=workspace_id
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
            await _sync_knowledge_vector_records(workspace_id, new_uuid, new_name, new_description)

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
        lambda _: _sync_knowledge_vector_records(workspace_id, node_uuid, new_name, new_description),
    )
    return node


async def delete_knowledge(uuid: str) -> None:
    async def work(tx: AsyncManagedTransaction) -> None:
        if not await graph_node_exists_tx(tx, NodeType.KNOWLEDGE, uuid):
            raise NotFoundError(f"业务知识不存在: {uuid}")
        await _delete_knowledge_node_tx(tx, uuid)

    await _run_write(work)
    await delete_vector_records_by_node_uuids([uuid])
