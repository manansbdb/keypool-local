"""OpenAI-compatible local proxy with auth, real SSE, and audit endpoints."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from llm_keypool.key_store import KeyStore
from llm_keypool.rotator import Rotator
from llm_keypool.providers.dispatch import complete, stream_complete
from llm_keypool.settings import (
    proxy_auth_token,
    proxy_require_auth,
    legacy_ignore_model,
    rotate_every_default,
)


def _load_provider_configs() -> dict:
    config_path = Path(__file__).parent / "config" / "providers.json"
    with open(config_path) as f:
        return json.load(f)["providers"]


class _ChatRequest(BaseModel):
    model: Optional[str] = None
    messages: list[dict[str, Any]]
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    stream: Optional[bool] = False
    tools: Optional[list[dict[str, Any]]] = None
    tool_choice: Optional[Any] = None


def _check_auth(authorization: Optional[str]) -> None:
    if not proxy_require_auth():
        return
    expected = proxy_auth_token()
    if not expected:
        raise HTTPException(
            status_code=401,
            detail="auth_required: set LLM_KEYPOOL_PROXY_TOKEN",
        )
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="auth_required")
    got = authorization.split(" ", 1)[1].strip()
    if got != expected:
        raise HTTPException(status_code=401, detail="auth_failed")


def make_app(
    capabilities: list[str] | None = None,
    rotate_every: int | None = None,
    category: str | None = None,
    store: KeyStore | None = None,
) -> FastAPI:
    if capabilities is None:
        capabilities = [category] if category else ["general_purpose"]
    if rotate_every is None:
        rotate_every = rotate_every_default()

    store = store or KeyStore()
    configs = _load_provider_configs()
    rotator = Rotator(store, configs, rotate_every=rotate_every)

    app = FastAPI(title="KeyPool Local proxy", version="0.5.0")

    def _resolve_caps(
        x_keypool_capabilities: Optional[str],
        x_keypool_category: Optional[str],
    ) -> list[str]:
        if x_keypool_capabilities:
            return [c.strip() for c in x_keypool_capabilities.split(",") if c.strip()]
        if x_keypool_category:
            return [x_keypool_category.strip()]
        return capabilities

    @app.post("/v1/chat/completions")
    async def chat_completions(
        req: _ChatRequest,
        request: Request,
        authorization: Optional[str] = Header(None),
        x_keypool_capabilities: Optional[str] = Header(None),
        x_keypool_category: Optional[str] = Header(None),
        x_subscriber_id: Optional[str] = Header(None),
    ):
        _check_auth(authorization)
        caps = _resolve_caps(x_keypool_capabilities, x_keypool_category)
        subscriber = x_subscriber_id or "proxy"

        kwargs: dict[str, Any] = {}
        if req.max_tokens is not None:
            kwargs["max_tokens"] = req.max_tokens
        if req.temperature is not None:
            kwargs["temperature"] = req.temperature
        if req.tool_choice is not None:
            kwargs["tool_choice"] = req.tool_choice

        req_model = req.model
        if legacy_ignore_model():
            req_model = None

        if req.stream:
            return await _stream_response(
                request, rotator, caps, req, subscriber, req_model, kwargs
            )

        result, key_data = await complete(
            rotator,
            capabilities=caps,
            messages=req.messages,
            subscriber_id=subscriber,
            model=req_model,
            tools=req.tools,
            **kwargs,
        )

        if result.error and not result.text:
            detail = result.error
            status = 503
            if "limited" in (detail or ""):
                status = 429
            if "unconfigured" in (detail or ""):
                status = 503
            if "exhausted" in (detail or ""):
                status = 503
            raise HTTPException(status_code=status, detail=detail)

        model_used = (key_data["model"] if key_data else None) or req.model or "unknown"
        if req_model and req_model not in ("any", "keypool-auto") and key_data:
            if not legacy_ignore_model():
                model_used = req_model
        provider_used = key_data["provider"] if key_data else "unknown"
        resp_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())

        prompt_tokens = getattr(result, "tokens_in", None)
        completion_tokens = (
            result.tokens_out if getattr(result, "tokens_out", None) is not None
            else result.tokens_used
        )
        usage = {
            "prompt_tokens": prompt_tokens if prompt_tokens is not None else 0,
            "completion_tokens": completion_tokens or 0,
            "total_tokens": result.tokens_used or 0,
        }
        if getattr(result, "usage_unknown", False) or prompt_tokens is None:
            usage["usage_unknown"] = True

        headers = {
            "X-Key-Provider": provider_used,
            "X-Key-Id": str(key_data["key_id"]) if key_data else "",
            "X-Key-Cost-Tier": (key_data or {}).get("cost_tier", ""),
        }
        body = {
            "id": resp_id,
            "object": "chat.completion",
            "created": created,
            "model": model_used,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": result.text},
                "finish_reason": "stop",
            }],
            "usage": usage,
            "x_key_provider": provider_used,
        }
        return JSONResponse(content=body, headers=headers)

    async def _stream_response(request, rotator, caps, req, subscriber, req_model, kwargs):
        resp_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())
        provider_holder = {"provider": "unknown", "model": req_model or "unknown"}

        async def _gen():
            try:
                async for event, payload in stream_complete(
                    rotator,
                    capabilities=caps,
                    messages=req.messages,
                    subscriber_id=subscriber,
                    model=req_model,
                    tools=req.tools,
                    **kwargs,
                ):
                    if await request.is_disconnected():
                        return
                    if event == "meta":
                        provider_holder["provider"] = payload.get(
                            "provider", provider_holder["provider"]
                        )
                        provider_holder["model"] = payload.get(
                            "model", provider_holder["model"]
                        )
                        continue
                    model_used = provider_holder["model"]
                    if event == "delta":
                        chunk = {
                            "id": resp_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model_used,
                            "choices": [{
                                "index": 0,
                                "delta": {
                                    "role": "assistant",
                                    "content": payload.get("content", ""),
                                },
                                "finish_reason": None,
                            }],
                        }
                        yield f"data: {json.dumps(chunk)}\n\n"
                    elif event == "error":
                        yield f"data: {json.dumps({'id': resp_id, 'object': 'error', 'error': payload})}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                    elif event == "done":
                        done_chunk = {
                            "id": resp_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model_used,
                            "choices": [{
                                "index": 0,
                                "delta": {},
                                "finish_reason": "stop",
                            }],
                        }
                        yield f"data: {json.dumps(done_chunk)}\n\n"
                        yield "data: [DONE]\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)[:200]})}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            _gen(),
            media_type="text/event-stream",
            headers={
                "X-Key-Provider": provider_holder["provider"],
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/v1/models")
    async def list_models(authorization: Optional[str] = Header(None)):
        _check_auth(authorization)
        seen: set[str] = set()
        data = []
        for alias in ("any", "keypool-auto"):
            data.append({
                "id": alias, "object": "model",
                "owned_by": "keypool-local", "created": 0,
            })
            seen.add(alias)
        for provider_name, cfg in configs.items():
            models = cfg.get("models", [])
            if isinstance(models, dict):
                models = [m for ms in models.values() for m in ms]
            default = cfg.get("default_model")
            if default and default not in models:
                models = [default] + list(models)
            for m in models:
                if m and m not in seen:
                    seen.add(m)
                    data.append({
                        "id": m, "object": "model",
                        "owned_by": provider_name, "created": 0,
                    })
        return {"object": "list", "data": data}

    @app.get("/health")
    async def health():
        keys = store.get_all_keys(reveal=False)
        active = sum(1 for k in keys if k["is_active"])
        return {
            "status": "ok",
            "keys_total": len(keys),
            "keys_active": active,
            "capabilities": capabilities,
            "free_only": os.environ.get("LLM_KEYPOOL_FREE_ONLY", "true"),
        }

    @app.get("/audit")
    async def audit_summary(
        days: int = 7,
        authorization: Optional[str] = Header(None),
    ):
        _check_auth(authorization)
        return store.get_audit_summary(days=days)

    return app
