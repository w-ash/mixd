"""Centralized retry policies for all connectors using tenacity.

This module provides reusable retry policies that integrate with our error
classification system, enabling sophisticated retry behavior based on error types.

Key components:
- ErrorClassifierRetry: Tenacity retry predicate using ErrorClassifier
- RetryPolicyFactory: Creates configured AsyncRetrying instances per service
- Enhanced callbacks: Port of backoff handlers to tenacity's rich retry state

The retry system preserves all current behavior while enabling:
- Centralized policy definitions (single source of truth)
- Composable stop conditions (attempts + time limits)
- Rich retry state for observability
- Error-type-specific retry behavior
"""

from collections.abc import Callable

from attrs import define
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
    wait_random,
)

from src.config import get_logger
from src.infrastructure.connectors._shared.error_classifier import (
    ErrorClassifier,
)

logger = get_logger(__name__).bind(service="retry_policies")

# -------------------------------------------------------------------------
# UTILITY FUNCTIONS
# -------------------------------------------------------------------------


def _format_duration(seconds: float | None) -> str:
    """Format duration for logging.

    Args:
        seconds: Duration in seconds, or None

    Returns:
        Formatted string like "2.5s" or "N/A" if None
    """
    return f"{seconds:.1f}s" if seconds else "N/A"


def _extract_classified_error(
    retry_state: RetryCallState, classifier: ErrorClassifier
) -> tuple[Exception, str, str, str] | None:
    """Extract and classify error from retry state.

    This utility consolidates the type guard and error classification logic
    that was duplicated across multiple callback handlers.

    Args:
        retry_state: Tenacity retry state
        classifier: Service-specific error classifier

    Returns:
        Tuple of (exception, error_type, error_code, error_description) if
        the retry state contains a failed Exception, None otherwise.

    Note:
        Returns None for:
        - No outcome or successful outcome
        - BaseException that isn't an Exception subclass
    """
    if not retry_state.outcome or not retry_state.outcome.failed:
        return None

    exception = retry_state.outcome.exception()

    # Type guard: classify_error expects Exception, not BaseException
    if not isinstance(exception, Exception):
        return None

    error_type, error_code, error_desc = classifier.classify_error(exception)
    return exception, error_type, error_code, error_desc


# -------------------------------------------------------------------------
# RETRY PREDICATES
# -------------------------------------------------------------------------


def create_error_classifier_retry(classifier: ErrorClassifier):
    """Create retry predicate using error classifier.

    This function creates a tenacity retry predicate that integrates our
    sophisticated error classification system. The returned predicate
    determines whether to retry based on the error type.

    Retry on:
        - temporary: Transient failures (500-504, network issues)
        - rate_limit: Rate limiting (429, service-specific codes)
        - unknown: Unclassified errors (defensive retry)

    Fail fast on:
        - permanent: Non-retryable errors (400, 401, 403)
        - not_found: Resource doesn't exist (404)

    Args:
        classifier: Service-specific error classifier

    Returns:
        Tenacity retry predicate for use with AsyncRetrying

    Example:
        >>> classifier = SpotifyErrorClassifier()
        >>> retry_predicate = create_error_classifier_retry(classifier)
        >>> policy = AsyncRetrying(retry=retry_predicate, ...)
    """
    from tenacity.retry import retry_if_exception

    def should_retry_exception(exc: BaseException) -> bool:
        """Check if exception should be retried based on error classification."""
        if not isinstance(exc, Exception):
            return False  # Never retry KeyboardInterrupt, SystemExit, etc.
        error_type, _, _ = classifier.classify_error(exc)
        # Retry on temporary, rate_limit, unknown
        # Give up on permanent and not_found errors
        return error_type not in ["permanent", "not_found"]

    return retry_if_exception(should_retry_exception)


# -------------------------------------------------------------------------
# CALLBACK HANDLERS
# -------------------------------------------------------------------------


def create_tenacity_backoff_handler(
    classifier: ErrorClassifier, service_name: str
) -> Callable[[RetryCallState], None]:
    """Create tenacity before_sleep callback with error classification.

    This callback is invoked before each retry attempt, providing detailed
    logging with error classification context. It preserves the behavior
    of the original backoff handlers while leveraging tenacity's richer
    retry state.

    Args:
        classifier: Service-specific error classifier
        service_name: Name of service for logging (e.g., "spotify", "lastfm")

    Returns:
        Callback function for tenacity's before_sleep parameter

    Example:
        >>> classifier = SpotifyErrorClassifier()
        >>> handler = create_tenacity_backoff_handler(classifier, "spotify")
        >>> policy = AsyncRetrying(before_sleep=handler, ...)
    """

    def _handle_backoff(retry_state: RetryCallState) -> None:
        """Handle backoff with error classification and enhanced logging.

        Args:
            retry_state: Tenacity retry state with comprehensive information
        """
        result = _extract_classified_error(retry_state, classifier)
        if not result:
            return

        exception, error_type, error_code, error_desc = result

        # Special handling for rate limit errors
        if error_type == "rate_limit":
            logger.warning(
                f"{service_name} rate limit detected - pausing requests",
                attempt=retry_state.attempt_number,
                wait_time=_format_duration(retry_state.idle_for),
                elapsed=_format_duration(retry_state.seconds_since_start),
                error_code=error_code,
                service=service_name,
            )
        else:
            logger.warning(
                f"{service_name} API retry {retry_state.attempt_number}",
                wait_time=_format_duration(retry_state.idle_for),
                elapsed=_format_duration(retry_state.seconds_since_start),
                error_type=error_type,
                error_code=error_code,
                error_description=error_desc,
                exception=str(exception),
                retry_reason=f"{error_type}_error",
                service=service_name,
            )

    return _handle_backoff


def create_tenacity_giveup_handler(
    classifier: ErrorClassifier, service_name: str
) -> Callable[[RetryCallState], None]:
    """Create tenacity after callback for final failure logging.

    This callback is invoked after EVERY attempt. It detects the final attempt
    by checking if the stop condition is met AND the outcome failed, then logs
    comprehensive failure information.

    Args:
        classifier: Service-specific error classifier
        service_name: Name of service for logging

    Returns:
        Callback function for tenacity's after parameter

    Example:
        >>> classifier = LastFMErrorClassifier()
        >>> handler = create_tenacity_giveup_handler(classifier, "lastfm")
        >>> policy = AsyncRetrying(after=handler, ...)
    """

    def _handle_giveup(retry_state: RetryCallState) -> None:
        """Log final failure after exhausting retries.

        This callback is invoked after every attempt. It checks if this is
        the final attempt by checking if the stop condition is met and the
        outcome failed.

        Args:
            retry_state: Tenacity retry state
        """
        # Only log if this attempt failed AND stop condition is met (no more retries)
        if not (retry_state.outcome and retry_state.outcome.failed):
            return

        # Check if stop condition is met (this is the final attempt)
        if not retry_state.retry_object.stop(retry_state):
            return  # Not the final attempt yet

        result = _extract_classified_error(retry_state, classifier)
        if not result:
            return

        exception, error_type, error_code, error_desc = result

        logger.warning(
            f"{service_name} API giving up after {retry_state.attempt_number} attempts",
            error_type=error_type,
            error_code=error_code,
            error_description=error_desc,
            total_elapsed=_format_duration(retry_state.seconds_since_start),
            retry_reason=f"{error_type.title()} error: {error_desc}",
            final_exception=str(exception),
            service=service_name,
        )

    return _handle_giveup


# -------------------------------------------------------------------------
# RETRY CONFIGURATION + FACTORY
# -------------------------------------------------------------------------


@define(frozen=True)
class RetryConfig:
    """Configuration for a service retry policy.

    All numeric tuning parameters are required — callers must supply values
    from ``settings.api.*`` so that retry behaviour is controlled solely by
    the configuration layer (no magic numbers in business or infrastructure
    code).

    Args:
        service_name: Service name for logging (e.g., "spotify", "lastfm").
        classifier: Service-specific error classifier instance.
        max_attempts: Maximum number of retry attempts.
            Source: ``settings.api.<service>_retry_count``.
        wait_multiplier: Exponential backoff base multiplier in seconds.
            Source: ``settings.api.<service>_retry_base_delay``.
        wait_max: Maximum wait between retries in seconds.
            Source: ``settings.api.<service>_retry_max_delay``.
        max_delay: Optional time-based stop in seconds (``None`` = no limit).
            Source: ``settings.api.<service>_retry_max_delay`` when a hard
            wall-clock cap is also needed (e.g. LastFM).
        include_httpx_errors: If True, type-filter to httpx exceptions before
            passing to the error classifier. Set False for non-httpx clients
            like MusicBrainz that use a sync library and catch all exceptions.
        service_error_types: Additional exception types to retry on beyond
            httpx errors (e.g., LastFMAPIError).
    """

    service_name: str
    classifier: ErrorClassifier
    max_attempts: int
    wait_multiplier: float
    wait_max: float
    max_delay: float | None = None
    include_httpx_errors: bool = True
    service_error_types: tuple[type[BaseException], ...] = ()


class RetryPolicyFactory:
    """Factory for creating centralized retry policies for connectors.

    All policies are created via the single ``create_policy()`` class method,
    parameterized by a ``RetryConfig`` dataclass.  This keeps the policy
    logic in one place while making per-service differences explicit at the
    call site.

    Example:
        >>> policy = RetryPolicyFactory.create_policy(
        ...     RetryConfig(
        ...         service_name="spotify",
        ...         classifier=SpotifyErrorClassifier(),
        ...     )
        ... )
        >>> result = await policy(api_method, *args)
    """

    @staticmethod
    def create_policy(config: RetryConfig) -> AsyncRetrying:
        """Create a retry policy from a RetryConfig.

        Builds the tenacity retry predicate, stop condition, and wait strategy
        from the supplied configuration.  When ``include_httpx_errors=True``
        (the default) the predicate type-filters to httpx exceptions (plus any
        ``service_error_types``) before invoking the error classifier.  When
        False, all exception types flow through the classifier directly.

        Args:
            config: Policy configuration parameters.

        Returns:
            Configured AsyncRetrying instance ready for ``await policy(fn, *args)``.
        """
        if config.include_httpx_errors:
            import httpx

            retry_predicate = retry_if_exception_type(
                httpx.HTTPStatusError
            ) | retry_if_exception_type(httpx.RequestError)
            if config.service_error_types:
                retry_predicate |= retry_if_exception_type(config.service_error_types)
            retry_predicate &= create_error_classifier_retry(config.classifier)
        else:
            retry_predicate = create_error_classifier_retry(config.classifier)
            if config.service_error_types:
                retry_predicate = (
                    retry_if_exception_type(config.service_error_types)
                    & retry_predicate
                )

        stop = stop_after_attempt(config.max_attempts)
        if config.max_delay is not None:
            stop |= stop_after_delay(config.max_delay)

        return AsyncRetrying(
            stop=stop,
            wait=wait_exponential(
                multiplier=config.wait_multiplier, max=config.wait_max
            )
            + wait_random(0, 1),
            retry=retry_predicate,
            before_sleep=create_tenacity_backoff_handler(
                config.classifier, config.service_name
            ),
            after=create_tenacity_giveup_handler(
                config.classifier, config.service_name
            ),
            reraise=True,
        )
