"""Authenticated encryption for API keys at rest (Fernet / AES-128-CBC+HMAC)."""
from __future__ import annotations

import hashlib
import os
import warnings
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .settings import master_key_file_default

_ENV_KEY = "LLM_KEYPOOL_MASTER_KEY"
_ENV_KEY_FILE = "LLM_KEYPOOL_MASTER_KEY_FILE"

# Module-level cache so all KeyStore instances share one Fernet
_fernet: Fernet | None = None
_master_source: str | None = None


def key_fingerprint(plaintext: str) -> str:
    """Stable SHA-256 hex fingerprint for dedupe (never store plaintext)."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _load_or_create_master_key() -> tuple[bytes, str]:
    env_key = os.environ.get(_ENV_KEY)
    if env_key and env_key.strip():
        raw = env_key.strip().encode("utf-8")
        # Accept raw Fernet key or derive from passphrase
        try:
            Fernet(raw)
            return raw, "env:LLM_KEYPOOL_MASTER_KEY"
        except Exception:
            digest = hashlib.sha256(raw).digest()
            import base64
            key = base64.urlsafe_b64encode(digest)
            return key, "env:LLM_KEYPOOL_MASTER_KEY(derived)"

    path_str = os.environ.get(_ENV_KEY_FILE)
    path = Path(path_str) if path_str else master_key_file_default()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        data = path.read_bytes().strip()
        return data, f"file:{path}"

    key = Fernet.generate_key()
    path.write_bytes(key)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return key, f"file:{path}(created)"


def get_fernet() -> Fernet:
    global _fernet, _master_source
    if _fernet is None:
        key, src = _load_or_create_master_key()
        _fernet = Fernet(key)
        _master_source = src
    return _fernet


def reset_crypto_cache() -> None:
    """Test helper: clear cached Fernet."""
    global _fernet, _master_source
    _fernet = None
    _master_source = None


def encrypt_secret(plaintext: str) -> str:
    token = get_fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_secret(token: str) -> str:
    try:
        return get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise ValueError("Failed to decrypt API key — wrong master key?") from e


def looks_encrypted(value: str) -> bool:
    """Heuristic: Fernet tokens start with gAAAAA."""
    return bool(value) and value.startswith("gAAAAA")


def mask_secret(plaintext: str | None, show: int = 4) -> str:
    if not plaintext:
        return ""
    if len(plaintext) <= show * 2:
        return "*" * len(plaintext)
    return plaintext[:show] + "…" + plaintext[-show:]


def warn_legacy_key_flag() -> None:
    warnings.warn(
        "Passing --key on the CLI stores the secret in shell history. "
        "Prefer interactive prompt (omit --key).",
        UserWarning,
        stacklevel=3,
    )
