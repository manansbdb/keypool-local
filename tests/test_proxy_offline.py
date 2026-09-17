"""Offline proxy/SSE/auth tests with mocked providers (no live API)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from llm_keypool.key_store import KeyStore
from llm_keypool.providers.base import CompletionResult
from llm_keypool.proxy import make_app


@pytest.fixture
def db(tmp_path, monkeypatch):
    db = tmp_path / "proxy.db"
    monkeypatch.setenv("LLM_KEYPOOL_DB", str(db))
    monkeypatch.setenv("LLM_KEYPOOL_PROXY_TOKEN", "test-token")
    monkeypatch.setenv("LLM_KEYPOOL_PROXY_REQUIRE_AUTH", "true")
    monkeypatch.setenv("LLM_KEYPOOL_FREE_ONLY", "true")
    monkeypatch.setenv("LLM_KEYPOOL_ALLOW_PAID_FALLBACK", "false")
    return db


@pytest.fixture
def store(db):
    return KeyStore(db_path=db)


@pytest.mark.asyncio
async def test_auth_fail_no_token(store, monkeypatch):
    store.register_key("groq", "secret-key-aaa", cost_tier="free")
    app = make_app(store=store)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_auth_fail_bad_token(store):
    store.register_key("groq", "secret-key-aaa", cost_tier="free")
    app = make_app(store=store)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer wrong"},
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_health_unauthenticated(store):
    app = make_app(store=store)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_streaming_first_chunk_before_done(store):
    store.register_key("groq", "secret-key-aaa", model="llama-3.3-70b-versatile", cost_tier="free")

    async def fake_stream(*args, **kwargs):
        yield ("meta", {"provider": "groq", "model": "llama-3.3-70b-versatile", "key_id": 1})
        yield ("delta", {"content": "Hello"})
        yield ("delta", {"content": " world"})
        yield ("done", {})

    app = make_app(store=store)
    transport = ASGITransport(app=app)
    with patch("llm_keypool.proxy.stream_complete", fake_stream):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST",
                "/v1/chat/completions",
                headers={"Authorization": "Bearer test-token"},
                json={
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                },
            ) as r:
                assert r.status_code == 200
                body = ""
                async for chunk in r.aiter_text():
                    body += chunk
    assert "Hello" in body
    assert "[DONE]" in body
    assert body.index("Hello") < body.index("[DONE]")


@pytest.mark.asyncio
async def test_no_silent_model_swap(store):
    store.register_key(
        "groq", "secret-key-aaa", model="llama-3.1-8b-instant", cost_tier="free"
    )

    async def fake_complete(*args, **kwargs):
        return (
            CompletionResult(text="ok", tokens_used=3, was_429=False, tokens_in=1, tokens_out=2),
            {
                "key_id": 1,
                "provider": "groq",
                "model": kwargs.get("model") or "llama-3.1-8b-instant",
                "cost_tier": "free",
            },
        )

    app = make_app(store=store)
    transport = ASGITransport(app=app)
    with patch("llm_keypool.proxy.complete", side_effect=fake_complete):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer test-token"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
    assert r.status_code == 200
    data = r.json()
    assert data["model"] == "llama-3.1-8b-instant"
    # headers must not leak secrets
    assert "secret-key" not in r.headers.get("X-Key-Provider", "")
    assert r.headers.get("X-Key-Provider") == "groq"


@pytest.mark.asyncio
async def test_pool_unconfigured_503(store):
    app = make_app(store=store)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer test-token"},
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
    assert r.status_code in (503, 429)
