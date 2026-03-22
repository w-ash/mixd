"""Domain exception types.

Typed exceptions for domain-level error conditions. These replace stringly-typed
ValueError messages so the API layer can map specific exception types to
specific HTTP status codes without parsing error message text.
"""


class DomainError(Exception):
    """Base class for domain-level errors."""


class NotFoundError(Exception):
    """Raised when a requested entity does not exist."""


class TemplateReadOnlyError(Exception):
    """Raised when attempting to modify or delete a read-only template workflow."""


class TracklistInvariantError(DomainError):
    """Raised when a tracklist violates workflow invariants."""


class ConfirmationRequiredError(DomainError):
    """Raised when a destructive operation requires explicit user confirmation."""

    def __init__(
        self, message: str, *, removals: int, total: int, remaining: int
    ) -> None:
        super().__init__(message)
        self.removals = removals
        self.total = total
        self.remaining = remaining
