"""Factory methods for connector playlist entities.

Provides reusable factory functions for creating ConnectorPlaylistItem instances
from tracks, eliminating duplication across use cases.
"""

from datetime import UTC, datetime
from typing import Any

from src.domain.entities.playlist import ConnectorPlaylistItem
from src.domain.entities.track import Track


def create_connector_playlist_item_from_track(
    track: Track,
    position: int,
    connector_name: str,
    added_at: datetime | None = None,
    added_by_id: str = "narada",
) -> ConnectorPlaylistItem | None:
    """Create ConnectorPlaylistItem from track if it has connector identifier.

    Args:
        track: Track to create playlist item from
        position: Position in the playlist (0-indexed)
        connector_name: Name of the connector service (e.g., "spotify")
        added_at: When the track was added (defaults to now)
        added_by_id: User/service that added the track

    Returns:
        ConnectorPlaylistItem if track has connector ID, None otherwise
    """
    # Check if track has connector identifier
    if not track.connector_track_identifiers:
        return None

    connector_track_id = track.connector_track_identifiers.get(connector_name)
    if not connector_track_id:
        return None

    # Build the playlist item
    return ConnectorPlaylistItem(
        connector_track_identifier=connector_track_id,
        position=position,
        added_at=(added_at or datetime.now(UTC)).isoformat(),
        added_by_id=added_by_id,
        extras={
            "track_uri": f"{connector_name}:track:{connector_track_id}",
            "local": False,
        },
    )


def create_connector_playlist_items_from_tracks(
    tracks: list[Track],
    connector_name: str,
    added_at: datetime | None = None,
    added_by_id: str = "narada",
) -> list[ConnectorPlaylistItem]:
    """Batch create ConnectorPlaylistItems from track list.

    Filters out tracks without connector identifiers and creates items
    only for tracks that have the specified connector's ID.

    Args:
        tracks: List of tracks to create items from
        connector_name: Name of the connector service
        added_at: When tracks were added (defaults to now)
        added_by_id: User/service that added the tracks

    Returns:
        List of created ConnectorPlaylistItems (may be shorter than input)
    """
    items = []
    timestamp = added_at or datetime.now(UTC)

    for position, track in enumerate(tracks):
        item = create_connector_playlist_item_from_track(
            track=track,
            position=position,
            connector_name=connector_name,
            added_at=timestamp,
            added_by_id=added_by_id,
        )
        if item:
            items.append(item)

    return items


def create_connector_playlist_item_with_extras(
    track: Track,
    position: int,
    connector_name: str,
    extras: dict[str, Any],
    added_at: datetime | None = None,
    added_by_id: str = "narada",
) -> ConnectorPlaylistItem | None:
    """Create ConnectorPlaylistItem with custom extras metadata.

    Used when creating items with service-specific metadata like
    video thumbnails, primary colors, etc.

    Args:
        track: Track to create playlist item from
        position: Position in the playlist
        connector_name: Name of the connector service
        extras: Custom metadata dictionary for this item
        added_at: When the track was added
        added_by_id: User/service that added the track

    Returns:
        ConnectorPlaylistItem with merged extras, or None if no connector ID
    """
    # Get base item
    base_item = create_connector_playlist_item_from_track(
        track=track,
        position=position,
        connector_name=connector_name,
        added_at=added_at,
        added_by_id=added_by_id,
    )

    if not base_item:
        return None

    # Merge extras with base extras
    merged_extras = {**base_item.extras, **extras}

    # Return new item with merged extras
    return ConnectorPlaylistItem(
        connector_track_identifier=base_item.connector_track_identifier,
        position=base_item.position,
        added_at=base_item.added_at,
        added_by_id=base_item.added_by_id,
        extras=merged_extras,
    )
