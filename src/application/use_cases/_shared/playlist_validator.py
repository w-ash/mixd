"""Validation strategies for connector playlist operations.

Provides reusable validation logic for playlist update operations with typed
results using Python 3.13+ patterns.
"""

from src.config import get_logger

logger = get_logger(__name__)


def is_auth_error_message(error_msg: str) -> bool:
    """Check if error message indicates authentication error."""
    msg_lower = error_msg.lower()
    return "auth" in msg_lower or "token" in msg_lower


def is_rate_limit_error(error_msg: str) -> bool:
    """Check if error message indicates rate limiting."""
    msg_lower = error_msg.lower()
    return "rate" in msg_lower or "429" in msg_lower


def classify_connector_api_error(exception: Exception) -> dict[str, str | bool]:
    """Classify connector API errors using pattern matching.

    Uses Python 3.13+ pattern matching and type guards for error classification.

    Args:
        exception: Exception from connector API call

    Returns:
        Classification dict with error_type, is_retryable, is_auth_error, is_rate_limit
    """
    error_type_name = type(exception).__name__
    error_message = str(exception)

    # Use pattern matching for error type classification
    match error_type_name:
        case "TimeoutError" | "ConnectionError" | "HTTPError":
            is_retryable = True
        case _:
            is_retryable = False

    # Use type guards for message-based classification
    is_auth = is_auth_error_message(error_message)
    is_rate_limit = is_rate_limit_error(error_message)

    return {
        "error_type": error_type_name,
        "is_retryable": is_retryable,
        "is_auth_error": is_auth,
        "is_rate_limit": is_rate_limit,
    }


def classify_database_error(exception: Exception) -> dict[str, str | bool]:
    """Classify database errors for retry and recovery decisions.

    Args:
        exception: Database exception

    Returns:
        Classification dict with error_type, is_constraint_violation, is_connection_error
    """
    error_message = str(exception).lower()

    return {
        "error_type": type(exception).__name__,
        "is_constraint_violation": "constraint" in error_message
        or "unique" in error_message,
        "is_connection_error": "connection" in error_message
        or "timeout" in error_message,
    }
