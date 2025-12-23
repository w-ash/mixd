"""Builder pattern for playlist operation metadata.

Eliminates duplicate metadata building code using modern Python 3.13+ builder pattern
with method chaining and type safety.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Self

from attrs import define, field

from src.application.use_cases._shared.playlist_results import ApiMetadata


@define(slots=True)
class PlaylistMetadataBuilder:
    """Fluent builder for playlist operation metadata.

    Uses Python 3.13+ Self type for proper method chaining type hints.
    """

    _metadata: dict[str, Any] = field(factory=dict)

    def with_timestamp(self, timestamp: datetime | None = None) -> Self:
        """Add timestamp to metadata (defaults to now)."""
        self._metadata["last_modified"] = (
            timestamp or datetime.now(UTC)
        ).isoformat()
        self._metadata["database_update_timestamp"] = datetime.now(UTC).isoformat()
        return self

    def with_operations(
        self,
        requested: int,
        applied: int,
    ) -> Self:
        """Add operation counts to metadata."""
        self._metadata["operations_requested"] = requested
        self._metadata["operations_applied"] = applied
        return self

    def with_snapshot(self, snapshot_id: str | None) -> Self:
        """Add external service snapshot ID."""
        self._metadata["snapshot_id"] = snapshot_id
        return self

    def with_track_counts(
        self,
        added: int = 0,
        removed: int = 0,
        moved: int = 0,
    ) -> Self:
        """Add track operation counts."""
        self._metadata["tracks_added"] = added
        self._metadata["tracks_removed"] = removed
        self._metadata["tracks_moved"] = moved
        return self

    def with_validation(self, passed: bool) -> Self:
        """Add validation status."""
        self._metadata["validation_passed"] = passed
        return self

    def with_error_info(
        self,
        error_type: str,
        is_retryable: bool = False,
        is_auth_error: bool = False,
        is_rate_limit: bool = False,
    ) -> Self:
        """Add error classification metadata."""
        self._metadata["error_type"] = error_type
        self._metadata["is_retryable"] = is_retryable
        self._metadata["is_auth_error"] = is_auth_error
        self._metadata["is_rate_limit"] = is_rate_limit
        return self

    def with_state_consistency(
        self,
        requested_tracks: int,
        created_items: int,
        operations_requested: int,
        operations_applied: int,
    ) -> Self:
        """Add state consistency validation metadata."""
        self._metadata["state_consistency_check"] = {
            "requested_tracks": requested_tracks,
            "created_items": created_items,
            "operations_requested": operations_requested,
            "operations_applied": operations_applied,
        }
        return self

    def with_existing_record_info(
        self,
        found: bool,
        existing_id: int | None = None,
        existing_items_count: int = 0,
    ) -> Self:
        """Add information about existing database records."""
        self._metadata["existing_record_found"] = found
        if existing_id is not None:
            self._metadata["existing_id"] = existing_id
            self._metadata["existing_items"] = existing_items_count
        return self

    def with_items_created(self, count: int) -> Self:
        """Add count of playlist items created."""
        self._metadata["items_created"] = count
        return self

    def with_custom(self, key: str, value: Any) -> Self:
        """Add custom metadata field."""
        self._metadata[key] = value
        return self

    def build(self) -> ApiMetadata:
        """Build final metadata dictionary."""
        return self._metadata  # type: ignore[return-value]

    def build_dict(self) -> dict[str, Any]:
        """Build as plain dictionary for cases where TypedDict isn't needed."""
        return self._metadata.copy()


def build_api_execution_metadata(
    operations_count: int,
    snapshot_id: str | None,
    tracks_added: int,
    tracks_removed: int,
    tracks_moved: int,
    validation_passed: bool,
) -> ApiMetadata:
    """Build metadata for successful API execution.

    Convenience function for common API execution metadata pattern.
    """
    return (
        PlaylistMetadataBuilder()
        .with_timestamp()
        .with_operations(operations_count, operations_count if validation_passed else 0)
        .with_snapshot(snapshot_id)
        .with_track_counts(tracks_added, tracks_removed, tracks_moved)
        .with_validation(validation_passed)
        .build()
    )


def build_error_metadata(
    error_type: str,
    is_retryable: bool = False,
    is_auth_error: bool = False,
    is_rate_limit: bool = False,
) -> ApiMetadata:
    """Build metadata for error responses.

    Convenience function for error metadata pattern.
    """
    return (
        PlaylistMetadataBuilder()
        .with_error_info(error_type, is_retryable, is_auth_error, is_rate_limit)
        .build()
    )


def build_database_update_metadata(
    api_metadata: ApiMetadata,
    requested_tracks: int,
    created_items: int,
    operations_requested: int,
    operations_applied: int,
    existing_record_found: bool,
) -> dict[str, Any]:
    """Build comprehensive metadata for database updates.

    Merges API metadata with database-specific validation info.
    """
    return (
        PlaylistMetadataBuilder()
        .with_custom("api_metadata", api_metadata)
        .with_timestamp()
        .with_state_consistency(
            requested_tracks, created_items, operations_requested, operations_applied
        )
        .with_existing_record_info(existing_record_found)
        .with_operations(operations_requested, operations_applied)
        .with_items_created(created_items)
        .build_dict()
    )
