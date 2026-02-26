"""Failure handling utilities for match providers.

Provides structured logging and composable utilities for failure handling
across all matching providers while maintaining clean architecture principles.
"""

from collections.abc import Callable

from src.config import get_logger
from src.domain.entities import Track
from src.domain.matching.types import (
    MatchFailure,
    MatchFailureReason,
    ProviderMatchResult,
    RawProviderMatch,
)

logger = get_logger(__name__)


def log_match_failure(failure: MatchFailure) -> None:
    """Log a match failure with structured WARNING-level logging.

    Args:
        failure: Structured failure information to log
    """
    message = f"{failure.service} {failure.method} match failed for track {failure.track_id}: {failure.details}"

    log_context = {
        "track_id": failure.track_id,
        "service": failure.service,
        "method": failure.method,
        "reason": failure.reason.value,
    }

    if failure.exception_type:
        message += (
            f" (reason: {failure.reason.value}, exception: {failure.exception_type})"
        )
        log_context["exception_type"] = failure.exception_type
    else:
        message += f" (reason: {failure.reason.value})"

    logger.warning(message, **log_context)


def log_failure_summary(service: str, match_count: int, failure_count: int) -> None:
    """Log a summary of provider results.

    Args:
        service: Name of the service provider
        match_count: Number of successful matches
        failure_count: Number of failed attempts
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


def create_and_log_failure(
    track_id: int,
    reason: MatchFailureReason,
    service: str,
    method: str,
    details: str,
    exception_type: str = "",
) -> MatchFailure:
    """Create a MatchFailure and log it in one operation."""
    failure = MatchFailure(
        track_id=track_id,
        reason=reason,
        service=service,
        method=method,
        details=details,
        exception_type=exception_type,
    )
    log_match_failure(failure)
    return failure


def handle_track_processing_failure(
    track_id: int,
    service: str,
    method: str,
    error: Exception,
) -> MatchFailure:
    """Standard handler for API exceptions during track processing."""
    return create_and_log_failure(
        track_id=track_id,
        reason=MatchFailureReason.API_ERROR,
        service=service,
        method=method,
        details=str(error),
        exception_type=type(error).__name__,
    )


def validate_track_for_method(
    track: Track,
    method: str,
    service: str,
    validator_func: Callable[[Track], bool],
    error_reason: MatchFailureReason,
    error_details: str,
) -> MatchFailure | None:
    """Validate a track for a specific method and return failure if invalid."""
    if not track.id:
        return None

    if not validator_func(track):
        return create_and_log_failure(
            track_id=track.id,
            reason=error_reason,
            service=service,
            method=method,
            details=error_details,
        )
    return None


def merge_results(*results: ProviderMatchResult) -> ProviderMatchResult:
    """Merge multiple ProviderMatchResult objects."""
    matches: dict[int, RawProviderMatch] = {}
    failures: list[MatchFailure] = []

    for result in results:
        matches.update(result.matches)
        failures.extend(result.failures)

    return ProviderMatchResult(matches=matches, failures=failures)
