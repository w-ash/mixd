"""Shared timing and logging envelope for read-side query use cases.

Provides a reusable async context manager that owns ExecutionTimer management
and error/success logging, eliminating duplication across query modules.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from src.application.utilities.timing import ExecutionTimer
from src.config import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def timed_query(
    operation_name: str,
    *,
    error_log_context: dict[str, object] | None = None,
) -> AsyncGenerator[ExecutionTimer]:
    """Async context manager for timed query operations with error envelope.

    Manages ExecutionTimer and exception logging for read-side use cases.
    Caller is responsible for logging operation start and success; this
    context manager handles consistent error logging on exceptions.

    Args:
        operation_name: Human-readable operation name for error logging
            (e.g., "Liked tracks retrieval" or "Canonical playlist read").
        error_log_context: Optional dict of fields to include in error log
            (e.g., {"playlist_id": "123", "connector_filter": "spotify"}).

    Yields:
        ExecutionTimer instance for measuring operation duration.

    Raises:
        Re-raises any exception that occurs in the context after logging.

    Example:
        >>> ctx = {"filter": "spotify"}
        >>> async with timed_query("Tracks retrieval", error_log_context=ctx) as timer:
        ...     result = await repo.get_tracks(limit=100)
        ...     execution_time_ms = timer.stop()
    """
    timer = ExecutionTimer()
    error_ctx = error_log_context or {}

    try:
        yield timer
    except Exception as e:
        logger.error(
            f"{operation_name} failed",
            error=str(e),
            **error_ctx,
        )
        raise
