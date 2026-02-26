"""Pure functional filtering transformations for track collections.

This module contains immutable, side-effect free filtering functions that operate
solely on Track and TrackList domain entities. These are pure domain transforms
with zero external dependencies.

All filters follow functional programming principles:
- Immutability: Return new TrackList instead of modifying existing ones
- Composition: Can be combined with other transforms via create_pipeline
- Currying: Designed for partial application with toolz.curry
- Purity: No side effects, logging, or external dependencies
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

from src.domain.entities.track import Track, TrackList
from src.domain.transforms.core import Transform, optional_tracklist_transform


@optional_tracklist_transform
def filter_by_predicate(predicate: Callable[[Track], bool]) -> Transform:
    """
    Filter tracks based on a predicate function.

    Args:
        predicate: Function returning True for tracks to keep

    Returns:
        Transformation function
    """

    def transform(t: TrackList) -> TrackList:
        filtered = [track for track in t.tracks if predicate(track)]
        return t.with_tracks(filtered)

    return transform


@optional_tracklist_transform
def filter_duplicates() -> Transform:
    """
    Remove duplicate tracks from a tracklist.

    Returns:
        Transformation function
    """

    def transform(t: TrackList) -> TrackList:
        seen_ids: set[int] = set()
        unique_tracks: list[Track] = []
        duplicates_removed = 0
        original_count = len(t.tracks)
        tracks_without_ids = 0

        for track in t.tracks:
            if track.id is None:
                # If track has no ID, keep it (can't properly deduplicate)
                unique_tracks.append(track)
                tracks_without_ids += 1
            elif track.id not in seen_ids:
                seen_ids.add(track.id)
                unique_tracks.append(track)
            else:
                duplicates_removed += 1

        result = t.with_tracks(unique_tracks)
        # Add metadata for reporting
        return (
            result
            .with_metadata("duplicates_removed", duplicates_removed)
            .with_metadata("original_count", original_count)
            .with_metadata("tracks_without_ids", tracks_without_ids)
        )

    return transform


@optional_tracklist_transform
def filter_by_date_range(
    min_age_days: int | None = None,
    max_age_days: int | None = None,
) -> Transform:
    """
    Filter tracks by release date range.

    Args:
        min_age_days: Minimum age in days (None for no minimum)
        max_age_days: Maximum age in days (None for no maximum)

    Returns:
        Transformation function
    """

    def in_date_range(track: Track) -> bool:
        if not track.release_date:
            return False

        age_days = (datetime.now(UTC) - track.release_date).days

        if max_age_days is not None and age_days > max_age_days:
            return False

        return not (min_age_days is not None and age_days < min_age_days)

    # cast: calling filter_by_predicate without tracklist always returns Transform
    return cast(Transform, filter_by_predicate(in_date_range))


@optional_tracklist_transform
def exclude_tracks(reference_tracks: list[Track]) -> Transform:
    """
    Filter out tracks that exist in a reference collection.

    Args:
        reference_tracks: List of tracks to exclude

    Returns:
        Transformation function
    """
    exclude_ids = {track.id for track in reference_tracks if track.id}

    def not_in_reference(track: Track) -> bool:
        return track.id not in exclude_ids

    return cast(Transform, filter_by_predicate(not_in_reference))


@optional_tracklist_transform
def exclude_artists(
    reference_tracks: list[Track],
    exclude_all_artists: bool = False,
) -> Transform:
    """
    Filter out tracks whose artists appear in a reference collection.

    Args:
        reference_tracks: List of tracks with artists to exclude
        exclude_all_artists: If True, checks all artists on a track, not just primary

    Returns:
        Transformation function
    """
    # Create set of artist names to exclude (case-insensitive)
    exclude_artists_set: set[str] = set()

    for track in reference_tracks:
        if not track.artists:
            continue

        if exclude_all_artists:
            # Add all artists from the track
            exclude_artists_set.update(artist.name.lower() for artist in track.artists)
        else:
            # Add only the primary artist
            exclude_artists_set.add(track.artists[0].name.lower())

    def not_artist_in_reference(track: Track) -> bool:
        if not track.artists:
            return True

        if exclude_all_artists:
            # Check if any artist on the track is in the exclusion set
            return not any(
                artist.name.lower() in exclude_artists_set for artist in track.artists
            )
        else:
            # Check only the primary artist
            return track.artists[0].name.lower() not in exclude_artists_set

    return cast(Transform, filter_by_predicate(not_artist_in_reference))
