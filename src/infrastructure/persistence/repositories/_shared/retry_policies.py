"""Tenacity retry policies for the persistence layer.

Sibling of ``connectors/_shared/retry_policies.py``, kept separate on purpose:
that module is built on ``ErrorClassifier`` + httpx2 exception types + per-service
``settings.api.*`` tuning, none of which exist for a database call. The retryable
condition here is a SQLAlchemy session-state error, and the backoff is a single
short fixed pause rather than exponential-with-jitter — reusing ``RetryConfig``
would mean supplying a fake classifier and a fake service name to disable
everything it does. So the persistence subsystem gets its own named policy in its
own ``_shared`` package, which is also the only import direction the layer rules
allow (persistence must not import from ``connectors/``).

Policy inventory:

- :func:`concurrent_session_retry` — one retry after 0.1s when a coroutine trips
  SQLAlchemy's "concurrent operations are not permitted" guard on an
  ``AsyncSession``.
"""

from typing import Final

from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_fixed,
)

from src.config import get_logger

logger = get_logger(__name__).bind(service="persistence_retry")

# SQLAlchemy raises this (as IllegalStateChangeError, a subclass of
# InvalidRequestError) when two coroutines drive the same AsyncSession at once.
# Matched on the message rather than the type because the type also covers
# unrelated illegal state changes that must NOT be retried.
CONCURRENT_SESSION_MARKER: Final = "concurrent operations are not permitted"

#: Total attempts, including the first — one initial call plus one retry.
CONCURRENT_SESSION_ATTEMPTS: Final = 2

#: Fixed pause before the retry. Deliberately no jitter or exponential growth:
#: the contending coroutine either released the session within a tick or the
#: retry is not going to help, and a longer wait just stalls the request.
CONCURRENT_SESSION_WAIT_SECONDS: Final = 0.1


def is_concurrent_session_error(exc: BaseException) -> bool:
    """Is this the SQLAlchemy "session already in use" error?

    ``BaseException`` subclasses that are not ``Exception`` (``CancelledError``,
    ``KeyboardInterrupt``) are never retried.
    """
    return isinstance(exc, Exception) and CONCURRENT_SESSION_MARKER in str(exc)


def _log_concurrent_session_retry(retry_state: RetryCallState) -> None:
    """Warn before the retry sleeps, mirroring tenacity's rich retry state."""
    logger.warning(
        "Detected concurrent session access, retrying operation",
        attempt=retry_state.attempt_number,
        wait_time=retry_state.idle_for,
    )


def concurrent_session_retry() -> AsyncRetrying:
    """Build the concurrent-session retry policy.

    Returns a fresh ``AsyncRetrying`` per call: the instance carries per-run
    statistics, so sharing one across concurrent repository calls would have
    them overwrite each other.

    ``reraise=True`` keeps the terminal behaviour of a plain call — the last
    database exception surfaces to the caller, not tenacity's ``RetryError``.
    """
    return AsyncRetrying(
        stop=stop_after_attempt(CONCURRENT_SESSION_ATTEMPTS),
        wait=wait_fixed(CONCURRENT_SESSION_WAIT_SECONDS),
        retry=retry_if_exception(is_concurrent_session_error),
        before_sleep=_log_concurrent_session_retry,
        reraise=True,
    )
