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
from http import HTTPStatus
import math
from typing import Final, override

from attrs import define
import httpx2
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
    wait_random,
)
from tenacity.wait import wait_base

from src.config import get_logger
from src.infrastructure.connectors._shared.error_classifier import (
    ErrorClassifier,
)
from src.infrastructure.connectors._shared.rate_limiting import (
    get_connector_rate_limiter,
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


def _retry_after_seconds(retry_state: RetryCallState) -> float | None:
    """Seconds a 429 response asked us to wait, if the failure carries one.

    Only the numeric ``Retry-After`` form is handled; the HTTP-date form (rare,
    and unused by our connectors' APIs) falls back to exponential waits.

    Single source for the header parse — both the wait strategy
    (:class:`RetryAfterWait`) and the shared limiter brake read it from here.
    """
    if not retry_state.outcome or not retry_state.outcome.failed:
        return None
    exception = retry_state.outcome.exception()
    if not isinstance(exception, httpx2.HTTPStatusError):
        return None
    if exception.response.status_code != HTTPStatus.TOO_MANY_REQUESTS:
        return None
    # Headers.get returns Any (untyped default); __getitem__ is typed -> str.
    headers = exception.response.headers
    if "Retry-After" not in headers:
        return None
    try:
        seconds = float(headers["Retry-After"])
    except ValueError:
        return None
    # float() accepts "nan"/"inf": NaN defeats both the floor below and the cap
    # in RetryAfterWait, handing tenacity an undefined sleep. Non-finite values
    # fall back to the exponential wait.
    if not math.isfinite(seconds):
        return None
    return max(seconds, 0.0)


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

# Applied when a rate limit is classified without a usable ``Retry-After``
# (no header, HTTP-date form, or a non-HTTP service code such as Last.fm's).
# Short on purpose: it is a guess at an unpublished window, and pacing plus
# exponential backoff still carry the rest of the correction.
DEFAULT_RATE_LIMIT_PAUSE_SECONDS: Final = 2.0


def _rate_limit_pause_seconds(retry_state: RetryCallState, cap: float) -> float:
    """How long the whole service should stand down after a rate-limit error.

    Bounded by ``cap`` for the same reason :class:`RetryAfterWait` bounds its
    sleep, and harder: ``Retry-After`` is server-supplied and may be hostile or
    simply wrong, and this value brakes the service's *process-global* limiter
    rather than one call. ``Retry-After: 999999`` is finite, so it clears the
    parse guard in :func:`_retry_after_seconds` and would otherwise freeze every
    caller of the service for ~11.5 days on the strength of one header.
    """
    retry_after = _retry_after_seconds(retry_state)
    if retry_after is None:
        return DEFAULT_RATE_LIMIT_PAUSE_SECONDS
    return min(retry_after, cap)


def create_tenacity_backoff_handler(
    classifier: ErrorClassifier, service_name: str, pause_cap_seconds: float
) -> Callable[[RetryCallState], None]:
    """Create tenacity before_sleep callback with error classification.

    This callback is invoked before each retry attempt, providing detailed
    logging with error classification context. It preserves the behavior
    of the original backoff handlers while leveraging tenacity's richer
    retry state.

    Args:
        classifier: Service-specific error classifier
        service_name: Name of service for logging (e.g., "spotify", "lastfm")
        pause_cap_seconds: Upper bound on a ``Retry-After``-driven limiter
            pause. Supplied by :meth:`RetryPolicyFactory.create_policy` from
            ``RetryConfig.wait_max``, the same value that caps
            :class:`RetryAfterWait` — one bound per service, not two.

    Returns:
        Callback function for tenacity's before_sleep parameter

    Example:
        >>> classifier = SpotifyErrorClassifier()
        >>> handler = create_tenacity_backoff_handler(classifier, "spotify", 60.0)
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
            pause_seconds = _rate_limit_pause_seconds(retry_state, pause_cap_seconds)
            limiter = get_connector_rate_limiter(service_name)
            if limiter is not None:
                limiter.pause_for(pause_seconds)
            logger.warning(
                f"{service_name} rate limit detected - pausing requests",
                attempt=retry_state.attempt_number,
                wait_time=_format_duration(retry_state.idle_for),
                elapsed=_format_duration(retry_state.seconds_since_start),
                pause=_format_duration(pause_seconds),
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


@define
class RetryAfterWait(wait_base):
    """Honor a 429's ``Retry-After`` when the server sent one, else fall back.

    The exponential fallback is blind to the server's stated rate-limit
    window: attempts spread over ~31s inside a longer window all burn and the
    operation dies with retries exhausted (the v0.10.2.2 GDPR-import failure).
    Waiting the stated window plus a 1s margin lands the next attempt after
    it; ``cap`` bounds the sleep so a hostile or broken header can't stall an
    operation indefinitely.
    """

    fallback: wait_base
    cap: float

    @override
    def __call__(self, retry_state: RetryCallState) -> float:
        retry_after = _retry_after_seconds(retry_state)
        if retry_after is None:
            return self.fallback(retry_state)
        return min(retry_after + 1.0, self.cap)


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
        wait_max: Maximum wait between retries in seconds. Doubles as the single
            bound on server-declared rate-limit windows — it caps both
            :class:`RetryAfterWait`'s sleep and the limiter pause the backoff
            handler applies, so a hostile ``Retry-After`` has one ceiling.
            Source: ``settings.api.<service>_retry_max_delay``.
        max_delay: Optional time-based stop in seconds (``None`` = no limit).
            Source: ``settings.api.<service>_retry_max_delay`` when a hard
            wall-clock cap is also needed (e.g. LastFM).
        include_httpx_errors: If True, type-filter to httpx2 exceptions before
            passing to the error classifier. Set False for non-httpx2 clients
            like MusicBrainz that use a sync library and catch all exceptions.
        service_error_types: Additional exception types to retry on beyond
            httpx2 errors (e.g., LastFMAPIError).
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
        (the default) the predicate type-filters to httpx2 exceptions (plus any
        ``service_error_types``) before invoking the error classifier.  When
        False, all exception types flow through the classifier directly.

        Args:
            config: Policy configuration parameters.

        Returns:
            Configured AsyncRetrying instance ready for ``await policy(fn, *args)``.
        """
        if config.include_httpx_errors:
            retry_predicate = retry_if_exception_type(
                httpx2.HTTPStatusError
            ) | retry_if_exception_type(httpx2.RequestError)
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
            wait=RetryAfterWait(
                fallback=wait_exponential(
                    multiplier=config.wait_multiplier, max=config.wait_max
                )
                + wait_random(0, 1),
                cap=config.wait_max,
            ),
            retry=retry_predicate,
            before_sleep=create_tenacity_backoff_handler(
                config.classifier, config.service_name, config.wait_max
            ),
            after=create_tenacity_giveup_handler(
                config.classifier, config.service_name
            ),
            reraise=True,
        )
