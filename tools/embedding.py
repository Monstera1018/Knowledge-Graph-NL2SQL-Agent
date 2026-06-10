import hashlib
import json
import os
from pathlib import Path
from typing import List, Optional

import aiohttp
from litellm import aembedding

from .common import require_non_empty_env, to_litellm_model_name

_CACHE_DIR = Path(__file__).resolve().parent.parent / "resources" / "cache"


def _cache_enabled() -> bool:
    raw = os.environ.get("DEV_CACHE", "false")
    return raw.strip().lower() == "true"


def _cache_key(model: str, dimension: int, text: str) -> str:
    payload = f"{model}\0{dimension}\0{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_file(cache_key: str) -> Path:
    return _CACHE_DIR / f"{cache_key}.json"


def _get_dimension() -> int:
    raw = require_non_empty_env("VECTOR_DIMENSION")
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(
            f"VECTOR_DIMENSION must be an integer, got {raw!r}"
        ) from exc


def lookup_embeddings(model: str, texts: List[str]) -> dict[str, List[float]]:
    if not _cache_enabled() or not texts:
        return {}

    result: dict[str, List[float]] = {}
    dimension = _get_dimension()
    for text in dict.fromkeys(texts):
        path = _cache_file(_cache_key(model, dimension, text))
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            result[text] = data["vector"]
        except (json.JSONDecodeError, KeyError, TypeError, OSError):
            continue
    return result


def store_embeddings(model: str, items: List[tuple[str, List[float]]]) -> None:
    if not _cache_enabled() or not items:
        return

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dimension = _get_dimension()
    for text, vector in items:
        path = _cache_file(_cache_key(model, dimension, text))
        payload = {
            "model": model,
            "dimension": dimension,
            "text": text,
            "vector": vector,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )


async def _fetch_embeddings(
        input_: List[str],
        model: str,
        api_base: str,
        api_key: str,
        mode: str,
) -> List[List[float]]:
    if mode.lower() == "litellm":
        response = await aembedding(
            model=to_litellm_model_name(model),
            api_base=api_base,
            api_key=api_key,
            extra_headers={"X-AI-AccessToken": api_key},
            input=input_,
        )
        return [data["embedding"] for data in response.data]

    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + api_key,
        **{"X-AI-AccessToken": api_key},
    }
    payload = {
        "input": input_,
        "model": model,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
                url=api_base,
                headers=headers,
                json=payload,
        ) as response:
            result = await response.json()
            return [x["embedding"] for x in result["data"]]


async def get_embeddings(
        input_: List[str],
        model: Optional[str] = None,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None
) -> List[List[float]]:
    if not input_:
        return []

    if model is None:
        model = require_non_empty_env("EMBEDDING_MODEL_NAME")

    if api_base is None:
        api_base = require_non_empty_env("EMBEDDING_API_BASE")

    if api_key is None:
        api_key = require_non_empty_env("EMBEDDING_API_KEY")

    mode = require_non_empty_env("EMBEDDING_MODE")

    cached = lookup_embeddings(model, input_)
    misses = [text for text in input_ if text not in cached]
    if misses:
        unique_misses = list(dict.fromkeys(misses))
        fetched = await _fetch_embeddings(
            unique_misses,
            model=model,
            api_base=api_base,
            api_key=api_key,
            mode=mode,
        )
        store_embeddings(model, list(zip(unique_misses, fetched)))
        for text, vector in zip(unique_misses, fetched):
            cached[text] = vector

    return [cached[text] for text in input_]
