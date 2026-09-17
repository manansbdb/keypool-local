"""FREE_ONLY / ALLOW_PAID_FALLBACK rotator selection policy."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from llm_keypool.key_store import KeyStore
from llm_keypool.rotator import Rotator
from llm_keypool.crypto import reset_crypto_cache


CONFIGS = {
    "freeprov": {
        "free_tier": True,
        "capabilities": ["general_purpose"],
        "base_url": "https://free.example/v1",
        "openai_compatible": True,
        "limits": {"rpd": 100},
        "cooldown_fallback": {"strategy": "rolling_60"},
        "default_model": "free-model",
        "models": ["free-model"],
    },
    "paidprov": {
        "free_tier": False,
        "capabilities": ["general_purpose"],
        "base_url": "https://paid.example/v1",
        "openai_compatible": True,
        "limits": {"rpd": 1000},
        "cooldown_fallback": {"strategy": "rolling_60"},
        "default_model": "paid-model",
        "models": ["paid-model"],
    },
    "missing_tier": {
        # missing free_tier => treated as False (not free)
        "capabilities": ["general_purpose"],
        "base_url": "https://unknown.example/v1",
        "openai_compatible": True,
        "limits": {"rpd": 50},
        "cooldown_fallback": {"strategy": "rolling_60"},
        "default_model": "unk-model",
        "models": ["unk-model"],
    },
}


@pytest.fixture
def store(tmp_path):
    return KeyStore(db_path=tmp_path / "free_only.db")


def _add(store, provider, key):
    store.register_key(provider, key, "general_purpose", None, {})
    return store.get_all_keys()[-1]


def test_is_free_provider(store):
    rot = Rotator(store, CONFIGS, rotate_every=5)
    assert rot._is_free_provider("freeprov") is True
    assert rot._is_free_provider("paidprov") is False
    assert rot._is_free_provider("missing_tier") is False
    assert rot._is_free_provider("nope") is False


def test_free_only_blocks_paid(store, monkeypatch):
    monkeypatch.setenv("LLM_KEYPOOL_FREE_ONLY", "true")
    monkeypatch.setenv("LLM_KEYPOOL_ALLOW_PAID_FALLBACK", "false")
    _add(store, "freeprov", "free_key")
    _add(store, "paidprov", "paid_key")
    rot = Rotator(store, CONFIGS, rotate_every=5)
    for _ in range(3):
        k = rot.get_best_key("general_purpose", reserve=False)
        assert k is not None
        assert k["provider"] == "freeprov"
        rot.release(k)


def test_free_only_blocks_missing_free_tier(store, monkeypatch):
    monkeypatch.setenv("LLM_KEYPOOL_FREE_ONLY", "true")
    monkeypatch.setenv("LLM_KEYPOOL_ALLOW_PAID_FALLBACK", "false")
    _add(store, "missing_tier", "unk_key")
    rot = Rotator(store, CONFIGS, rotate_every=5)
    assert rot.get_best_key("general_purpose", reserve=False) is None


def test_allow_paid_fallback_when_free_exhausted(store, monkeypatch):
    monkeypatch.setenv("LLM_KEYPOOL_FREE_ONLY", "true")
    monkeypatch.setenv("LLM_KEYPOOL_ALLOW_PAID_FALLBACK", "true")
    free = _add(store, "freeprov", "free_key")
    _add(store, "paidprov", "paid_key")
    # Cool down free key
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    store.record_usage(free["id"], tokens=0, was_429=True, cooldown_until=future)

    rot = Rotator(store, CONFIGS, rotate_every=5)
    k = rot.get_best_key("general_purpose", reserve=False)
    assert k is not None
    assert k["provider"] == "paidprov"


def test_prefer_free_when_both_available(store, monkeypatch):
    monkeypatch.setenv("LLM_KEYPOOL_FREE_ONLY", "true")
    monkeypatch.setenv("LLM_KEYPOOL_ALLOW_PAID_FALLBACK", "true")
    _add(store, "freeprov", "free_key")
    _add(store, "paidprov", "paid_key")
    rot = Rotator(store, CONFIGS, rotate_every=5)
    for _ in range(4):
        k = rot.get_best_key("general_purpose", reserve=False)
        assert k is not None
        assert k["provider"] == "freeprov"
        rot.mark_dispatched(k)
        rot.handle_success(k["key_id"], tokens_used=1)


def test_not_free_only_allows_paid(store, monkeypatch):
    monkeypatch.setenv("LLM_KEYPOOL_FREE_ONLY", "false")
    monkeypatch.setenv("LLM_KEYPOOL_ALLOW_PAID_FALLBACK", "false")
    _add(store, "paidprov", "paid_key")
    rot = Rotator(store, CONFIGS, rotate_every=5)
    k = rot.get_best_key("general_purpose", reserve=False)
    assert k is not None
    assert k["provider"] == "paidprov"


def test_peek_respects_free_only(store, monkeypatch):
    monkeypatch.setenv("LLM_KEYPOOL_FREE_ONLY", "true")
    monkeypatch.setenv("LLM_KEYPOOL_ALLOW_PAID_FALLBACK", "false")
    _add(store, "paidprov", "paid_key")
    rot = Rotator(store, CONFIGS, rotate_every=5)
    assert rot.peek_current_key("general_purpose") is None
