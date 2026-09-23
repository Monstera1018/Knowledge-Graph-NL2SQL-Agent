import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import List, Optional, Tuple

import aiohttp
from litellm import aembedding

from tools.llm import (
    is_llm_quota_or_rate_limit_error,
    mark_profile_rate_limited,
    order_profiles_for_attempt,
)
from .common import require_non_empty_env, to_litellm_model_name

_CACHE_DIR = Path(__file__).resolve().parent.parent / "resources" / "cache"
logger = logging.getLogger(__name__)
_FALLBACK_DELAY_S = 0.5


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


def _optional_env(name: str) -> str:
    return str(os.environ.get(name) or "").strip()


def _embedding_failover_profile(
        model: str,
        api_base: str,
        api_key: str,
) -> Optional[Tuple[str, str, str]]:
    """主套餐额度用尽时，改走另一套 embedding 入口（当前备用通常为 Coding Plan）。"""

    fallback_base = (
        _optional_env("EMBEDDING_FALLBACK_API_BASE")
        or _optional_env("LLM_FALLBACK_API_BASE")
    )
    fallback_key = (
        _optional_env("EMBEDDING_FALLBACK_API_KEY")
        or _optional_env("LLM_FALLBACK_API_KEY")
    )
    fallback_model = _optional_env("EMBEDDING_FALLBACK_MODEL_NAME") or model
    if not fallback_base or not fallback_key:
        return None
    if (
        fallback_base == api_base
        and fallback_key == api_key
        and fallback_model == model
    ):
        return None
    return fallback_model, fallback_base, fallback_key


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

    explicit = model is not None or api_base is not None or api_key is not None
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
        profiles: List[Tuple[str, str, str]] = [(model, api_base, api_key)]
        if not explicit:
            fallback = _embedding_failover_profile(model, api_base, api_key)
            if fallback is not None:
                profiles.append(fallback)

        fetched: Optional[List[List[float]]] = None
        last_error: Optional[BaseException] = None
        profiles = order_profiles_for_attempt(profiles)
        for index, (use_model, use_base, use_key) in enumerate(profiles):
            try:
                fetched = await _fetch_embeddings(
                    unique_misses,
                    model=use_model,
                    api_base=use_base,
                    api_key=use_key,
                    mode=mode,
                )
                break
            except BaseException as exc:
                last_error = exc
                if is_llm_quota_or_rate_limit_error(exc):
                    mark_profile_rate_limited((use_model, use_base, use_key))
                has_next = index + 1 < len(profiles)
                if not has_next or not is_llm_quota_or_rate_limit_error(exc):
                    raise
                logger.warning(
                    "Embedding 套餐触发限流或额度限制，切换到备用套餐重试: %s",
                    exc,
                )
                await asyncio.sleep(_FALLBACK_DELAY_S)

        if fetched is None:
            if last_error is not None:
                raise last_error
            return [cached[text] for text in input_ if text in cached]

        store_embeddings(model, list(zip(unique_misses, fetched)))
        for text, vector in zip(unique_misses, fetched):
            cached[text] = vector

    return [cached[text] for text in input_]
