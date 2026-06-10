import uuid
from typing import Any, List

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncManagedTransaction, AsyncTransaction
from neo4j.exceptions import Neo4jError

from tools.common import require_non_empty_env
from tools.graph.model import NodeType, BaseNode, BaseEdge, ConflictError, EdgeType

_driver: AsyncDriver | None = None

_graph_uuid_constraints_ensured = False


def _get_graph_schema_namespace() -> uuid.UUID:
    """Resolve the UUID namespace lazily so importing graph helpers is cheap."""
    return uuid.uuid5(
        uuid.NAMESPACE_DNS,
        require_non_empty_env("GRAPH_SCHEMA_NS"),
    )


def _get_graph_database() -> str:
    return require_non_empty_env("GRAPH_DATABASE")


def get_graph_driver() -> AsyncDriver:
    """Return a Neo4j async driver bound to the current event loop."""
    global _driver

    if _driver is None:
        uri = require_non_empty_env("GRAPH_URI")
        user = require_non_empty_env("GRAPH_USER")
        password = require_non_empty_env("GRAPH_PASSWORD")
        _driver = AsyncGraphDatabase.driver(uri, auth=(user, password))

    assert isinstance(_driver, AsyncDriver)

    return _driver


async def close_graph_driver() -> None:
    """Close the singleton driver (call once on application shutdown)."""
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None


def build_stable_table_uuid(workspace_id: str, table_name: str) -> str:
    t = (table_name or "").strip()
    return uuid.uuid5(_get_graph_schema_namespace(), f"{workspace_id}:{NodeType.TABLE}:{t}").hex


def build_stable_column_uuid(workspace_id: str, table_name: str, column_name: str) -> str:
    t = (table_name or "").strip()
    c = (column_name or "").strip()
    return uuid.uuid5(_get_graph_schema_namespace(), f"{workspace_id}:{NodeType.COLUMN}:{t}:{c}").hex


def build_stable_knowledge_uuid(workspace_id: str, knowledge_name: str, knowledge_description: str) -> str:
    n = (knowledge_name or "").strip()
    d = (knowledge_description or "").strip()
    return uuid.uuid5(_get_graph_schema_namespace(), f"{workspace_id}:{NodeType.KNOWLEDGE}:{n}:{d}").hex


def build_stable_enum_uuid(workspace_id: str, table_name: str, column_name: str, enum_value: str) -> str:
    t = (table_name or "").strip()
    c = (column_name or "").strip()
    v = (enum_value or "").strip()
    return uuid.uuid5(_get_graph_schema_namespace(), f"{workspace_id}:{NodeType.ENUM}:{t}:{c}:{v}").hex


def build_stable_sql_uuid(workspace_id: str, sql_name: str, sql_content: str) -> str:
    n = (sql_name or "").strip()
    c = (sql_content or "").strip()
    return uuid.uuid5(_get_graph_schema_namespace(), f"{workspace_id}:{NodeType.SQL}:{n}:{c}").hex


async def _run_write(work) -> Any:
    driver = get_graph_driver()
    async with driver.session(database=_get_graph_database()) as session:
        return await session.execute_write(work)


async def _run_read(query: str, **params: Any) -> List[dict[str, Any]]:
    driver = get_graph_driver()
    async with driver.session(database=_get_graph_database()) as session:
        result = await session.run(query, **params)
        return [record.data() async for record in result]


async def _ensure_graph_uuid_constraints() -> None:
    """Apply uuid uniqueness constraints in a dedicated transaction.

    Neo4j forbids mixing schema modifications with data writes in one transaction.
    """
    global _graph_uuid_constraints_ensured
    if _graph_uuid_constraints_ensured:
        return

    async def work(tx: AsyncManagedTransaction) -> None:
        for node_type in NodeType:
            label = node_type.value
            await tx.run(
                f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:`{label}`) REQUIRE n.uuid IS UNIQUE",
            )

    await _run_write(work)
    _graph_uuid_constraints_ensured = True


async def graph_node_exists_tx(
        tx: AsyncManagedTransaction,
        node_type: NodeType,
        node_uuid: str,
) -> bool:
    result = await tx.run(
        f"MATCH (n:{node_type} {{uuid: $uuid}}) RETURN n LIMIT 1",
        uuid=node_uuid,
    )
    return (await result.single()) is not None


async def create_graph_node_tx(
        transaction: AsyncTransaction | AsyncManagedTransaction,
        node: BaseNode,
) -> None:
    """Create a node; raise ConflictError if uuid already exists."""
    await _ensure_graph_uuid_constraints()
    label = node.type.value
    try:
        await transaction.run(
            f"CREATE (n:`{label}`) SET n = $props",
            props=node.model_dump(),
        )
    except Neo4jError as exc:
        if exc.code == "Neo.ClientError.Schema.ConstraintValidationFailed":
            raise ConflictError(f"节点已存在: {node.uuid}") from exc
        raise


async def upsert_graph_node_tx(
        transaction: AsyncTransaction | AsyncManagedTransaction,
        node: BaseNode,
):
    """Merge node by uuid; create if missing, merge properties if present."""
    label = node.type.value

    return await transaction.run(
        f"MERGE (n:`{label}` {{uuid: $uuid}}) SET n += $props",
        uuid=node.uuid,
        props=node.model_dump(),
    )


async def upsert_graph_edge_tx(
        transaction: AsyncTransaction | AsyncManagedTransaction,
        edge: BaseEdge,
) -> None:
    relation = edge.type.value

    result = await transaction.run(
        f"MATCH (a {{uuid: $from_uuid}}), (b {{uuid: $to_uuid}}) "
        f"MERGE (a)-[r:`{relation}`]->(b) "
        "SET r += $rel_props "
        "RETURN 1 AS _",
        from_uuid=edge.from_uuid,
        to_uuid=edge.to_uuid,
        rel_props=edge.model_dump(),
    )
    record = await result.single()
    if record is None:
        raise ValueError(
            f"Cannot add edge {relation!r}: missing node with "
            f"from_uuid={edge.from_uuid!r} and/or to_uuid={edge.to_uuid!r}",
        )


async def truncate_graph(driver: AsyncDriver | None = None, database: str | None = None) -> None:
    if driver is None:
        driver = get_graph_driver()

    if database is None:
        database = _get_graph_database()

    async def work(tx: AsyncManagedTransaction):
        await tx.run("MATCH (n) DETACH DELETE n")

    async with driver.session(database=database) as session:
        await session.execute_write(work)


async def commit_graph_then_vectors(
        graph_work,
        vector_sync,
        compensate_graph=None,
) -> Any:
    """Run graph write, then vector sync; compensate graph if vector sync fails."""
    result = await _run_write(graph_work)
    try:
        await vector_sync(result)
    except Exception:
        if compensate_graph is not None:
            # noinspection PyBroadException
            try:
                await _run_write(lambda tx: compensate_graph(tx, result))
            except Exception:
                pass
        raise
    return result


async def _collect_incoming_describe_edges_tx(
        tx: AsyncManagedTransaction,
        target_uuids: list[str],
) -> list[tuple[str, str]]:
    if not target_uuids:
        return []
    result = await tx.run(
        f"""
        MATCH (k:{NodeType.KNOWLEDGE})-[:{EdgeType.DESCRIBES}]->(target)
        WHERE target.uuid IN $uuids
        RETURN k.uuid AS from_uuid, target.uuid AS to_uuid
        """,
        uuids=target_uuids,
    )
    return [(record["from_uuid"], record["to_uuid"]) async for record in result]


async def _collect_incoming_uses_edges_tx(
        tx: AsyncManagedTransaction,
        target_uuids: list[str],
) -> list[tuple[str, str]]:
    if not target_uuids:
        return []
    result = await tx.run(
        f"""
        MATCH (s:{NodeType.SQL})-[:{EdgeType.USES}]->(target)
        WHERE target.uuid IN $uuids
        RETURN s.uuid AS from_uuid, target.uuid AS to_uuid
        """,
        uuids=target_uuids,
    )
    return [(record["from_uuid"], record["to_uuid"]) async for record in result]
