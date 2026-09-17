"""Runtime settings for KeyPool Local (env-driven, no SaaS)."""
from __future__ import annotations

import os
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def free_only() -> bool:
    return _env_bool("LLM_KEYPOOL_FREE_ONLY", True)


def allow_paid_fallback() -> bool:
    return _env_bool("LLM_KEYPOOL_ALLOW_PAID_FALLBACK", False)


def legacy_ignore_model() -> bool:
    """When True, request model is ignored (upstream behavior). Default off."""
    return _env_bool("LLM_KEYPOOL_LEGACY_IGNORE_MODEL", False)


def rotate_every_default() -> int:
    try:
        return max(1, int(os.environ.get("LLM_KEYPOOL_ROTATE_EVERY", "5")))
    except ValueError:
        return 5


def proxy_auth_token() -> str | None:
    """Bearer token required by proxy. None/empty disables auth (dev only)."""
    tok = os.environ.get("LLM_KEYPOOL_PROXY_TOKEN") or os.environ.get("LLM_KEYPOOL_AUTH_TOKEN")
    return tok.strip() if tok and tok.strip() else None


def proxy_require_auth() -> bool:
    # Default: require token when set; if unset, require auth in production sense
    # Spec: auth token required. Empty token → reject all authenticated routes.
    return _env_bool("LLM_KEYPOOL_PROXY_REQUIRE_AUTH", True)


def master_key_file_default() -> Path:
    return Path.home() / ".llm-keypool" / "master.key"


def db_path_default() -> Path:
    return Path.home() / ".llm-keypool" / "keys.db"
