"""Pure functional playlist transformation operations.

This module contains immutable, side-effect free functions that operate on
Playlist entities and perform playlist-level transformations. These are pure
domain transforms with zero external dependencies.

All playlist operations follow functional programming principles:
- Immutability: Return new Playlist instead of modifying existing ones
- Composition: Can be combined with other transforms via create_pipeline
- Currying: Designed for partial application with toolz.curry
- Purity: No side effects, logging, or external dependencies
"""

from collections.abc import Callable

from toolz import curry

from src.domain.entities.playlist import Playlist
from src.domain.entities.track import Track


@curry
def calculate_track_list_diff(
    current_tracks: list[Track],
    target_tracks: list[Track],
) -> tuple[list[Track], list[Track], list[Track]]:
    """Calculate pure diff between track lists without database operations.

    Pure functional transform that identifies added, removed, and common tracks
    based on track identity. Repository layer handles database-aware matching.

    Args:
        current_tracks: Current ordered list of tracks
        target_tracks: Target ordered list of tracks

    Returns:
        Tuple of (tracks_to_remove, tracks_to_add, tracks_in_common)
    """
    # Create sets for efficient lookup by track ID
    current_ids = {track.id for track in current_tracks if track.id is not None}
    target_ids = {track.id for track in target_tracks if track.id is not None}

    # Calculate set differences
    ids_to_remove = current_ids - target_ids
    ids_to_add = target_ids - current_ids
    ids_in_common = current_ids & target_ids

    # Convert back to track lists maintaining order
    tracks_to_remove = [t for t in current_tracks if t.id in ids_to_remove]
    tracks_to_add = [t for t in target_tracks if t.id in ids_to_add]
    tracks_in_common = [t for t in current_tracks if t.id in ids_in_common]

    return tracks_to_remove, tracks_to_add, tracks_in_common


def reorder_to_match_target(
    current_tracks: list[Track],
    target_tracks: list[Track],
) -> list[Track]:
    """Reorder current tracks to match target track order.

    Pure functional transform that reconstructs track list in target order,
    preserving existing track instances where possible and adding new tracks.
    Handles duplicates correctly by using greedy matching to preserve instances.

    Args:
        current_tracks: Current ordered list of tracks
        target_tracks: Target ordered list of tracks (desired final order)

    Returns:
        List of tracks reordered to match target, preserving track instances
    """
    # Create mapping from track ID to ALL available instances (handles duplicates)
    current_track_instances = {}
    for track in current_tracks:
        if track.id is not None:
            if track.id not in current_track_instances:
                current_track_instances[track.id] = []
            current_track_instances[track.id].append(track)

    # Reconstruct list following target order with greedy instance matching
    reordered_tracks = []
    for target_track in target_tracks:
        if (
            target_track.id is not None
            and target_track.id in current_track_instances
            and current_track_instances[target_track.id]
        ):
            # Use the first available existing track instance to preserve metadata/relationships
            existing_track = current_track_instances[target_track.id].pop(0)
            reordered_tracks.append(existing_track)
        else:
            # New track to add - use target track instance
            reordered_tracks.append(target_track)

    return reordered_tracks


@curry
def rename(
    new_name: str,
    playlist: Playlist | None = None,
) -> Callable[[Playlist], Playlist] | Playlist:
    """
    Set playlist name.

    Args:
        new_name: New playlist name
        playlist: Optional playlist to transform immediately

    Returns:
        Transformation function or transformed playlist if provided
    """

    def transform(p: Playlist) -> Playlist:
        return Playlist(
            name=new_name,
            tracks=p.tracks,
            description=p.description,
            id=p.id,
            connector_playlist_identifiers=p.connector_playlist_identifiers.copy(),
        )

    return transform(playlist) if playlist is not None else transform


@curry
def set_description(
    description: str,
    playlist: Playlist | None = None,
) -> Callable[[Playlist], Playlist] | Playlist:
    """
    Set playlist description.

    Args:
        description: New playlist description
        playlist: Optional playlist to transform immediately

    Returns:
        Transformation function or transformed playlist if provided
    """

    def transform(p: Playlist) -> Playlist:
        return Playlist(
            name=p.name,
            tracks=p.tracks,
            description=description,
            id=p.id,
            connector_playlist_identifiers=p.connector_playlist_identifiers.copy(),
        )

    return transform(playlist) if playlist is not None else transform