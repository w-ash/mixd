"""Shared chat exception → (error code, HTTP status) table.

The chat surface reaches the client on two paths that must agree on error codes:
the pre-stream HTTP error envelope (``middleware.py``) and the in-stream SSE
``error`` event (``chat_sse.py``). Both derive from this single table so a code
string can never drift between them.

The SSE path additionally maps a handful of *in-stream-only* exceptions (raised
once the model turn is underway) that never surface as an HTTP status — those
live in ``chat_sse.py`` and are merged with the codes here.
"""

from src.domain.exceptions import (
    ActionExpiredError,
    ChatUnavailableError,
    ForbiddenError,
    RateLimitExceededError,
)

# exception type -> (error code string, HTTP status). Shared by the HTTP
# middleware (registers a handler per entry — hence type[Exception], what
# add_exception_handler accepts) and the SSE mapper (takes the code; its own
# map widens these keys to type[BaseException] to sit alongside SSE-only ones).
CHAT_ERROR_CODES: dict[type[Exception], tuple[str, int]] = {
    ChatUnavailableError: ("CHAT_UNAVAILABLE", 503),
    RateLimitExceededError: ("RATE_LIMIT_EXCEEDED", 429),
    ActionExpiredError: ("ACTION_EXPIRED", 409),
    ForbiddenError: ("FORBIDDEN", 403),
}
