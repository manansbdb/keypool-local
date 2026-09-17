"""Offline tests for Fernet AEAD helpers."""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from llm_keypool import crypto
from llm_keypool.crypto import (
    decrypt_secret,
    encrypt_secret,
    key_fingerprint,
    looks_encrypted,
    reset_crypto_cache,
)


def test_encrypt_decrypt_roundtrip():
    plain = "gsk_test_secret_value_12345"
    token = encrypt_secret(plain)
    assert looks_encrypted(token)
    assert token.startswith("gAAAAA")
    assert decrypt_secret(token) == plain


def test_wrong_key_fails(monkeypatch):
    plain = "sk-secret"
    token = encrypt_secret(plain)
    # Switch master key
    monkeypatch.setenv("LLM_KEYPOOL_MASTER_KEY", Fernet.generate_key().decode())
    reset_crypto_cache()
    with pytest.raises(ValueError, match="decrypt|master key"):
        decrypt_secret(token)


def test_fingerprint_stable():
    a = key_fingerprint("same-key")
    b = key_fingerprint("same-key")
    c = key_fingerprint("other-key")
    assert a == b
    assert a != c
    assert len(a) == 64  # sha256 hex


def test_looks_encrypted():
    assert looks_encrypted("gAAAAA" + "x" * 20)
    assert not looks_encrypted("gsk_plaintext")
    assert not looks_encrypted("")


def test_encrypt_nondeterministic():
    """Fernet tokens differ each call — fingerprint must be used for dedupe."""
    t1 = encrypt_secret("same")
    t2 = encrypt_secret("same")
    assert t1 != t2
    assert decrypt_secret(t1) == decrypt_secret(t2) == "same"
