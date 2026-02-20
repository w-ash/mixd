"""Typed result objects for playlist operations.

Replaces tuple-based returns with strongly-typed result objects using Python 3.13+
features for better type safety and maintainability.
"""

from typing import Any, Self, TypedDict

from attrs import define, field


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
class ApiExecutionResult:
    """Result from executing playlist operations against external API.

    Replaces 6-tuple return with structured, type-safe result object.
    """

    api_calls_made: int
    metadata: ApiMetadata
    operations_performed: int
    counts: OperationCounts
    success: bool = True
    error: str | None = None

    @property
    def tracks_added(self) -> int:
        """Convenience accessor for added track count."""
        return self.counts.added if self.success else 0

    @property
    def tracks_removed(self) -> int:
        """Convenience accessor for removed track count."""
        return self.counts.removed if self.success else 0

    @property
    def tracks_moved(self) -> int:
        """Convenience accessor for moved track count."""
        return self.counts.moved if self.success else 0


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


def _empty_api_metadata() -> ApiMetadata:
    """Factory for empty ApiMetadata."""
    return ApiMetadata()


@define(frozen=True, slots=True)
class ExternalApiResponse:
    """Generic response from external service API operations.

    Replaces dict-based responses with typed structure.
    """

    success: bool
    api_calls_made: int
    metadata: ApiMetadata = field(factory=_empty_api_metadata)
    error: str | None = None
    partial_success: bool = False

    @classmethod
    def success_response(
        cls,
        api_calls: int,
        metadata: ApiMetadata,
    ) -> Self:
        """Create successful response."""
        return cls(
            success=True,
            api_calls_made=api_calls,
            metadata=metadata,
            error=None,
            partial_success=False,
        )

    @classmethod
    def error_response(
        cls,
        error: str,
        metadata: ApiMetadata | None = None,
    ) -> Self:
        """Create error response."""
        empty_metadata = ApiMetadata()
        return cls(
            success=False,
            api_calls_made=0,
            metadata=metadata or empty_metadata,
            error=error,
            partial_success=False,
        )

    @classmethod
    def partial_response(
        cls,
        api_calls: int,
        metadata: ApiMetadata,
    ) -> Self:
        """Create partial success response."""
        return cls(
            success=False,
            api_calls_made=api_calls,
            metadata=metadata,
            error=None,
            partial_success=True,
        )
