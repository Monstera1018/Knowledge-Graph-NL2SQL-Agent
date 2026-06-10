import hashlib
import json
import os
from pathlib import Path
from typing import Optional

from litellm import acompletion

from tools.common import require_non_empty_env, to_litellm_model_name

_CACHE_DIR = Path(__file__).resolve().parent.parent / "resources" / "cache"


def _dev_cache_enabled() -> bool:
    raw = os.environ.get("DEV_CACHE", "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _get_llm_model() -> str:
    return require_non_empty_env("LLM_MODEL_NAME")


def _get_llm_api_base() -> str:
    return require_non_empty_env("OPENAI_API_BASE")


def _get_llm_api_key() -> str:
    return require_non_empty_env("OPENAI_API_KEY")


def _cache_key(model: str, system_prompt: Optional[str], user_prompt: str) -> str:
    payload = f"{model}\0{system_prompt or ''}\0{user_prompt}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_file(cache_key: str) -> Path:
    return _CACHE_DIR / f"llm_{cache_key}.json"


def _lookup_cached(
        model: str,
        user_prompt: str,
        system_prompt: Optional[str],
) -> Optional[str]:
    if not _dev_cache_enabled():
        return None

    path = _cache_file(_cache_key(model, system_prompt, user_prompt))
    if not path.is_file():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        content = data.get("content")
        return content if isinstance(content, str) and content.strip() else None
    except (json.JSONDecodeError, KeyError, TypeError, OSError):
        return None


def _store_cached(
        model: str,
        user_prompt: str,
        system_prompt: Optional[str],
        content: str,
) -> None:
    if not _dev_cache_enabled():
        return

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_file(_cache_key(model, system_prompt, user_prompt))
    payload = {
        "model": model,
        "system_prompt": system_prompt or "",
        "user_prompt": user_prompt,
        "content": content,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


async def chat(
        user_prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
) -> str:
    if model is None:
        model = _get_llm_model()
    if api_base is None:
        api_base = _get_llm_api_base()
    if api_key is None:
        api_key = _get_llm_api_key()

    cached = _lookup_cached(model, user_prompt, system_prompt)
    if cached is not None:
        return cached

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    response = await acompletion(
        model=to_litellm_model_name(model),
        api_base=api_base,
        api_key=api_key,
        extra_headers={"X-AI-AccessToken": api_key},
        messages=messages,
    )
    content = response.choices[0].message.content or ""
    content = content.strip()
    if content:
        _store_cached(model, user_prompt, system_prompt, content)
    return content
