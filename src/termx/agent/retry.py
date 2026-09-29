"""Bounded provider-request retry with failure classification.

Classification is deliberately conservative: only transient transport and
server-side conditions are retried. Auth failures, malformed requests,
invalid models and schema rejections fail immediately, and nothing in this
module ever retries after a tool side effect has been dispatched — it wraps
only the provider call itself. No payload, header or secret is reflected in
emitted events; payloads carry reason class + status code + delay only.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeVar

import httpx

from termx.agent.providers import ProviderError

T = TypeVar("T")

MAX_ATTEMPTS = 3
_BASE_DELAY_S = 0.5
_MAX_DELAY_S = 8.0
_RETRY_AFTER_CAP_S = 30.0

_RETRYABLE_STATUSES = {408, 500, 502, 503, 504}
_RATE_LIMITED_STATUSES = {429}


@dataclass(frozen=True)
class FailureClass:
    kind: str  # "retryable" | "rate_limited" | "non_retryable"
    status_code: int | None = None
    retry_after_s: float | None = None


def classify(exc: BaseException) -> FailureClass:
    """Map a provider failure to a retry decision class."""
    status = getattr(exc, "status_code", None)
    retry_after = getattr(exc, "retry_after_s", None)
    if isinstance(exc, ProviderError):
        if exc.network or status is None and exc.__cause__ is not None:
            # Transport failure (DNS, reset, timeout) wrapped into ProviderError.
            return FailureClass("retryable")
        if status in _RATE_LIMITED_STATUSES:
            return FailureClass(
                "rate_limited",
                status_code=status,
                retry_after_s=min(_RETRY_AFTER_CAP_S, float(retry_after or _BASE_DELAY_S)),
            )
        if status in _RETRYABLE_STATUSES:
            return FailureClass("retryable", status_code=status)
        return FailureClass("non_retryable", status_code=status)
    if isinstance(exc, (httpx.RequestError, OSError)):
        return FailureClass("retryable")
    return FailureClass("non_retryable")


def _delay_s(attempt: int, classification: FailureClass) -> float:
    if classification.retry_after_s is not None:
        return max(0.0, classification.retry_after_s)
    base = min(_MAX_DELAY_S, _BASE_DELAY_S * (2 ** (attempt - 1)))
    return base * (1 + random.uniform(0, 0.2))


async def _sleep(delay_s: float, cancel: asyncio.Event) -> None:
    """Cancel-aware sleep: raises CancelledError as soon as cancel is set."""
    sleeper = asyncio.ensure_future(asyncio.sleep(delay_s))
    watcher = asyncio.ensure_future(cancel.wait())
    try:
        await asyncio.wait({sleeper, watcher}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        sleeper.cancel()
        watcher.cancel()
    if cancel.is_set():
        raise asyncio.CancelledError


async def retrying(
    factory: Callable[[], Awaitable[T]],
    *,
    emit: Callable[[str, dict[str, Any]], None] | None = None,
    cancel: asyncio.Event | None = None,
    max_attempts: int = MAX_ATTEMPTS,
    may_retry: Callable[[], bool] | None = None,
) -> T:
    """Run ``factory`` with bounded retry/backoff, emitting retry events.

    ``factory`` is re-invoked per attempt; the callable itself must be free of
    side effects other than the provider request. ``may_retry`` is consulted
    after each failure — returning False suppresses the retry (e.g. a stream
    that already emitted visible deltas must not be replayed transparently).
    Events:
    ``provider.retry {attempt, max_attempts, reason_class, status_code?, delay_ms}``,
    ``provider.rate_limited {status_code, delay_ms}``,
    ``provider.failed {reason_class, status_code?, retry_suppressed?}``.
    """
    cancel = cancel or asyncio.Event()
    attempt = 0
    while True:
        attempt += 1
        try:
            return await factory()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            classification = classify(exc)
            suppressed = may_retry is not None and not may_retry()
            if classification.kind == "non_retryable" or attempt >= max_attempts or suppressed:
                if emit is not None:
                    payload = {
                        "reason_class": classification.kind,
                        "status_code": classification.status_code,
                        "attempt": attempt,
                    }
                    if suppressed:
                        payload["retry_suppressed"] = True
                    emit("provider.failed", payload)
                raise
            delay_s = _delay_s(attempt, classification)
            if emit is not None:
                payload = {
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "reason_class": classification.kind,
                    "status_code": classification.status_code,
                    "delay_ms": int(delay_s * 1000),
                }
                emit("provider.retry", payload)
                if classification.kind == "rate_limited":
                    emit(
                        "provider.rate_limited",
                        {"status_code": classification.status_code, "delay_ms": int(delay_s * 1000)},
                    )
            await _sleep(delay_s, cancel)
