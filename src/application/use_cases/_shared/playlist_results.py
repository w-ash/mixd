"""Typed result objects for playlist operations.

Replaces tuple-based returns with strongly-typed result objects using Python 3.13+
features for better type safety and maintainability.
"""

# pyright: reportExplicitAny=false
# Legitimate Any: use case results, OperationResult metadata, metric values

from typing import Any, TypedDict

from attrs import define


class ApiMetadata(TypedDict, total=False):
    """Structured metadata from external API operations.

    Uses TypedDict for type-safe metadata instead of dict[str, Any].
    """

    last_modified: str
    operations_requested: int
    operations_applied: int
    snapshot_id: str | None
    tracks_added: int
    tracks_removed: int
    tracks_moved: int
    validation_passed: bool
    error_type: str
    is_retryable: bool
    is_auth_error: bool
    is_rate_limit: bool


@define(frozen=True, slots=True)
class OperationCounts:
    """Count of playlist operations by type.

    Replaces tuple[int, int, int] with named fields for clarity.
    """

    added: int = 0
    removed: int = 0
    moved: int = 0

    @property
    def total(self) -> int:
        """Total number of operations across all types."""
        return self.added + self.removed + self.moved

    @property
    def has_changes(self) -> bool:
        """Whether any operations were counted."""
        return self.total > 0


@define(frozen=True, slots=True)
class AppendOperationResult:
    """Result from appending tracks to external playlist.

    Replaces 4-tuple return with structured result object.
    """

    api_calls_made: int
    metadata: dict[str, Any]
    operations_performed: int
    tracks_added: int
    success: bool = True

    @property
    def has_changes(self) -> bool:
        """Whether any tracks were added."""
        return self.tracks_added > 0
