import uuid
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, overload

from pydantic import BaseModel, Field
from pymilvus import AsyncMilvusClient, DataType

from tools.common import require_non_empty_env
from tools.embedding import get_embeddings

_client: AsyncMilvusClient | None = None

_DEFAULT_SEARCH_EF = 64
_DEFAULT_SEARCH_LIMIT = 10


@dataclass(frozen=True)
class _CollectionSearchSpec:
    collection_name: str
    node_types: tuple[str, ...]
    limit: int


def _env_int(name: str, default: int) -> int:
    raw = require_non_empty_env(name) if name in {
        "VECTOR_TIMEOUT",
        "VECTOR_DIMENSION",
    } else None
    if raw is None:
        import os

        raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _get_timeout() -> int:
    return _env_int("VECTOR_TIMEOUT", 5)


def _get_dimension() -> int:
    return _env_int("VECTOR_DIMENSION", 2048)


def _node_type_value(node_type: Any) -> str:
    return str(getattr(node_type, "value", node_type))


def collection_for_node_type(node_type: Any) -> str:
    value = _node_type_value(node_type)
    if value in {"TABLE", "COLUMN"}:
        return require_non_empty_env("VECTOR_COLLECTION_TABLE")
    if value == "ENUM":
        return require_non_empty_env("VECTOR_COLLECTION_ENUM")
    if value == "KNOWLEDGE":
        return require_non_empty_env("VECTOR_COLLECTION_KNOWLEDGE")
    if value == "SQL":
        return require_non_empty_env("VECTOR_COLLECTION_SQL")
    raise ValueError(f"Unsupported vector node type: {node_type}")


def all_vector_collections() -> List[str]:
    return list(
        dict.fromkeys(
            [
                require_non_empty_env("VECTOR_COLLECTION_TABLE"),
                require_non_empty_env("VECTOR_COLLECTION_ENUM"),
                require_non_empty_env("VECTOR_COLLECTION_KNOWLEDGE"),
                require_non_empty_env("VECTOR_COLLECTION_SQL"),
            ]
        )
    )


def _collection_schema_namespace(collection_name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, collection_name)


def build_stable_vector_uuid(
        workspace_id: str,
        node_uuid: str,
        content: str,
        *,
        collection_name: str,
) -> str:
    namespace = _collection_schema_namespace(collection_name)
    return uuid.uuid5(namespace, f"{workspace_id}:{node_uuid}:{content}").hex


class VectorRecord(BaseModel):
    id: str
    workspace_id: str
    text: str
    vector: List[float] = []
    node_type: str
    node_uuid: str


class VectorSearchHit(BaseModel):
    id: str
    workspace_id: str
    text: str
    node_type: str
    node_uuid: str
    distance: float = Field(description="Milvus COSINE score; higher is more similar.")
    collection_name: str = ""


def get_client() -> AsyncMilvusClient:
    global _client

    if _client is None:
        uri = require_non_empty_env("VECTOR_URI")
        if not uri or not uri.strip():
            raise ValueError(
                "VECTOR_URI is required but missing or empty; set it in the environment or .env."
            )
        _client = AsyncMilvusClient(uri=uri.strip(), timeout=_get_timeout())

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
        client = get_client()
    if timeout is None:
        timeout = _get_timeout()

    collection_names = [collection_name] if collection_name else all_vector_collections()
    for name in collection_names:
        if await client.has_collection(name):
            await client.drop_collection(name, timeout=timeout)


async def create_collection(
        client: AsyncMilvusClient | None = None,
        collection_name: Optional[str] = None,
        dimension: Optional[int] = None,
        timeout: Optional[int] = None,
) -> None:
    if client is None:
        client = get_client()
    if collection_name is None:
        raise ValueError("collection_name is required")
    if dimension is None:
        dimension = _get_dimension()
    if timeout is None:
        timeout = _get_timeout()

    if await client.has_collection(collection_name):
        await drop_collection(client, collection_name, timeout)

    schema = client.create_schema()
    schema.add_field(
        field_name="id",
        is_primary=True,
        auto_id=False,
        datatype=DataType.VARCHAR,
        max_length=64,
    )
    schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=dimension)
    schema.add_field(field_name="node_type", datatype=DataType.VARCHAR, max_length=64)
    schema.add_field(field_name="node_uuid", datatype=DataType.VARCHAR, max_length=64)
    schema.add_field(field_name="workspace_id", datatype=DataType.VARCHAR, max_length=64)
    schema.add_field(field_name="text", datatype=DataType.VARCHAR, max_length=16384)

    await client.create_collection(collection_name, schema=schema, timeout=timeout)

    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name="vector",
        index_type="HNSW",
        metric_type="COSINE",
        params={"M": 32, "efConstruction": 200},
    )
    await client.create_index(
        collection_name=collection_name,
        index_params=index_params,
        timeout=timeout,
    )
    await client.load_collection(collection_name, timeout=timeout)


async def ensure_collection(collection_name: str) -> None:
    client = get_client()
    if await client.has_collection(collection_name):
        return
    await create_collection(client=client, collection_name=collection_name)


async def ensure_all_collections() -> None:
    for collection_name in all_vector_collections():
        await ensure_collection(collection_name)


def _milvus_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _milvus_filter_workspace(workspace_id: str) -> str:
    ws = _milvus_escape(workspace_id.strip())
    return f'workspace_id == "{ws}"'


def _milvus_filter_node_types(node_types: Sequence[Any]) -> str:
    quoted = ", ".join(f'"{_node_type_value(node_type)}"' for node_type in node_types)
    return f"node_type in [{quoted}]"


def _build_semantic_search_filter(
        workspace_id: str,
        node_types: Sequence[Any] | None = None,
) -> str:
    parts = [_milvus_filter_workspace(workspace_id)]
    if node_types:
        parts.append(_milvus_filter_node_types(node_types))
    return " and ".join(parts)


def _milvus_filter_in_node_uuids(node_uuids: List[str]) -> str:
    quoted = ", ".join(f'"{_milvus_escape(node_uuid)}"' for node_uuid in node_uuids)
    return f"node_uuid in [{quoted}]"


async def delete_vector_records_by_node_uuids(node_uuids: List[str]) -> None:
    unique = list(dict.fromkeys(node_uuids))
    if not unique:
        return

    client = get_client()
    for collection_name in all_vector_collections():
        if not await client.has_collection(collection_name):
            continue
        await client.delete(
            collection_name=collection_name,
            filter=_milvus_filter_in_node_uuids(unique),
        )


def unique_texts_for_vector_upsert(
        workspace_id: str,
        node_uuid: str,
        texts: List[str],
        *,
        collection_name: str,
) -> List[str]:
    """去除空文本，以及 Milvus 主键相同的重复文本。"""
    seen_ids: set[str] = set()
    unique: List[str] = []
    for text in texts:
        if not text.strip():
            continue
        vector_id = build_stable_vector_uuid(
            workspace_id,
            node_uuid,
            text,
            collection_name=collection_name,
        )
        if vector_id in seen_ids:
            continue
        seen_ids.add(vector_id)
        unique.append(text)
    return unique


async def upsert_vector_records(
        workspace_id: str,
        texts: List[str],
        node_type: Any,
        node_uuid: str,
) -> None:
    collection_name = collection_for_node_type(node_type)
    texts = unique_texts_for_vector_upsert(
        workspace_id,
        node_uuid,
        texts,
        collection_name=collection_name,
    )
    if len(texts) == 0:
        return

    embeddings = await get_embeddings(texts)
    vector_records: List[VectorRecord] = []
    for text, vector in zip(texts, embeddings):
        vector_records.append(
            VectorRecord(
                id=build_stable_vector_uuid(
                    workspace_id,
                    node_uuid,
                    text,
                    collection_name=collection_name,
                ),
                node_type=_node_type_value(node_type),
                node_uuid=node_uuid,
                text=text,
                vector=vector,
                workspace_id=workspace_id,
            )
        )

    await ensure_collection(collection_name)
    client = get_client()
    await client.upsert(
        collection_name=collection_name,
        data=[x.model_dump() for x in vector_records],
    )


async def flush_all_vector_collections() -> None:
    client = get_client()
    for collection_name in all_vector_collections():
        if await client.has_collection(collection_name):
            await client.flush(collection_name=collection_name)


def _normalize_queries(queries: str | Sequence[str]) -> List[str]:
    if isinstance(queries, str):
        return [(queries or "").strip()]
    return [(q or "").strip() for q in queries]


def _parse_search_batch(batch: list[dict], *, collection_name: str) -> List[VectorSearchHit]:
    hits: List[VectorSearchHit] = []
    for row in batch:
        hits.append(
            VectorSearchHit(
                id=str(row["id"]),
                workspace_id=str(row["workspace_id"]),
                text=str(row["text"]),
                node_type=str(row["node_type"]),
                node_uuid=str(row["node_uuid"]),
                distance=float(row["distance"]),
                collection_name=collection_name,
            )
        )
    return hits


def _topk_env_for_node_type(node_type: Any) -> int:
    value = _node_type_value(node_type)
    if value in {"TABLE", "COLUMN"}:
        return _env_int("VECTOR_TOPK_TABLE", _DEFAULT_SEARCH_LIMIT)
    if value == "ENUM":
        return _env_int("VECTOR_TOPK_ENUM", 8)
    if value == "KNOWLEDGE":
        return _env_int("VECTOR_TOPK_KNOWLEDGE", 8)
    if value == "SQL":
        return _env_int("VECTOR_TOPK_SQL", 5)
    return _DEFAULT_SEARCH_LIMIT


def _search_specs(
        node_types: Sequence[Any] | None,
        limit: int,
) -> List[_CollectionSearchSpec]:
    if node_types:
        grouped: dict[str, list[str]] = {}
        for node_type in node_types:
            grouped.setdefault(collection_for_node_type(node_type), []).append(_node_type_value(node_type))
        return [
            _CollectionSearchSpec(
                collection_name=collection_name,
                node_types=tuple(types),
                limit=limit,
            )
            for collection_name, types in grouped.items()
        ]

    return [
        _CollectionSearchSpec(
            collection_name=require_non_empty_env("VECTOR_COLLECTION_TABLE"),
            node_types=("TABLE", "COLUMN"),
            limit=_topk_env_for_node_type("TABLE"),
        ),
        _CollectionSearchSpec(
            collection_name=require_non_empty_env("VECTOR_COLLECTION_ENUM"),
            node_types=("ENUM",),
            limit=_topk_env_for_node_type("ENUM"),
        ),
        _CollectionSearchSpec(
            collection_name=require_non_empty_env("VECTOR_COLLECTION_KNOWLEDGE"),
            node_types=("KNOWLEDGE",),
            limit=_topk_env_for_node_type("KNOWLEDGE"),
        ),
        _CollectionSearchSpec(
            collection_name=require_non_empty_env("VECTOR_COLLECTION_SQL"),
            node_types=("SQL",),
            limit=_topk_env_for_node_type("SQL"),
        ),
    ]


@overload
async def semantic_search(
        queries: str,
        workspace_id: str,
        *,
        limit: int = _DEFAULT_SEARCH_LIMIT,
        node_types: Sequence[Any] | None = None,
        timeout: int | None = None,
) -> List[VectorSearchHit]: ...


@overload
async def semantic_search(
        queries: Sequence[str],
        workspace_id: str,
        limit: int = _DEFAULT_SEARCH_LIMIT,
        node_types: Sequence[Any] | None = None,
        timeout: int | None = None,
) -> List[List[VectorSearchHit]]: ...


async def semantic_search(
        queries: str | Sequence[str],
        workspace_id: str,
        limit: int = _DEFAULT_SEARCH_LIMIT,
        node_types: Sequence[Any] | None = None,
        timeout: int | None = None,
) -> List[VectorSearchHit] | List[List[VectorSearchHit]]:
    """对查询文本做向量化，并在拆分后的 Milvus 集合中执行 COSINE ANN 检索。"""
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
        timeout = _get_timeout()

    non_empty = [q for q in query_list if q]
    if not non_empty:
        return [] if single else [[] for _ in query_list]

    embeddings = await get_embeddings(non_empty)
    embedding_by_text = dict(zip(non_empty, embeddings))

    vectors: List[List[float]] = []
    vector_query_indexes: List[int] = []
    for idx, q in enumerate(query_list):
        if q:
            vectors.append(embedding_by_text[q])
            vector_query_indexes.append(idx)

    client = get_client()
    combined: List[List[VectorSearchHit]] = [[] for _ in query_list]

    for spec in _search_specs(node_types, limit):
        if spec.limit <= 0:
            continue
        if not await client.has_collection(spec.collection_name):
            continue

        await client.load_collection(spec.collection_name, timeout=timeout)
        raw = await client.search(
            collection_name=spec.collection_name,
            data=vectors,
            anns_field="vector",
            filter=_build_semantic_search_filter(ws, spec.node_types),
            limit=spec.limit,
            output_fields=["workspace_id", "text", "node_type", "node_uuid"],
            search_params={"metric_type": "COSINE", "params": {"ef": _DEFAULT_SEARCH_EF}},
            timeout=timeout,
        )

        for batch_idx, query_idx in enumerate(vector_query_indexes):
            combined[query_idx].extend(
                _parse_search_batch(raw[batch_idx], collection_name=spec.collection_name)
            )

    for idx, hits in enumerate(combined):
        combined[idx] = sorted(hits, key=lambda hit: hit.distance, reverse=True)

    if single:
        return combined[0]
    return combined
