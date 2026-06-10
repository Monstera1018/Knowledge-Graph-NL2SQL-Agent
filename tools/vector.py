import uuid
from typing import List, Optional, Sequence, overload

from pydantic import BaseModel, Field
from pymilvus import AsyncMilvusClient, DataType

from tools.common import require_non_empty_env
from tools.graph.model import NodeType
from tools.embedding import get_embeddings

_client: AsyncMilvusClient | None = None


def _get_vector_schema_namespace() -> uuid.UUID:
    return uuid.uuid5(
        uuid.NAMESPACE_DNS,
        _get_collection_name(),
    )


def build_stable_vector_uuid(workspace_id: str, node_uuid: str, content: str) -> str:
    return uuid.uuid5(_get_vector_schema_namespace(), f"{workspace_id}:{node_uuid}:{content}").hex


class VectorRecord(BaseModel):
    id: str
    workspace_id: str
    text: str
    vector: List[float] = []
    node_type: NodeType
    node_uuid: str


class VectorSearchHit(BaseModel):
    id: str
    workspace_id: str
    text: str
    node_type: NodeType
    node_uuid: str
    distance: float = Field(description="Milvus COSINE distance; lower is more similar.")


_DEFAULT_SEARCH_LIMIT = 10
_DEFAULT_SEARCH_EF=64

def _get_collection_name() -> str:
    return require_non_empty_env("VECTOR_COLLECTION_NAME")


def get_client() -> AsyncMilvusClient:
    global _client

    if _client is None:
        uri = require_non_empty_env("VECTOR_URI")
        if not uri or not uri.strip():
            raise ValueError(
                "VECTOR_URI is required but missing or empty; set it in the environment or .env."
            )
        timeout_raw = require_non_empty_env("VECTOR_TIMEOUT")
        if timeout_raw is None or not str(timeout_raw).strip():
            raise ValueError(
                "VECTOR_TIMEOUT is required but missing or empty; set it in the environment or .env."
            )
        try:
            timeout = int(timeout_raw)
        except ValueError as exc:
            raise ValueError(
                f"VECTOR_TIMEOUT must be an integer, got {timeout_raw!r}"
            ) from exc
        _client = AsyncMilvusClient(uri=uri.strip(), timeout=timeout)

    assert isinstance(_client, AsyncMilvusClient)

    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


async def drop_collection(
        client: AsyncMilvusClient | None = None,
        collection_name: Optional[str] = None,
        timeout: Optional[int] = None,
) -> None:
    if client is None:
        client: AsyncMilvusClient = get_client()

    if collection_name is None:
        collection_name: str = require_non_empty_env("VECTOR_COLLECTION_NAME")

    if timeout is None:
        timeout = int(require_non_empty_env("VECTOR_TIMEOUT"))

    if await client.has_collection(collection_name):
        await client.drop_collection(collection_name, timeout=timeout)


async def create_collection(
        client: AsyncMilvusClient | None = None,
        collection_name: Optional[str] = None,
        dimension: Optional[int] = None,
        timeout: Optional[int] = None,
) -> None:
    if client is None:
        client: AsyncMilvusClient = get_client()

    if collection_name is None:
        collection_name: str = require_non_empty_env("VECTOR_COLLECTION_NAME")

    if dimension is None:
        dimension = int(require_non_empty_env("VECTOR_DIMENSION"))

    if timeout is None:
        timeout = int(require_non_empty_env("VECTOR_TIMEOUT"))

    if await client.has_collection(collection_name):
        await drop_collection(client, collection_name, timeout)

    schema = client.create_schema()

    schema.add_field(
        field_name="id",
        is_primary=True,
        auto_id=False,
        datatype=DataType.VARCHAR,
        max_length=64
    )

    schema.add_field(
        field_name="vector",
        datatype=DataType.FLOAT_VECTOR, dim=dimension
    )

    schema.add_field(
        field_name="node_type",
        datatype=DataType.VARCHAR,
        max_length=64
    )

    schema.add_field(
        field_name="node_uuid",
        datatype=DataType.VARCHAR,
        max_length=64
    )

    schema.add_field(
        field_name="workspace_id",
        datatype=DataType.VARCHAR,
        max_length=64
    )

    schema.add_field(
        field_name="text",
        datatype=DataType.VARCHAR,
        max_length=2048
    )

    await client.create_collection(collection_name, schema=schema, timeout=timeout)

    index_params = client.prepare_index_params()

    index_params.add_index(
        field_name="vector",
        index_type="HNSW",
        metric_type="COSINE",
        params={
            "M": 32,
            "efConstruction": 200
        }
    )

    await client.create_index(
        collection_name=collection_name,
        index_params=index_params,
        timeout=timeout
    )

    await client.load_collection(collection_name, timeout=timeout)


async def ensure_collection() -> None:
    client = get_client()
    collection_name = _get_collection_name()
    if await client.has_collection(collection_name):
        return

    await create_collection()


def _milvus_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _milvus_filter_workspace(workspace_id: str) -> str:
    ws = _milvus_escape(workspace_id.strip())
    return f'workspace_id == "{ws}"'


def _milvus_filter_node_types(node_types: Sequence[NodeType]) -> str:
    quoted = ", ".join(f'"{node_type}"' for node_type in node_types)
    return f"node_type in [{quoted}]"


def _build_semantic_search_filter(
        workspace_id: str,
        node_types: Sequence[NodeType] | None = None,
) -> str:
    parts = [_milvus_filter_workspace(workspace_id)]
    if node_types:
        parts.append(_milvus_filter_node_types(node_types))
    return " and ".join(parts)


def _milvus_filter_in_node_uuids(node_uuids: List[str]) -> str:
    quoted = ", ".join(f"\"{node_uuid}\"" for node_uuid in node_uuids)
    return f"node_uuid in [{quoted}]"


async def delete_vector_records_by_node_uuids(node_uuids: List[str]) -> None:
    unique = list(dict.fromkeys(node_uuids))
    if not unique:
        return

    client = get_client()
    collection_name = _get_collection_name()
    if not await client.has_collection(collection_name):
        return

    await client.delete(
        collection_name=collection_name,
        filter=_milvus_filter_in_node_uuids(unique),
    )


def unique_texts_for_vector_upsert(workspace_id: str, node_uuid: str, texts: List[str]) -> List[str]:
    """Drop blank texts and duplicates that share the same Milvus primary key.
    """
    seen_ids: set[str] = set()
    unique: List[str] = []
    for text in texts:
        if not text.strip():
            continue
        vector_id = build_stable_vector_uuid(workspace_id, node_uuid, text)
        if vector_id in seen_ids:
            continue
        seen_ids.add(vector_id)
        unique.append(text)
    return unique


async def upsert_vector_records(workspace_id: str, texts: List[str], node_type: NodeType, node_uuid: str) -> None:
    texts = unique_texts_for_vector_upsert(workspace_id, node_uuid, texts)
    if len(texts) == 0:
        return

    vector_records: List[VectorRecord] = []
    embeddings = await get_embeddings(texts)
    for text, vector in zip(texts, embeddings):
        vector_records.append(
            VectorRecord(
                id=build_stable_vector_uuid(workspace_id, node_uuid, text),
                node_type=node_type,
                node_uuid=node_uuid,
                text=text,
                vector=vector,
                workspace_id=workspace_id
            )
        )

    await ensure_collection()
    client = get_client()
    await client.upsert(
        collection_name=_get_collection_name(),
        data=[x.model_dump() for x in vector_records],
    )


def _normalize_queries(queries: str | Sequence[str]) -> List[str]:
    if isinstance(queries, str):
        return [(queries or "").strip()]
    return [(q or "").strip() for q in queries]


def _parse_search_batch(batch: list[dict]) -> List[VectorSearchHit]:
    hits: List[VectorSearchHit] = []
    for row in batch:
        hits.append(
            VectorSearchHit(
                id=str(row["id"]),
                workspace_id=str(row["workspace_id"]),
                text=str(row["text"]),
                node_type=NodeType(row["node_type"]),
                node_uuid=str(row["node_uuid"]),
                distance=float(row["distance"]),
            )
        )
    return hits


@overload
async def semantic_search(
        queries: str,
        workspace_id: str,
        *,
        limit: int = _DEFAULT_SEARCH_LIMIT,
        node_types: Sequence[NodeType] | None = None,
        timeout: int | None = None,
) -> List[VectorSearchHit]: ...


@overload
async def semantic_search(
        queries: Sequence[str],
        workspace_id: str,
        limit: int = _DEFAULT_SEARCH_LIMIT,
        node_types: Sequence[NodeType] | None = None,
        timeout: int | None = None,
) -> List[List[VectorSearchHit]]: ...


async def semantic_search(
        queries: str | Sequence[str],
        workspace_id: str,
        limit: int = _DEFAULT_SEARCH_LIMIT,
        node_types: Sequence[NodeType] | None = None,
        timeout: int | None = None,
) -> List[VectorSearchHit] | List[List[VectorSearchHit]]:
    """Embed *queries* and run COSINE ANN search in Milvus for the given workspace.

    Pass a single string to get a flat hit list; pass a sequence to get one hit
    list per query (empty query strings yield empty lists at the same index).
    """
    single = isinstance(queries, str)
    query_list = _normalize_queries(queries)

    if not query_list:
        return [] if single else []

    ws = (workspace_id or "").strip()
    if not ws:
        raise ValueError("workspace_id is required")

    if limit <= 0:
        return [] if single else [[] for _ in query_list]

    if timeout is None:
        timeout = int(require_non_empty_env("VECTOR_TIMEOUT"))

    collection_name = _get_collection_name()
    client = get_client()
    if not await client.has_collection(collection_name):
        return [] if single else [[] for _ in query_list]

    await client.load_collection(collection_name, timeout=timeout)

    non_empty = [q for q in query_list if q]
    if not non_empty:
        return [] if single else [[] for _ in query_list]

    embeddings = await get_embeddings(non_empty)
    embedding_by_text = dict(zip(non_empty, embeddings))

    vectors: List[List[float]] = []
    for q in query_list:
        if q:
            vectors.append(embedding_by_text[q])

    raw = await client.search(
        collection_name=collection_name,
        data=vectors,
        anns_field="vector",
        filter=_build_semantic_search_filter(ws, node_types),
        limit=limit,
        output_fields=["workspace_id", "text", "node_type", "node_uuid"],
        search_params={"metric_type": "COSINE", "params": {"ef": _DEFAULT_SEARCH_EF}},
        timeout=timeout,
    )

    batch_results: List[List[VectorSearchHit]] = []
    batch_idx = 0
    for q in query_list:
        if not q:
            batch_results.append([])
            continue
        batch_results.append(_parse_search_batch(raw[batch_idx]))
        batch_idx += 1

    if single:
        return batch_results[0]
    return batch_results
