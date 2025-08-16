"""Utilities to eliminate duplication in provider failure handling.

This module provides composable utilities that make failure handling DRY
across all providers while maintaining clean architecture principles.
"""

from collections.abc import Callable

from src.domain.matching.types import (
    MatchFailure,
    MatchFailureReason,
    ProviderMatchResult,
)
from src.infrastructure.connectors._shared.failure_logging import log_match_failure


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
    track,
    method: str,
    service: str,
    validator_func: Callable,
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
    matches = {}
    failures = []

    for result in results:
        matches.update(result.matches)
        failures.extend(result.failures)

    return ProviderMatchResult(matches=matches, failures=failures)
