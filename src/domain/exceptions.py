"""Domain exception types.

Typed exceptions for domain-level error conditions. These replace stringly-typed
ValueError messages so the API layer can map specific exception types to
specific HTTP status codes without parsing error message text.
"""

from uuid import UUID


class DomainError(Exception):
    """Base class for domain-level errors."""


class NotFoundError(Exception):
    """Raised when a requested entity does not exist."""


class TemplateReadOnlyError(Exception):
    """Raised when attempting to modify or delete a read-only template workflow."""


class TracklistInvariantError(DomainError):
    """Raised when a tracklist violates workflow invariants."""


class OptimisticLockError(DomainError):
    """Raised when a concurrent modification is detected (stale version)."""

    def __init__(self, entity_id: UUID, expected_version: int) -> None:
        super().__init__(
            f"Concurrent modification detected for entity {entity_id} "
            f"(expected version {expected_version})"
        )
        self.entity_id = entity_id
        self.expected_version = expected_version


class ConfirmationRequiredError(DomainError):
    """Raised when a destructive operation requires explicit user confirmation."""

    def __init__(
        self, message: str, *, removals: int, total: int, remaining: int
    ) -> None:
        super().__init__(message)
        self.removals = removals
        self.total = total
        self.remaining = remaining
