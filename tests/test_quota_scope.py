"""Shared quota_scope: cooldowns/usage mirrored across sibling keys."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from llm_keypool.key_store import KeyStore


@pytest.fixture
def store(tmp_path):
    return KeyStore(db_path=tmp_path / "quota.db")


def test_default_quota_scope_unique_per_key(store):
    store.register_key("groq", "key_a", "general_purpose", None, {})
    store.register_key("groq", "key_b", "general_purpose", None, {})
    keys = store.get_all_keys()
    scopes = {k["quota_scope"] for k in keys}
    assert len(scopes) == 2
    for k in keys:
        assert k["quota_scope"].startswith("groq:")
        assert len(k["quota_scope"].split(":", 1)[1]) == 16


def test_shared_quota_scope_cooldown_after_429(store):
    scope = "shared-account-1"
    store.register_key("groq", "key_a", "general_purpose", None, {}, quota_scope=scope)
    store.register_key("mistral", "key_b", "general_purpose", None, {}, quota_scope=scope)
    keys = store.get_keys_by_quota_scope(scope)
    assert len(keys) == 2
    a, b = keys[0], keys[1]

    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    store.record_usage(a["id"], tokens=0, was_429=True, cooldown_until=future)

    a2 = store.get_key_by_id(a["id"])
    b2 = store.get_key_by_id(b["id"])
    assert a2["cooldown_until"] == future
    assert b2["cooldown_until"] == future

    active = store.get_active_keys("general_purpose")
    assert active == []


def test_shared_quota_scope_usage_mirrored(store):
    scope = "shared-usage"
    store.register_key("groq", "key_a", "general_purpose", None, {}, quota_scope=scope)
    store.register_key("groq", "key_b", "general_purpose", None, {}, quota_scope=scope)
    a = store.get_keys_by_quota_scope(scope)[0]
    store.record_usage(a["id"], tokens=150, was_429=False)
    for k in store.get_keys_by_quota_scope(scope):
        assert k["tokens_used_today"] == 150
        assert k["requests_today"] == 1
