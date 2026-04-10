"""Builder pattern for playlist operation metadata.

Eliminates duplicate metadata building code using modern Python 3.13+ builder pattern
with method chaining and type safety.
"""

from datetime import UTC, datetime
from typing import Self

from attrs import define, field

from src.application.use_cases._shared.playlist_results import ApiMetadata
from src.domain.entities.shared import JsonValue


@define(slots=True)
class PlaylistMetadataBuilder:
    """Fluent builder for playlist operation metadata.

    Uses Python 3.13+ Self type for proper method chaining type hints.
    """

    _metadata: dict[str, JsonValue] = field(factory=dict)

    def with_timestamp(self, timestamp: datetime | None = None) -> Self:
        """Add timestamp to metadata (defaults to now)."""
        self._metadata["last_modified"] = (timestamp or datetime.now(UTC)).isoformat()
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

    def with_custom(self, key: str, value: JsonValue) -> Self:
        """Add custom metadata field."""
        self._metadata[key] = value
        return self

    def build(self) -> ApiMetadata:
        """Build final metadata dictionary."""
        return self._metadata  # pyright: ignore[reportReturnType] — builder accumulates any keys; ApiMetadata is total=False

    def build_dict(self) -> dict[str, JsonValue]:
        """Build as plain dictionary for cases where TypedDict isn't needed."""
        return self._metadata.copy()


def build_api_execution_metadata(
    operations_count: int,
    snapshot_id: str | None,
    tracks_added: int,
    tracks_removed: int,
    tracks_moved: int,
    validation_passed: bool,
) -> dict[str, JsonValue]:
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
        .build_dict()
    )
