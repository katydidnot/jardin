"""
core/resilience.py — retry decorator for Anthropic API calls
=============================================================

Provides ``@with_retry``, a decorator factory that wraps **async** functions
with tenacity-backed exponential backoff.

Retry conditions
----------------
* ``anthropic.RateLimitError``  (HTTP 429) — back off and retry; these are
  transient and almost always succeed after a short wait.
* ``anthropic.APIConnectionError`` — transient network failures.

All other exceptions are logged at ERROR level and re-raised immediately
(no retry), so bugs don't silently burn through your retry budget.

Usage::

    from core.resilience import with_retry

    @with_retry(max_attempts=3, backoff=2.0)
    async def _run_scoring_agent(...) -> list[dict]:
        ...

    # Or wrap a call site inline:
    call = with_retry(max_attempts=2)(some_async_fn)
    result = await call(arg1, arg2)

The decorator is async-only — don't apply it to sync functions.

Tenacity note
-------------
tenacity >= 8.0 natively handles ``async def`` functions when the decorated
function is awaited.  ``tenacity==9.1.4`` is pinned in requirements.txt.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any, Callable, TypeVar

import anthropic
from tenacity import (
    RetryCallState,
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exception types that warrant a retry
# ---------------------------------------------------------------------------

_RETRYABLE = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,
)

# langchain_anthropic sometimes wraps Anthropic errors in a generic Exception.
# We detect those by inspecting the cause chain.
_RETRYABLE_MESSAGES = ("rate_limit", "rate limit", "429", "connection")


def _is_retryable(exc: BaseException) -> bool:
    """
    Return True if *exc* is retryable — either a known Anthropic exception or
    an exception whose cause chain contains one.
    """
    if isinstance(exc, _RETRYABLE):
        return True

    # Walk the cause chain (langchain wrappers can hide the original)
    cause = exc.__cause__ or exc.__context__
    if cause is not None and isinstance(cause, _RETRYABLE):
        return True

    # Heuristic: check message text for rate-limit indicators
    msg = str(exc).lower()
    return any(marker in msg for marker in _RETRYABLE_MESSAGES)


# ---------------------------------------------------------------------------
# Before-sleep callback — logs each retry attempt with context
# ---------------------------------------------------------------------------

def _before_sleep(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception()  # type: ignore[union-attr]
    fn_name = getattr(retry_state.fn, "__name__", "unknown")
    logger.warning(
        "Retrying %s (attempt %d/%d) after %.1fs — %s: %s",
        fn_name,
        retry_state.attempt_number,
        retry_state.retry_object.stop.max_attempt_number,  # type: ignore[attr-defined]
        retry_state.next_action.sleep,  # type: ignore[union-attr]
        type(exc).__name__ if exc else "?",
        exc,
    )


# ---------------------------------------------------------------------------
# Decorator factory
# ---------------------------------------------------------------------------

F = TypeVar("F", bound=Callable[..., Any])


def with_retry(
    max_attempts: int = 3,
    backoff: float = 2.0,
    min_wait: float = 1.0,
    max_wait: float = 60.0,
) -> Callable[[F], F]:
    """
    Decorator factory for async Anthropic API calls.

    Parameters
    ----------
    max_attempts:
        Maximum total attempts (including the initial one).
    backoff:
        Exponential multiplier between retries (seconds).  Actual wait time
        is ``backoff * 2^(attempt - 1)``, capped at *max_wait*.
    min_wait:
        Minimum wait time before the first retry (seconds).
    max_wait:
        Maximum wait time between retries (seconds).

    Example
    -------
    ::

        @with_retry(max_attempts=3, backoff=2.0)
        async def call_claude(...):
            return await llm.ainvoke(messages)
    """
    def decorator(fn: F) -> F:
        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=backoff, min=min_wait, max=max_wait),
            retry=retry_if_exception_type(_RETRYABLE),
            before_sleep=_before_sleep,
            reraise=True,
        )
        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:
                # If the exception is retryable but wrapped (e.g. by langchain),
                # convert it so tenacity can detect it and apply the retry policy.
                if _is_retryable(exc) and not isinstance(exc, _RETRYABLE):
                    logger.debug(
                        "Re-raising wrapped retryable error as RateLimitError: %s",
                        exc,
                    )
                    raise anthropic.RateLimitError(
                        message=str(exc),
                        response=getattr(exc, "response", None),  # type: ignore[arg-type]
                        body=None,
                    ) from exc
                raise

        return async_wrapper  # type: ignore[return-value]

    return decorator
