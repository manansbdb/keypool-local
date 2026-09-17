"""Central dispatch: select key, call provider, classify errors, retry other routes."""
from __future__ import annotations

import time
from .base import CompletionResult
from . import openai_compat, cohere as _cohere, cloudflare as _cloudflare
from ..errors import (
    ErrorKind,
    classify_http_status,
    is_recoverable_provider_error,
    make_error,
)

MAX_RETRY_ATTEMPTS = 10


def _parse_status_from_error(err: str | None) -> int | None:
    if not err:
        return None
    if err.startswith("HTTP "):
        parts = err.split()
        if len(parts) >= 2 and parts[1].rstrip(":").isdigit():
            return int(parts[1].rstrip(":"))
    if "429" in err:
        return 429
    return None


async def complete(
    rotator,
    capabilities: list[str] | None = None,
    messages: list[dict] | None = None,
    subscriber_id: str = "unknown",
    category: str | None = None,
    model: str | None = None,
    tools: list | None = None,
    **kwargs,
) -> tuple[CompletionResult, dict | None]:
    if capabilities is None:
        capabilities = [category] if category else ["general_purpose"]
    messages = messages or []
    if hasattr(rotator, "begin_request"):
        rotator.begin_request()

    peek = rotator.store.get_all_keys()
    if not any(k["is_active"] for k in peek):
        err = make_error(ErrorKind.POOL_UNCONFIGURED, "pool_unconfigured: no active keys")
        return CompletionResult(
            text="", tokens_used=0, was_429=False, error=err.message,
            error_kind=err.kind.value,
        ), None

    for _ in range(MAX_RETRY_ATTEMPTS):
        try:
            key_data = rotator.get_best_key(
                capabilities,
                subscriber_id=subscriber_id,
                model=model,
                reserve=True,
            )
        except TypeError:
            key_data = rotator.get_best_key(capabilities, subscriber_id=subscriber_id)

        if not key_data:
            earliest = rotator.get_earliest_retry(capabilities)
            kind = ErrorKind.ALL_LIMITED if earliest else ErrorKind.NO_ROUTE
            err = make_error(kind, "all_keys_limited" if earliest else "all_keys_exhausted")
            return CompletionResult(
                text="", tokens_used=0, was_429=False,
                error=err.message, error_kind=err.kind.value,
            ), None

        call_kwargs = dict(kwargs)
        if tools is not None:
            call_kwargs["tools"] = tools
        if model and model not in ("any", "keypool-auto"):
            call_kwargs["model"] = model

        try:
            if hasattr(rotator, "mark_dispatched"):
                rotator.mark_dispatched(key_data)
            t0 = time.monotonic()
            result = await _call_complete(key_data, messages, **call_kwargs)
            latency_ms = int((time.monotonic() - t0) * 1000)

            if result.was_429:
                rotator.handle_429(
                    key_data["key_id"],
                    key_data["provider"],
                    result.rate_limit_headers,
                    subscriber_id=subscriber_id,
                    model=key_data.get("model", ""),
                    **({"quota_scope": key_data.get("quota_scope")}
                       if "quota_scope" in rotator.handle_429.__code__.co_varnames else {}),
                )
                continue

            if result.error:
                status = _parse_status_from_error(result.error)
                kind = classify_http_status(status or 500, result.error)
                result.error_kind = kind.value
                if is_recoverable_provider_error(kind):
                    if hasattr(rotator, "block_scope_for_request"):
                        rotator.block_scope_for_request(
                            key_data.get("quota_scope"), key_data["key_id"]
                        )
                    rotator.store.log_audit(
                        subscriber_id=subscriber_id,
                        key_id=key_data["key_id"],
                        provider=key_data["provider"],
                        model=key_data.get("model", ""),
                        success=False,
                        error=result.error[:200],
                        event=kind.value,
                    )
                    continue
                return result, key_data

            tokens_in = getattr(result, "tokens_in", None)
            if tokens_in is None:
                tokens_in = sum(len(str(m.get("content", ""))) // 4 for m in messages)
                result.usage_unknown = True

            rotator.handle_success(
                key_data["key_id"],
                result.tokens_used,
                result.rate_limit_headers,
                key_data["provider"],
                tokens_in=tokens_in or 0,
                latency_ms=latency_ms,
                subscriber_id=subscriber_id,
                model=key_data.get("model", ""),
            )
            return result, key_data
        finally:
            if hasattr(rotator, "release"):
                rotator.release(key_data)

    err = make_error(ErrorKind.ALL_LIMITED, "max_retries_exceeded")
    return CompletionResult(
        text="", tokens_used=0, was_429=False,
        error=err.message, error_kind=err.kind.value,
    ), None


async def _call_complete(key_data: dict, messages: list[dict], **kwargs) -> CompletionResult:
    if key_data["openai_compatible"]:
        return await openai_compat.complete(key_data, messages, **kwargs)
    if key_data["provider"] == "cohere":
        return await _cohere.complete(key_data, messages, **kwargs)
    if key_data["provider"] == "cloudflare":
        return await _cloudflare.complete(key_data, messages, **kwargs)
    return CompletionResult(
        text="", tokens_used=0, was_429=False,
        error=f"no client for provider '{key_data['provider']}'",
        error_kind=ErrorKind.FATAL.value,
    )


async def stream_complete(
    rotator,
    capabilities: list[str] | None = None,
    messages: list[dict] | None = None,
    subscriber_id: str = "unknown",
    model: str | None = None,
    tools: list | None = None,
    **kwargs,
):
    """Async generator: ('meta'|'delta'|'usage'|'error'|'done', payload). No mid-stream switch."""
    if capabilities is None:
        capabilities = ["general_purpose"]
    messages = messages or []
    if hasattr(rotator, "begin_request"):
        rotator.begin_request()

    try:
        key_data = rotator.get_best_key(
            capabilities, subscriber_id=subscriber_id, model=model, reserve=True
        )
    except TypeError:
        key_data = rotator.get_best_key(capabilities, subscriber_id=subscriber_id)

    if not key_data:
        earliest = rotator.get_earliest_retry(capabilities)
        kind = ErrorKind.ALL_LIMITED if earliest else ErrorKind.POOL_UNCONFIGURED
        err = make_error(kind, "all_keys_limited" if earliest else "all_keys_exhausted")
        yield ("error", {"error": err.message, "error_kind": err.kind.value,
                         "http_status": err.http_status})
        return

    call_kwargs = dict(kwargs)
    if tools is not None:
        call_kwargs["tools"] = tools
    if model and model not in ("any", "keypool-auto"):
        call_kwargs["model"] = model

    try:
        if hasattr(rotator, "mark_dispatched"):
            rotator.mark_dispatched(key_data)
        yield ("meta", {
            "provider": key_data["provider"],
            "model": key_data["model"],
            "key_id": key_data["key_id"],
            "cost_tier": key_data.get("cost_tier"),
        })

        if not key_data["openai_compatible"] or not hasattr(openai_compat, "stream"):
            result = await _call_complete(key_data, messages, **call_kwargs)
            if result.was_429:
                kwargs429 = {}
                if "quota_scope" in rotator.handle_429.__code__.co_varnames:
                    kwargs429["quota_scope"] = key_data.get("quota_scope")
                rotator.handle_429(
                    key_data["key_id"], key_data["provider"],
                    result.rate_limit_headers, subscriber_id=subscriber_id,
                    model=key_data.get("model", ""), **kwargs429,
                )
                yield ("error", {"error": "429", "error_kind": "rate_limited", "http_status": 429})
                return
            if result.error:
                yield ("error", {"error": result.error, "http_status": 502})
                return
            yield ("delta", {"content": result.text})
            yield ("usage", {
                "prompt_tokens": getattr(result, "tokens_in", None),
                "completion_tokens": result.tokens_used,
                "total_tokens": result.tokens_used,
                "usage_unknown": getattr(result, "usage_unknown", False),
            })
            rotator.handle_success(
                key_data["key_id"], result.tokens_used,
                result.rate_limit_headers, key_data["provider"],
                tokens_in=getattr(result, "tokens_in", None) or 0,
                subscriber_id=subscriber_id, model=key_data.get("model", ""),
            )
            yield ("done", {})
            return

        usage = {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
        t0 = time.monotonic()
        async for event, payload in openai_compat.stream(key_data, messages, **call_kwargs):
            if event == "delta":
                yield ("delta", payload)
            elif event == "usage":
                usage.update(payload)
                yield ("usage", payload)
            elif event == "error":
                if payload.get("was_429"):
                    kwargs429 = {}
                    if "quota_scope" in rotator.handle_429.__code__.co_varnames:
                        kwargs429["quota_scope"] = key_data.get("quota_scope")
                    rotator.handle_429(
                        key_data["key_id"], key_data["provider"],
                        payload.get("headers") or {},
                        subscriber_id=subscriber_id,
                        model=key_data.get("model", ""), **kwargs429,
                    )
                yield ("error", payload)
                return
        latency_ms = int((time.monotonic() - t0) * 1000)
        tokens = usage.get("total_tokens") or 0
        tokens_in = usage.get("prompt_tokens") or 0
        rotator.handle_success(
            key_data["key_id"], tokens or 0, {},
            key_data["provider"],
            tokens_in=tokens_in,
            latency_ms=latency_ms,
            subscriber_id=subscriber_id,
            model=key_data.get("model", ""),
        )
        if usage.get("total_tokens") is None:
            yield ("usage", {"usage_unknown": True})
        yield ("done", {})
    finally:
        if hasattr(rotator, "release"):
            rotator.release(key_data)
