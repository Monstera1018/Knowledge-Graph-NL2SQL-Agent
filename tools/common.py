import os


def require_non_empty_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not str(value).strip():
        raise ValueError(
            f"{name} is required but missing or empty; set it in the environment or .env."
        )
    return str(value).strip()


def require_env_present(name: str) -> str:
    if name not in os.environ:
        raise ValueError(
            f"{name} is required but missing; set it in the environment or .env (use empty value if none)."
        )
    return os.environ[name]


def normalize_key(value: str) -> str:
    return (value or "").strip().lower()


def normalize_text(value: str) -> str:
    return (value or "").strip()


def to_litellm_model_name(model: str) -> str:
    """``openai:qwen3.6-max`` → ``openai/qwen3.6-max``; leaves ``openai/foo`` unchanged."""

    name = (model or "").strip()
    if not name:
        return name

    if "/" in name and ":" not in name:
        return name

    if ":" in name:
        provider, rest = name.split(":", 1)
        provider = provider.strip()
        rest = rest.strip()
        if provider and rest:
            return f"{provider}/{rest}"

    return name
