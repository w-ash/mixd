"""Pure domain logic for playlist operations.

These functions contain only business logic with no external dependencies,
making them easy to unit test without mocking.
"""

from typing import Any

from src.domain.entities import Playlist, Track, TrackList


def create_playlist_operation(
    tracklist: TrackList, config: dict[str, Any], persisted_tracks: list[Track]
) -> Playlist:
    """Pure business logic for creating a playlist.

    Args:
        tracklist: Original tracklist (for metadata)
        config: Playlist configuration
        persisted_tracks: Already persisted tracks with IDs

    Returns:
        Playlist entity ready for persistence
    """
    _ = tracklist  # Reserved for future metadata extraction
    name = config.get("name", "Narada Playlist")
    description = config.get("description", "Created by Narada")

    return Playlist.from_tracklist(
        name=name,
        tracklist=persisted_tracks,
        description=description,
    )


def create_spotify_playlist_operation(
    tracklist: TrackList,
    config: dict[str, Any],
    persisted_tracks: list[Track],
    spotify_id: str,
) -> Playlist:
    """Pure business logic for creating a Spotify-connected playlist.

    Args:
        tracklist: Original tracklist (for metadata)
        config: Playlist configuration
        persisted_tracks: Already persisted tracks with IDs
        spotify_id: Spotify playlist ID from external API

    Returns:
        Playlist entity with Spotify connection ready for persistence
    """
    _ = tracklist  # Reserved for future metadata extraction
    name = config.get("name", "Narada Playlist")
    description = config.get("description", "Created by Narada")

    playlist = Playlist.from_tracklist(
        name=name,
        tracklist=persisted_tracks,
        description=description,
    )
    # Add Spotify connector identifier
    return Playlist(
        name=playlist.name,
        entries=playlist.entries,
        description=playlist.description,
        connector_playlist_identifiers={"spotify": spotify_id},
    )


def calculate_track_persistence_stats(
    original_tracks: list[Track], persisted_tracks: list[Track]
) -> dict[str, int]:
    """Pure business logic for calculating persistence statistics.

    Args:
        original_tracks: Tracks before persistence
        persisted_tracks: Tracks after persistence

    Returns:
        Dictionary with persistence statistics
    """
    stats = {"new_tracks": 0, "updated_tracks": 0}

    for original, persisted in zip(original_tracks, persisted_tracks, strict=False):
        if original.id != persisted.id:
            if original.id is None:
                stats["new_tracks"] += 1
            else:
                stats["updated_tracks"] += 1

    return stats


def format_destination_result(
    operation_type: str,
    playlist: Playlist,
    tracklist: TrackList,
    persisted_tracks: list[Track],
    stats: dict[str, int],
    **additional_fields,
) -> dict[str, Any]:
    """Pure business logic for formatting destination operation results.

    Args:
        operation_type: Type of operation performed
        playlist: Created/updated playlist
        tracklist: Original tracklist (for metadata preservation)
        persisted_tracks: Tracks that were persisted
        stats: Persistence statistics
        **additional_fields: Additional result fields

    Returns:
        Formatted result dictionary
    """
    # Preserve original metadata in result
    result_tracklist = TrackList(tracks=persisted_tracks, metadata=tracklist.metadata)

    base_result = {
        "playlist_id": playlist.id,
        "playlist_name": playlist.name,
        "track_count": len(persisted_tracks),
        "operation": operation_type,
        "tracklist": result_tracklist,
        **stats,
    }

    # Add any additional fields
    base_result.update(additional_fields)

    return base_result


def format_spotify_destination_result(
    playlist: Playlist,
    tracklist: TrackList,
    persisted_tracks: list[Track],
    stats: dict[str, int],
    spotify_id: str,
) -> dict[str, Any]:
    """Format result for Spotify destination operations."""
    return format_destination_result(
        operation_type="create_spotify_playlist",
        playlist=playlist,
        tracklist=tracklist,
        persisted_tracks=persisted_tracks,
        stats=stats,
        spotify_playlist_id=spotify_id,
    )


def update_playlist_tracks_operation(
    existing_playlist: Playlist, new_tracks: list[Track], append_mode: bool = False
) -> Playlist:
    """Pure business logic for updating playlist tracks.

    Args:
        existing_playlist: Current playlist
        new_tracks: New tracks to add
        append_mode: If True, append tracks; if False, replace tracks

    Returns:
        Updated playlist entity
    """
    from datetime import UTC, datetime

    from src.domain.entities.playlist import PlaylistEntry

    if append_mode:
        # Preserve existing entries, add new ones with current timestamp
        existing_entries = list(existing_playlist.entries)
        new_entries = [
            PlaylistEntry(track=track, added_at=datetime.now(UTC))
            for track in new_tracks
        ]
        updated_entries = existing_entries + new_entries
    else:
        # Replace all tracks with new timestamp
        updated_entries = [
            PlaylistEntry(track=track, added_at=datetime.now(UTC))
            for track in new_tracks
        ]

    return existing_playlist.with_entries(updated_entries)


def format_update_destination_result(
    playlist: Playlist,
    tracklist: TrackList,
    persisted_tracks: list[Track],
    stats: dict[str, int],
    spotify_id: str,
    append_mode: bool,
    original_track_count: int,
) -> dict[str, Any]:
    """Format result for Spotify update destination operations."""
    return format_destination_result(
        operation_type="update_spotify_playlist",
        playlist=playlist,
        tracklist=tracklist,
        persisted_tracks=persisted_tracks,
        stats=stats,
        spotify_playlist_id=spotify_id,
        append_mode=append_mode,
        original_count=original_track_count,
    )
