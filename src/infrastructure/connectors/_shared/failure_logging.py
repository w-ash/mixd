"""Structured logging utilities for match failures.

This module provides consistent WARNING-level logging for match failures across
all providers, enabling observability and debugging of matching issues.
"""

from __future__ import annotations

from src.config import get_logger
from src.domain.matching.types import MatchFailure

logger = get_logger(__name__)


def log_match_failure(failure: MatchFailure) -> None:
    """Log a match failure with structured WARNING-level logging.

    Args:
        failure: Structured failure information to log

    Example log output:
        [WARNING] spotify isrc match failed for track 12345: No ISRC available (reason: no_isrc)
        [WARNING] musicbrainz artist_title match failed for track 67890: API timeout (reason: api_error, exception: requests.Timeout)
    """
    # Build the main message
    message = f"{failure.service} {failure.method} match failed for track {failure.track_id}: {failure.details}"

    # Build structured context for the log
    log_context = {
        "track_id": failure.track_id,
        "service": failure.service,
        "method": failure.method,
        "reason": failure.reason.value,
    }

    # Add exception info if available
    if failure.exception_type:
        message += (
            f" (reason: {failure.reason.value}, exception: {failure.exception_type})"
        )
        log_context["exception_type"] = failure.exception_type
    else:
        message += f" (reason: {failure.reason.value})"

    # Log with structured context
    logger.warning(message, **log_context)


def log_failure_summary(service: str, match_count: int, failure_count: int) -> None:
    """Log a summary of provider results.

    Args:
        service: Name of the service provider
        match_count: Number of successful matches
        failure_count: Number of failed attempts

    Example log output:
        [INFO] Provider spotify completed: 45 matches, 5 failures
    """
    total_attempts = match_count + failure_count
    success_rate = (match_count / total_attempts * 100) if total_attempts > 0 else 0

    logger.info(
        f"Provider {service} completed: {match_count} matches, {failure_count} failures",
        service=service,
        match_count=match_count,
        failure_count=failure_count,
        total_attempts=total_attempts,
        success_rate=round(success_rate, 1),
    )
