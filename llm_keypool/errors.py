"""Central error classification and HTTP/exit code mapping."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ErrorKind(str, Enum):
    POOL_UNCONFIGURED = "pool_unconfigured"
    ALL_LIMITED = "all_limited"
    AUTH_REQUIRED = "auth_required"
    AUTH_FAILED = "auth_failed"
    RATE_LIMITED = "rate_limited"
    UNAUTHORIZED_KEY = "unauthorized_key"
    FORBIDDEN = "forbidden"
    MODEL_UNAVAILABLE = "model_unavailable"
    PAYLOAD_INVALID = "payload_invalid"
    PROVIDER_5XX = "provider_5xx"
    RECOVERABLE = "recoverable"
    FATAL = "fatal"
    BLOCKED_COST = "blocked_cost"
    NO_ROUTE = "no_route"


@dataclass
class ClassifiedError:
    kind: ErrorKind
    message: str
    http_status: int
    exit_code: int
    retryable: bool
    headers: dict | None = None


_HTTP = {
    ErrorKind.POOL_UNCONFIGURED: 503,
    ErrorKind.ALL_LIMITED: 429,
    ErrorKind.AUTH_REQUIRED: 401,
    ErrorKind.AUTH_FAILED: 401,
    ErrorKind.RATE_LIMITED: 429,
    ErrorKind.UNAUTHORIZED_KEY: 401,
    ErrorKind.FORBIDDEN: 403,
    ErrorKind.MODEL_UNAVAILABLE: 404,
    ErrorKind.PAYLOAD_INVALID: 400,
    ErrorKind.PROVIDER_5XX: 502,
    ErrorKind.RECOVERABLE: 503,
    ErrorKind.FATAL: 500,
    ErrorKind.BLOCKED_COST: 403,
    ErrorKind.NO_ROUTE: 503,
}

_EXIT = {
    ErrorKind.POOL_UNCONFIGURED: 10,
    ErrorKind.ALL_LIMITED: 11,
    ErrorKind.AUTH_REQUIRED: 12,
    ErrorKind.AUTH_FAILED: 12,
    ErrorKind.RATE_LIMITED: 11,
    ErrorKind.UNAUTHORIZED_KEY: 13,
    ErrorKind.FORBIDDEN: 13,
    ErrorKind.MODEL_UNAVAILABLE: 14,
    ErrorKind.PAYLOAD_INVALID: 15,
    ErrorKind.PROVIDER_5XX: 16,
    ErrorKind.RECOVERABLE: 16,
    ErrorKind.FATAL: 1,
    ErrorKind.BLOCKED_COST: 17,
    ErrorKind.NO_ROUTE: 10,
}


def classify_http_status(status: int, body_hint: str = "") -> ErrorKind:
    if status == 429:
        return ErrorKind.RATE_LIMITED
    if status == 401:
        return ErrorKind.UNAUTHORIZED_KEY
    if status == 403:
        return ErrorKind.FORBIDDEN
    if status == 404 or "model" in body_hint.lower():
        return ErrorKind.MODEL_UNAVAILABLE
    if status == 400:
        return ErrorKind.PAYLOAD_INVALID
    if 500 <= status <= 599:
        return ErrorKind.PROVIDER_5XX
    return ErrorKind.FATAL


def make_error(
    kind: ErrorKind,
    message: str,
    *,
    retryable: Optional[bool] = None,
    headers: dict | None = None,
) -> ClassifiedError:
    if retryable is None:
        retryable = kind in (
            ErrorKind.RATE_LIMITED,
            ErrorKind.PROVIDER_5XX,
            ErrorKind.RECOVERABLE,
            ErrorKind.ALL_LIMITED,
        )
    return ClassifiedError(
        kind=kind,
        message=message,
        http_status=_HTTP[kind],
        exit_code=_EXIT[kind],
        retryable=retryable,
        headers=headers,
    )


def is_recoverable_provider_error(kind: ErrorKind) -> bool:
    """Retry other routes on these; never retry blocked scope same request."""
    return kind in (
        ErrorKind.RATE_LIMITED,
        ErrorKind.PROVIDER_5XX,
        ErrorKind.RECOVERABLE,
        ErrorKind.UNAUTHORIZED_KEY,  # try another key
    )
