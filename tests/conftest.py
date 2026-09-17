"""Shared fixtures: Fernet master key + crypto cache reset."""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from llm_keypool.crypto import reset_crypto_cache


@pytest.fixture(autouse=True)
def _fernet_master_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LLM_KEYPOOL_MASTER_KEY", key)
    monkeypatch.delenv("LLM_KEYPOOL_MASTER_KEY_FILE", raising=False)
    reset_crypto_cache()
    yield key
    reset_crypto_cache()
