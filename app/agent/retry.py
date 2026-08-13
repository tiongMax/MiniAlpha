"""Bounded retry policies for read-only agent dependencies."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

_TRANSIENT_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
_TRANSIENT_ERROR_NAMES = frozenset(
    {
        "deadlineexceeded",
        "internalservererror",
        "resourceexhausted",
        "serviceunavailable",
        "toomanyrequests",
    }
)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Small, explicit retry budget with injectable sleeping."""

    max_attempts: int = 2
    base_delay_seconds: float = 0.2

    def __post_init__(self) -> None:
        if self.max_attempts <= 0 or self.base_delay_seconds < 0:
            raise ValueError("Retry attempts must be positive and delay non-negative.")

    def delay(self, completed_attempt: int) -> float:
        """Return exponential backoff after one failed attempt."""
        return self.base_delay_seconds * (2 ** max(completed_attempt - 1, 0))


def is_transient_model_error(error: Exception) -> bool:
    """Classify model transport failures without depending on one SDK.

    Gemini errors have changed class locations across client versions.  The
    classifier therefore uses stable exception names and numeric HTTP status
    attributes, never exception messages that may contain request data.
    """
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True
    if type(error).__name__.casefold() in _TRANSIENT_ERROR_NAMES:
        return True

    candidates = [getattr(error, "status_code", None), getattr(error, "code", None)]
    response = getattr(error, "response", None)
    candidates.append(getattr(response, "status_code", None))
    for candidate in candidates:
        if callable(candidate):
            try:
                candidate = candidate()
            except Exception:
                continue
        candidate = getattr(candidate, "value", candidate)
        try:
            if int(candidate) in _TRANSIENT_STATUS_CODES:
                return True
        except (TypeError, ValueError):
            continue
    return False


async def retry_async[T](
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    retryable: Callable[[Exception], bool],
    sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Run an async operation with a bounded, classifier-driven retry."""
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await operation()
        except Exception as error:
            if attempt >= policy.max_attempts or not retryable(error):
                raise
            await sleeper(policy.delay(attempt))
    raise AssertionError("unreachable retry state")
