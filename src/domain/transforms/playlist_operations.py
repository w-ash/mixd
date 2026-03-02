"""Pure functional playlist transformation operations.

This module contains immutable, side-effect free functions that operate on
Track entities and perform playlist-level reordering. These are pure domain
transforms with zero external dependencies.

All playlist operations follow functional programming principles:
- Immutability: Return new lists instead of modifying existing ones
- Purity: No side effects, logging, or external dependencies
"""

from src.domain.entities.track import Track


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
    current_track_instances: dict[int, list[Track]] = {}
    for track in current_tracks:
        if track.id is not None:
            if track.id not in current_track_instances:
                current_track_instances[track.id] = []
            current_track_instances[track.id].append(track)

    # Reconstruct list following target order with greedy instance matching
    reordered_tracks: list[Track] = []
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
