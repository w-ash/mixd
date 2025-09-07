"""Generalized error classification system for connector error handling.

Provides pluggable error classification that can be customized per service
while maintaining consistent retry behavior patterns across all connectors.
"""

from abc import ABC, abstractmethod
from typing import Protocol

from src.config import get_logger

logger = get_logger(__name__).bind(service="connectors")


class ErrorClassifierProtocol(Protocol):
    """Protocol for service-specific error classifiers."""

    def classify_error(self, exception: Exception) -> tuple[str, str, str]:
        """Classify error for retry behavior.

        Returns:
            Tuple of (error_type, error_code, error_description)
            error_type: "permanent", "temporary", "rate_limit", "not_found", "unknown"
        """
        ...


class BaseErrorClassifier(ABC):
    """Base error classifier with common patterns."""

    @abstractmethod
    def classify_error(self, exception: Exception) -> tuple[str, str, str]:
        """Classify error for retry behavior."""
        ...


class DefaultErrorClassifier(BaseErrorClassifier):
    """Default error classifier for services without specific patterns."""

    def classify_error(self, exception: Exception) -> tuple[str, str, str]:
        """Classify generic exceptions as unknown errors."""
        return ("unknown", "N/A", str(exception))


def should_giveup_on_error(classifier: ErrorClassifierProtocol, service_name: str = "lastfm"):
    """Create giveup function that uses error type-specific retry counts."""

    def _should_giveup(exception) -> bool:
        """Determine if we should give up retrying based on error classification."""
        error_type, _, _ = classifier.classify_error(exception)

        # Always give up immediately on permanent and not_found errors
        # For these error types, we don't need to track retry counts
        if error_type in ["permanent", "not_found"]:
            return True

        # For retryable errors (rate_limit, temporary, unknown), 
        # let the backoff decorator handle the retry count via max_tries
        return False

    return _should_giveup


def create_backoff_handler(classifier: ErrorClassifierProtocol, service_name: str):
    """Create backoff handler that uses the provided error classifier."""

    def _handle_backoff(details):
        """Handle backoff with error classification and enhanced logging."""
        exception = details.get("exception")
        error_type, error_code, error_desc = classifier.classify_error(exception)

        if error_type == "rate_limit":
            logger.warning(
                f"{service_name} rate limit detected - pausing requests",
                tries=f"{details['tries']}",
                wait_time=f"{details.get('wait', 0):.1f}s",
                elapsed=f"{details.get('elapsed', 0):.1f}s",
                error_code=error_code,
                service=service_name,
            )
        else:
            logger.warning(
                f"{service_name} API retry {details['tries']}",
                wait_time=f"{details.get('wait', 0):.1f}s",
                elapsed=f"{details.get('elapsed', 0):.1f}s",
                error_type=error_type,
                error_code=error_code,
                error_description=error_desc,
                exception=str(exception),
                retry_reason=f"{error_type}_error",
                service=service_name,
            )

    return _handle_backoff


def create_giveup_handler(classifier: ErrorClassifierProtocol, service_name: str):
    """Create giveup handler that uses the provided error classifier."""

    def _handle_giveup(details):
        """Enhanced giveup logging with error classification."""
        exception = details.get("exception")
        error_type, error_code, error_desc = classifier.classify_error(exception)

        logger.warning(
            f"{service_name} API giving up after {details['tries']} attempts",
            error_type=error_type,
            error_code=error_code,
            error_description=error_desc,
            total_elapsed=f"{details.get('elapsed', 0):.1f}s",
            retry_reason=f"{error_type.title()} error: {error_desc}",
            final_exception=str(exception),
            service=service_name,
        )

    return _handle_giveup
