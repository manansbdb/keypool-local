"""Offline rotation pattern: AAAAA then BBBBB with rotate_every=5."""
from __future__ import annotations

from llm_keypool.key_store import KeyStore
from llm_keypool.rotator import Rotator

CONFIGS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "openai_compatible": True,
        "free_tier": True,
        "limits": {"rpd": 100},
        "cooldown_fallback": {"strategy": "rolling_60"},
        "default_model": "llama-a",
        "models": ["llama-a"],
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "openai_compatible": True,
        "free_tier": True,
        "limits": {"rpd": 100},
        "cooldown_fallback": {"strategy": "rolling_60"},
        "default_model": "mistral-b",
        "models": ["mistral-b"],
    },
}


def test_rotate_every_5_aaaaa_bbbbb(tmp_path):
    store = KeyStore(db_path=tmp_path / "rot.db")
    # Equal scores → deterministic by id order
    store.register_key("groq", "key-AAAAA", cost_tier="free")
    store.register_key("mistral", "key-BBBBB", cost_tier="free")
    rot = Rotator(store, CONFIGS, rotate_every=5)

    providers = []
    for _ in range(10):
        k = rot.get_best_key("general_purpose", reserve=False)
        assert k is not None
        rot.mark_dispatched(k)
        providers.append(k["provider"])
        rot.handle_success(k["key_id"], tokens_used=1)

    # First 5 same key, next 5 the other (round-robin rotate_every=5)
    assert providers[:5] == [providers[0]] * 5
    assert providers[5:10] == [providers[5]] * 5
    assert providers[0] != providers[5]
