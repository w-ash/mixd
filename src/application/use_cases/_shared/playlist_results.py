"""Typed result objects and counters for playlist operations.

Replaces tuple-based returns with strongly-typed result objects using Python 3.13+
features for better type safety and maintainability. The coherent "result
assembly" surface for playlist mutations: the ``OperationCounts`` value object,
its ``count_operation_types`` producer, and ``build_playlist_changes`` evidence.
"""

from attrs import define

from src.domain.playlist.diff_engine import (
    PlaylistDiff,
    PlaylistOperation,
    PlaylistOperationType,
)

_MAX_EVIDENCE_TRACKS = 100
"""Cap per-list to avoid unbounded JSON in workflow run history."""


def build_playlist_changes(
    diff: PlaylistDiff, playlist_id: str, connector: str | None = None
) -> dict[str, object]:
    """Build lightweight playlist change evidence from a PlaylistDiff.

    Extracts track summaries (id, title, artists) from diff operations
    for persisting as node_details in workflow run history. Lists are
    capped at _MAX_EVIDENCE_TRACKS with a total count for the remainder.

    Values in the returned dict are strict-JSON types (``str``, ``int``,
    ``None``) — ``track.id`` is stringified at the boundary so in-process
    consumers (workflow preview, CLI rendering, unit tests) can rely on
    plain JSON-compatible values without further coercion. orjson handles
    raw UUID / datetime values at the JSONB write path natively (see
    ``db_connection.set_json_dumps``).
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
            "track_id": str(track.id),
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


def count_operation_types(operations: list[PlaylistOperation]) -> OperationCounts:
    """Count add/remove/move operations from a diff operations list.

    Analyzes a list of playlist operations and returns typed counts of each
    operation type for reporting and validation.

    Args:
        operations: List of PlaylistOperation objects with operation_type field.

    Returns:
        OperationCounts with added, removed, and moved counts.

    Example:
        >>> ops = [
        ...     PlaylistOperation(operation_type=PlaylistOperationType.ADD),
        ...     PlaylistOperation(operation_type=PlaylistOperationType.ADD),
        ...     PlaylistOperation(operation_type=PlaylistOperationType.REMOVE),
        ... ]
        >>> counts = count_operation_types(ops)
        >>> counts.added
        2
        >>> counts.removed
        1
    """
    added = sum(
        1 for op in operations if op.operation_type == PlaylistOperationType.ADD
    )
    removed = sum(
        1 for op in operations if op.operation_type == PlaylistOperationType.REMOVE
    )
    moved = sum(
        1 for op in operations if op.operation_type == PlaylistOperationType.MOVE
    )
    return OperationCounts(added=added, removed=removed, moved=moved)
