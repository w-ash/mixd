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

from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
    wait_random,
)

from src.config import get_logger, settings
from src.infrastructure.connectors._shared.error_classification import (
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
# RETRY POLICY FACTORY
# -------------------------------------------------------------------------


class RetryPolicyFactory:
    """Factory for creating centralized retry policies for connectors.

    This factory creates AsyncRetrying instances with service-specific
    configurations while maintaining consistent retry behavior patterns.
    Each policy preserves the current behavior of the service's backoff
    decorators while enabling easier maintenance and future enhancements.

    The factory supports:
    - Service-specific retry counts and timeouts
    - Error classification integration
    - Comprehensive logging via callbacks
    - Settings-based dynamic configuration

    Example:
        >>> # In SpotifyAPIClient.__attrs_post_init__
        >>> self._retry_policy = RetryPolicyFactory.create_spotify_policy()
        >>>
        >>> # Use policy to retry method calls
        >>> result = await self._retry_policy(api_method, *args)
    """

    @staticmethod
    def create_spotify_policy(
        classifier: ErrorClassifier,
        service_error_types: tuple[type[BaseException], ...] = (),
    ) -> AsyncRetrying:
        """Create Spotify retry policy (preserves current behavior).

        Configuration:
            - max_tries: 3 attempts
            - wait: Exponential backoff (0.5s base, 30s max)
            - retry: httpx HTTP and network errors, based on error classification
            - reraise: True (let caller handle final exception)

        Args:
            classifier: Spotify-specific error classifier instance.
            service_error_types: Additional exception types to retry on
                (beyond httpx errors).

        Returns:
            Configured AsyncRetrying instance for Spotify API calls
        """
        import httpx

        retry_predicate = retry_if_exception_type(
            httpx.HTTPStatusError
        ) | retry_if_exception_type(httpx.RequestError)
        if service_error_types:
            retry_predicate |= retry_if_exception_type(service_error_types)

        return AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.5, max=30) + wait_random(0, 1),
            retry=retry_predicate & create_error_classifier_retry(classifier),
            before_sleep=create_tenacity_backoff_handler(classifier, "spotify"),
            after=create_tenacity_giveup_handler(classifier, "spotify"),
            reraise=True,
        )

    @staticmethod
    def create_lastfm_policy(
        classifier: ErrorClassifier,
        service_error_types: tuple[type[BaseException], ...] = (),
    ) -> AsyncRetrying:
        """Create Last.FM retry policy (settings-based, preserves behavior).

        Configuration:
            - max_tries: settings.api.lastfm_retry_count_rate_limit
            - max_time: settings.api.lastfm_retry_max_delay
            - wait: Exponential backoff (settings-based multiplier and max)
            - retry: service_error_types + httpx exceptions, based on error classification
            - reraise: True

        Args:
            classifier: Last.fm-specific error classifier instance.
            service_error_types: Additional exception types to retry on
                (e.g., LastFMAPIError).

        Returns:
            Configured AsyncRetrying instance for Last.FM API calls
        """
        import httpx

        retry_predicate = retry_if_exception_type(
            httpx.HTTPStatusError
        ) | retry_if_exception_type(httpx.RequestError)
        if service_error_types:
            retry_predicate |= retry_if_exception_type(service_error_types)

        return AsyncRetrying(
            stop=(
                stop_after_attempt(settings.api.lastfm_retry_count_rate_limit)
                | stop_after_delay(settings.api.lastfm_retry_max_delay)
            ),
            wait=wait_exponential(
                multiplier=settings.api.lastfm_retry_base_delay,
                max=settings.api.lastfm_retry_max_delay,
            )
            + wait_random(0, 1),
            retry=retry_predicate & create_error_classifier_retry(classifier),
            before_sleep=create_tenacity_backoff_handler(classifier, "lastfm"),
            after=create_tenacity_giveup_handler(classifier, "lastfm"),
            reraise=True,
        )

    @staticmethod
    def create_musicbrainz_policy(
        classifier: ErrorClassifier,
        service_error_types: tuple[type[BaseException], ...] = (),
    ) -> AsyncRetrying:
        """Create MusicBrainz retry policy.

        Configuration:
            - max_tries: 3 attempts
            - wait: Exponential backoff (1s base, 30s max)
            - retry: All exceptions, based on error classification
            - reraise: True

        Args:
            classifier: MusicBrainz-specific error classifier instance.
            service_error_types: Additional exception types to retry on.

        Returns:
            Configured AsyncRetrying instance for MusicBrainz API calls
        """
        base_retry = create_error_classifier_retry(classifier)
        if service_error_types:
            base_retry = retry_if_exception_type(service_error_types) & base_retry

        return AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1.0, max=30) + wait_random(0, 1),
            retry=base_retry,
            before_sleep=create_tenacity_backoff_handler(classifier, "musicbrainz"),
            after=create_tenacity_giveup_handler(classifier, "musicbrainz"),
            reraise=True,
        )
