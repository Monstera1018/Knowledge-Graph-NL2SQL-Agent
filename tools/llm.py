import asyncio
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from litellm import acompletion

from tools.common import require_non_empty_env, to_litellm_model_name

_CACHE_DIR = Path(__file__).resolve().parent.parent / "resources" / "cache"
logger = logging.getLogger(__name__)

# 限流/额度类错误才切备用套餐；同套餐连打会把 burst 打得更密。
_QUOTA_OR_RATE_RE = re.compile(
    r"request burst|slow down traffic|system protection|"
    r"rate.?limit|too many requests|tpm|rpm|throttl|"
    r"quota|insufficient_quota|resource.?exhausted|"
    r"budget.?exceeded|overloaded|429\b",
    re.I,
)
_AUTH_RE = re.compile(
    r"invalid api key|unauthorized|authentication|401\b|forbidden|403\b",
    re.I,
)
_FALLBACK_DELAY_S = 0.5


@dataclass(frozen=True)
class LlmProfile:
    """一套可独立调用的模型入口（endpoint + key + 模型名）。"""

    name: str
    model: str
    api_base: str
    api_key: str


def _optional_env(name: str) -> str:
    return str(os.environ.get(name) or "").strip()


def load_llm_profiles() -> list[LlmProfile]:
    """主套餐必填；备用套餐（通常为 Agent Plan）配齐 base 与 key 后才会启用。"""

    primary = LlmProfile(
        name="primary",
        model=require_non_empty_env("LLM_MODEL_NAME"),
        api_base=require_non_empty_env("OPENAI_API_BASE"),
        api_key=require_non_empty_env("OPENAI_API_KEY"),
    )
    profiles = [primary]

    fallback_base = _optional_env("LLM_FALLBACK_API_BASE")
    fallback_key = _optional_env("LLM_FALLBACK_API_KEY")
    if fallback_base and fallback_key:
        fallback_model = _optional_env("LLM_FALLBACK_MODEL_NAME") or primary.model
        fallback = LlmProfile(
            name="fallback",
            model=fallback_model,
            api_base=fallback_base,
            api_key=fallback_key,
        )
        if (
            fallback.api_base != primary.api_base
            or fallback.api_key != primary.api_key
            or fallback.model != primary.model
        ):
            profiles.append(fallback)
    elif fallback_key and not fallback_base:
        logger.warning(
            "已配置 LLM_FALLBACK_API_KEY，但缺少 LLM_FALLBACK_API_BASE，备用套餐未启用"
        )
    return profiles


def litellm_agent_kwargs(profile: LlmProfile) -> dict[str, Any]:
    """ADK LiteLlm 构造参数：显式传 api_base/api_key，避免备用套餐误用全局环境变量。"""

    return {
        "model": to_litellm_model_name(profile.model),
        "api_base": profile.api_base,
        "api_key": profile.api_key,
        "temperature": 0.0,
        "enable_thinking": False,
        "num_retries": 0,
        "extra_headers": {"X-AI-AccessToken": profile.api_key},
        "allowed_openai_params": ["enable_thinking"],
    }


def _iter_exceptions(exc: BaseException, *, _seen: Optional[set[int]] = None):
    seen = _seen if _seen is not None else set()
    ident = id(exc)
    if ident in seen:
        return
    seen.add(ident)
    yield exc
    group = getattr(exc, "exceptions", None)
    if group:
        for inner in group:
            if isinstance(inner, BaseException):
                yield from _iter_exceptions(inner, _seen=seen)
    for attr in ("original_exception", "__cause__", "__context__"):
        inner = getattr(exc, attr, None)
        if isinstance(inner, BaseException):
            yield from _iter_exceptions(inner, _seen=seen)


def is_llm_quota_or_rate_limit_error(exc: BaseException) -> bool:
    """判断是否为限流、突发保护或额度用尽，这类错误才切换备用套餐。"""

    try:
        from litellm.exceptions import BudgetExceededError, RateLimitError
    except Exception:  # pragma: no cover - 运行环境缺 litellm 异常时退回文案匹配
        RateLimitError = BudgetExceededError = tuple()  # type: ignore[misc, assignment]

    for item in _iter_exceptions(exc):
        if RateLimitError and isinstance(item, RateLimitError):
            return True
        if BudgetExceededError and isinstance(item, BudgetExceededError):
            return True
        status = getattr(item, "status_code", None)
        if status is None:
            status = getattr(item, "status", None)
        text = str(item)
        if status == 429 or _QUOTA_OR_RATE_RE.search(text):
            if status in (401, 403) or (_AUTH_RE.search(text) and not _QUOTA_OR_RATE_RE.search(text)):
                continue
            return True
    return False


async def generate_with_failover(
    backends: list[Any],
    llm_request: Any,
    stream: bool = False,
) -> AsyncIterator[Any]:
    """依次尝试多套模型后端；仅在尚未向调用方产出内容且错误为限流/额度时切换。"""

    if not backends:
        raise RuntimeError("没有可用的模型套餐")

    last_error: Optional[BaseException] = None
    total = len(backends)
    for index, backend in enumerate(backends):
        request = llm_request
        backend_model = getattr(backend, "model", None)
        if (
            backend_model
            and hasattr(llm_request, "model_copy")
            and getattr(llm_request, "model", None) not in (None, "", backend_model)
        ):
            try:
                request = llm_request.model_copy(update={"model": backend_model})
            except Exception:
                request = llm_request

        yielded = False
        try:
            async for chunk in backend.generate_content_async(request, stream=stream):
                yielded = True
                yield chunk
            return
        except BaseException as exc:
            last_error = exc
            has_next = index + 1 < total
            if yielded or not has_next or not is_llm_quota_or_rate_limit_error(exc):
                raise
            next_backend = backends[index + 1]
            logger.warning(
                "模型套餐 %s 触发限流或额度限制，切换到 %s 重试: %s",
                getattr(backend, "profile_name", getattr(backend, "model", index)),
                getattr(next_backend, "profile_name", getattr(next_backend, "model", index + 1)),
                exc,
            )
            await asyncio.sleep(_FALLBACK_DELAY_S)

    if last_error is not None:
        raise last_error


def _dev_cache_enabled() -> bool:
    raw = os.environ.get("DEV_CACHE", "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


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
    if model is not None or api_base is not None or api_key is not None:
        profiles = [
            LlmProfile(
                name="explicit",
                model=model or require_non_empty_env("LLM_MODEL_NAME"),
                api_base=api_base or require_non_empty_env("OPENAI_API_BASE"),
                api_key=api_key or require_non_empty_env("OPENAI_API_KEY"),
            )
        ]
    else:
        profiles = load_llm_profiles()

    cached = _lookup_cached(profiles[0].model, user_prompt, system_prompt)
    if cached is not None:
        return cached

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    last_error: Optional[BaseException] = None
    for index, profile in enumerate(profiles):
        try:
            response = await acompletion(
                model=to_litellm_model_name(profile.model),
                api_base=profile.api_base,
                api_key=profile.api_key,
                temperature=0.0,
                num_retries=0,
                extra_headers={"X-AI-AccessToken": profile.api_key},
                messages=messages,
            )
            content = response.choices[0].message.content or ""
            content = content.strip()
            if content:
                _store_cached(profile.model, user_prompt, system_prompt, content)
            return content
        except BaseException as exc:
            last_error = exc
            has_next = index + 1 < len(profiles)
            if not has_next or not is_llm_quota_or_rate_limit_error(exc):
                raise
            logger.warning(
                "模型套餐 %s 触发限流或额度限制，切换到 %s 重试: %s",
                profile.name,
                profiles[index + 1].name,
                exc,
            )
            await asyncio.sleep(_FALLBACK_DELAY_S)

    if last_error is not None:
        raise last_error
    return ""
