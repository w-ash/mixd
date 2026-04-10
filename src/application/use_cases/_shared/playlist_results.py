"""Typed result objects for playlist operations.

Replaces tuple-based returns with strongly-typed result objects using Python 3.13+
features for better type safety and maintainability.
"""

from typing import TypedDict

from attrs import define

from src.domain.entities.shared import JsonValue
from src.domain.playlist.diff_engine import PlaylistDiff, PlaylistOperationType

_MAX_EVIDENCE_TRACKS = 100
"""Cap per-list to avoid unbounded JSON in workflow run history."""


def build_playlist_changes(
    diff: PlaylistDiff, playlist_id: str, connector: str | None = None
) -> dict[str, object]:
    """Build lightweight playlist change evidence from a PlaylistDiff.

    Extracts track summaries (id, title, artists) from diff operations
    for persisting as node_details in workflow run history.  Lists are
    capped at _MAX_EVIDENCE_TRACKS with a total count for the remainder.
    """
    added: list[dict[str, object]] = []
    removed: list[dict[str, object]] = []
    moved = 0
    for op in diff.operations:
        if op.operation_type == PlaylistOperationType.MOVE:
            moved += 1
            continue
        track = op.track
        summary: dict[str, object] = {
            "track_id": track.id,
            "title": track.title or "Unknown",
            "artists": track.artists_display or "Unknown",
        }
        if op.operation_type == PlaylistOperationType.ADD:
            added.append(summary)
        elif op.operation_type == PlaylistOperationType.REMOVE:
            removed.append(summary)

    total_added = len(added)
    total_removed = len(removed)

    return {
        "tracks_added": added[:_MAX_EVIDENCE_TRACKS],
        "tracks_removed": removed[:_MAX_EVIDENCE_TRACKS],
        "tracks_added_total": total_added,
        "tracks_removed_total": total_removed,
        "tracks_moved": moved,
        "playlist_id": playlist_id,
        "connector": connector,
    }


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
    metadata: dict[str, JsonValue]
    operations_performed: int
    tracks_added: int
    success: bool = True

    @property
    def has_changes(self) -> bool:
        """Whether any tracks were added."""
        return self.tracks_added > 0
