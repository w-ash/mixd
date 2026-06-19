"""Metadata for external playlist-API executions."""

from datetime import UTC, datetime

from src.domain.entities.shared import JsonValue


def build_api_execution_metadata(
    operations_count: int,
    snapshot_id: str | None,
    tracks_added: int,
    tracks_removed: int,
    tracks_moved: int,
    validation_passed: bool,
    operations_dropped: int = 0,
) -> dict[str, JsonValue]:
    """Build the metadata dict describing one external playlist-API execution.

    A flat literal — the former ``PlaylistMetadataBuilder`` fluent chain had a
    single caller (this function) and a dead ``build()`` terminal, so it was
    collapsed to plain construction.

    ``operations_dropped`` are operations requested but never submitted (a track
    with no connector mapping, validation/bounds filtering). They are reported so
    callers can surface "N synced, M unmapped"; ``operations_applied`` excludes
    them so it reflects what actually reached the connector.
    """
    now = datetime.now(UTC).isoformat()
    return {
        "last_modified": now,
        "database_update_timestamp": now,
        "operations_requested": operations_count,
        "operations_applied": (operations_count - operations_dropped)
        if validation_passed
        else 0,
        "operations_dropped": operations_dropped,
        "snapshot_id": snapshot_id,
        "tracks_added": tracks_added,
        "tracks_removed": tracks_removed,
        "tracks_moved": tracks_moved,
        "validation_passed": validation_passed,
    }
