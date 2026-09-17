"""OpenAI-compatible provider client (complete + real SSE stream)."""
from __future__ import annotations

import re
from openai import AsyncOpenAI, RateLimitError, APIStatusError, APIConnectionError
from .base import CompletionResult
from .headers import collect_rl_headers, extract_remaining_requests

_THINK_CLOSED_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_THINK_OPEN_RE = re.compile(r"<think>.*$", re.DOTALL)


def _strip_thinking(text: str) -> str:
    text = _THINK_CLOSED_RE.sub("", text)
    text = _THINK_OPEN_RE.sub("", text)
    return text.strip()


async def complete(key_data: dict, messages: list[dict], **kwargs) -> CompletionResult:
    strip_thinking = kwargs.pop("strip_thinking", True)
    model = kwargs.pop("model", None) or key_data["model"]
    provider = key_data.get("provider", "")
    client = AsyncOpenAI(base_url=key_data["base_url"], api_key=key_data["api_key"])
    try:
        raw = await client.chat.completions.with_raw_response.create(
            model=model,
            messages=messages,
            **kwargs,
        )
        resp = raw.parse()
        rl_headers = collect_rl_headers(raw.headers)
        remaining = extract_remaining_requests(provider, rl_headers)
        text = resp.choices[0].message.content or ""
        if strip_thinking:
            text = _strip_thinking(text)
        usage = resp.usage
        tokens_in = usage.prompt_tokens if usage else None
        tokens_out = usage.completion_tokens if usage else None
        total = usage.total_tokens if usage else 0
        return CompletionResult(
            text=text,
            tokens_used=total or ((tokens_in or 0) + (tokens_out or 0)),
            was_429=False,
            remaining_requests=remaining,
            rate_limit_headers=rl_headers,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            usage_unknown=usage is None,
        )
    except RateLimitError as e:
        rl_headers = {}
        if hasattr(e, "response") and e.response is not None:
            rl_headers = collect_rl_headers(e.response.headers)
        return CompletionResult(
            text="", tokens_used=0, was_429=True,
            error=str(e)[:200], rate_limit_headers=rl_headers,
            error_kind="rate_limited",
        )
    except APIStatusError as e:
        return CompletionResult(
            text="", tokens_used=0, was_429=False,
            error=f"HTTP {e.status_code}: {str(e)[:160]}",
            error_kind="provider_5xx" if e.status_code >= 500 else "fatal",
        )
    except APIConnectionError as e:
        return CompletionResult(
            text="", tokens_used=0, was_429=False,
            error=str(e)[:200], error_kind="recoverable",
        )
    except Exception as e:
        return CompletionResult(
            text="", tokens_used=0, was_429=False,
            error=str(e)[:200], error_kind="fatal",
        )


async def stream(key_data: dict, messages: list[dict], **kwargs):
    """Yield ('delta'|'usage'|'error', payload) from real provider SSE."""
    model = kwargs.pop("model", None) or key_data["model"]
    kwargs.pop("strip_thinking", None)
    client = AsyncOpenAI(base_url=key_data["base_url"], api_key=key_data["api_key"])
    try:
        stream_obj = await client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
            stream_options={"include_usage": True},
            **kwargs,
        )
        async for chunk in stream_obj:
            if not chunk.choices and getattr(chunk, "usage", None):
                u = chunk.usage
                yield ("usage", {
                    "prompt_tokens": getattr(u, "prompt_tokens", None),
                    "completion_tokens": getattr(u, "completion_tokens", None),
                    "total_tokens": getattr(u, "total_tokens", None),
                })
                continue
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            content = getattr(delta, "content", None) or ""
            if content:
                yield ("delta", {"content": content})
            if getattr(chunk, "usage", None):
                u = chunk.usage
                yield ("usage", {
                    "prompt_tokens": getattr(u, "prompt_tokens", None),
                    "completion_tokens": getattr(u, "completion_tokens", None),
                    "total_tokens": getattr(u, "total_tokens", None),
                })
    except RateLimitError as e:
        headers = {}
        if hasattr(e, "response") and e.response is not None:
            headers = collect_rl_headers(e.response.headers)
        yield ("error", {"error": str(e)[:200], "was_429": True, "headers": headers,
                         "http_status": 429})
    except APIStatusError as e:
        yield ("error", {"error": f"HTTP {e.status_code}", "was_429": e.status_code == 429,
                         "http_status": e.status_code})
    except Exception as e:
        yield ("error", {"error": str(e)[:200], "http_status": 502})
