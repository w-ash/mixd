"""
Pure functional transformations for playlists and tracks.

This module contains immutable, side-effect free functions that transform
domain models. These primitives form the foundation of our workflow system.

Transformations follow functional programming principles:
- Immutability: All operations return new objects instead of modifying existing ones
- Composition: Transformations can be combined to form complex pipelines
- Currying: Functions are designed to work with partial application
- Purity: No side effects or external dependencies
"""

import random  # noqa: I001
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar, cast

from toolz import compose_left, curry, get_in

from src.config import get_logger

from src.domain.entities.playlist import Playlist
from src.domain.entities.track import Track, TrackList

logger = get_logger(__name__)

# Type variables for generic transformations
T = TypeVar("T", bound=TrackList)

# Type alias for transformation functions
Transform = Callable[[TrackList], TrackList]


# === Core Pipeline Functions ===


def create_pipeline(*operations: Transform) -> Transform:
    """
    Compose multiple transformations into a single operation.

    Args:
        *operations: Transformation functions to compose

    Returns:
        A single transformation function combining all operations
    """
    return compose_left(*operations)


# === Track Filtering ===


@curry
def filter_by_predicate(
    predicate: Callable[[Track], bool],
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """
    Filter tracks based on a predicate function.

    Args:
        predicate: Function returning True for tracks to keep
        tracklist: Optional tracklist to transform immediately

    Returns:
        Transformation function or transformed tracklist if provided
    """

    def transform(t: TrackList) -> TrackList:
        filtered = [track for track in t.tracks if predicate(track)]
        return t.with_tracks(filtered)

    if tracklist is not None:
        return transform(tracklist)
    return transform


@curry
def filter_duplicates(tracklist: TrackList | None = None) -> Transform | TrackList:
    """
    Remove duplicate tracks from a tracklist.

    Args:
        tracklist: Optional tracklist to transform immediately

    Returns:
        Transformation function or transformed tracklist if provided
    """

    def transform(t: TrackList) -> TrackList:
        seen_ids = set()
        unique_tracks = []
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
            result.with_metadata("duplicates_removed", duplicates_removed)
            .with_metadata("original_count", original_count)
            .with_metadata("tracks_without_ids", tracks_without_ids)
        )

    return transform(tracklist) if tracklist is not None else transform


@curry
def filter_by_date_range(
    min_age_days: int | None = None,
    max_age_days: int | None = None,
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """
    Filter tracks by release date range.

    Args:
        min_age_days: Minimum age in days (None for no minimum)
        max_age_days: Maximum age in days (None for no maximum)
        tracklist: Optional tracklist to transform immediately

    Returns:
        Transformation function or transformed tracklist if provided
    """

    def in_date_range(track: Track) -> bool:
        if not track.release_date:
            return False

        age_days = (datetime.now(UTC) - track.release_date).days

        if max_age_days is not None and age_days > max_age_days:
            return False

        return not (min_age_days is not None and age_days < min_age_days)

    return cast("Transform | TrackList", filter_by_predicate(in_date_range, tracklist))


@curry
def exclude_tracks(
    reference_tracks: list[Track],
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """
    Filter out tracks that exist in a reference collection.

    Args:
        reference_tracks: List of tracks to exclude
        tracklist: Optional tracklist to transform immediately

    Returns:
        Transformation function or transformed tracklist if provided
    """
    exclude_ids = {track.id for track in reference_tracks if track.id}

    def not_in_reference(track: Track) -> bool:
        return track.id not in exclude_ids

    return cast(
        "Transform | TrackList",
        filter_by_predicate(not_in_reference, tracklist),
    )


@curry
def exclude_artists(
    reference_tracks: list[Track],
    exclude_all_artists: bool = False,
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """
    Filter out tracks whose artists appear in a reference collection.

    Args:
        reference_tracks: List of tracks with artists to exclude
        exclude_all_artists: If True, checks all artists on a track, not just primary
        tracklist: Optional tracklist to transform immediately

    Returns:
        Transformation function or transformed tracklist if provided
    """
    # Create set of artist names to exclude (case-insensitive)
    exclude_artists = set()

    for track in reference_tracks:
        if not track.artists:
            continue

        if exclude_all_artists:
            # Add all artists from the track
            exclude_artists.update(artist.name.lower() for artist in track.artists)
        else:
            # Add only the primary artist
            exclude_artists.add(track.artists[0].name.lower())

    def not_artist_in_reference(track: Track) -> bool:
        if not track.artists:
            return True

        if exclude_all_artists:
            # Check if any artist on the track is in the exclusion set
            return not any(
                artist.name.lower() in exclude_artists for artist in track.artists
            )
        else:
            # Check only the primary artist
            return track.artists[0].name.lower() not in exclude_artists

    return cast(
        "Transform | TrackList",
        filter_by_predicate(not_artist_in_reference, tracklist),
    )


@curry
def filter_by_metric_range(
    metric_name: str,
    min_value: float | None = None,
    max_value: float | None = None,
    include_missing: bool = False,
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """
    Filter tracks based on a metric value range.

    Args:
        metric_name: Name of the metric to filter by (e.g., 'lastfm_user_playcount')
        min_value: Minimum value (inclusive), or None for no minimum
        max_value: Maximum value (inclusive), or None for no maximum
        include_missing: Whether to include tracks without the metric
        tracklist: Optional tracklist to transform immediately

    Returns:
        Transformation function or transformed tracklist if provided
    """

    def is_in_range(track: Track) -> bool:
        """Check if track's metric is within the specified range."""
        if not track.id:
            return include_missing

        # Get the metrics dictionary from the tracklist metadata
        metrics = {} if tracklist is None else tracklist.metadata.get("metrics", {})
        metric_values = metrics.get(metric_name, {})

        # Check if track has the metric
        if track.id not in metric_values:
            return include_missing

        value = metric_values[track.id]

        # Check range bounds
        if min_value is not None and value < min_value:
            return False

        return not (max_value is not None and value > max_value)

    def transform(t: TrackList) -> TrackList:
        """Apply the metric filter transformation."""
        # Set the tracklist for metric lookup in is_in_range
        nonlocal tracklist
        tracklist = t

        # Apply filter
        filter_func = cast("Transform", filter_by_predicate(is_in_range))
        result = filter_func(t)

        # Add metadata about the filter operation
        filtered_count = len(result.tracks)
        return cast("TrackList", result).with_metadata(
            "filter_metrics",
            {
                "metric_name": metric_name,
                "min_value": min_value,
                "max_value": max_value,
                "include_missing": include_missing,
                "original_count": len(t.tracks),
                "filtered_count": filtered_count,
                "removed_count": len(t.tracks) - filtered_count,
            },
        )

    return transform(tracklist) if tracklist is not None else transform


# === Track Sorting ===


@curry
def sort_by_attribute(
    key_fn: Callable[[Track], Any] | str,
    metric_name: str,
    reverse: bool = False,
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """Sort tracks by any attribute or derived value.

    Args:
        key_fn: Function to extract sort key or metric name string
        metric_name: Name for tracking metrics in tracklist metadata
        reverse: Whether to sort in descending order
        tracklist: Optional tracklist to transform immediately

    Returns:
        Transformation function or transformed tracklist if provided
    """

    # Allow passing metric name directly for common use cases
    if isinstance(key_fn, str):
        stored_metric_name = key_fn

        def metric_key_fn(track: Track) -> Any:
            return _extract_track_metric(track, stored_metric_name, 0)

        key_fn = metric_key_fn

    def _extract_track_metric(track: Track, metric_key: str, default: Any = 0) -> Any:
        """Extract metric from track or its metadata.

        This function is used during the initial key_fn setup phase,
        but the actual metric extraction is handled by enhanced_key_fn
        which has access to the tracklist metadata.
        """
        if not track.id:
            return default

        # First try track's own properties
        if hasattr(track, metric_key) and getattr(track, metric_key) is not None:
            return getattr(track, metric_key)

        # Note: Tracklist metadata metrics are handled by enhanced_key_fn
        # This function is only used as a fallback for track attributes
        return default

    def transform(t: TrackList) -> TrackList:
        """Apply the sorting transformation with metrics-driven approach."""
        # Simply use metrics that were resolved at the node boundary
        metrics_dict = t.metadata.get("metrics", {}).get(metric_name, {})

        # Validate that metrics keys are integers (our expected format)
        if metrics_dict:
            # Check if metrics dictionary has string keys (which is an error)
            string_keys = [k for k in metrics_dict if isinstance(k, str)]
            if string_keys:
                # Instead of silently working around this, throw an error so we can fix it
                # at the source - track IDs should be integers
                sample_keys = string_keys[:5]
                raise TypeError(
                    f"Metrics dictionary contains string keys instead of integer track IDs: {sample_keys}. "
                    f"This indicates an upstream issue in metric resolution or storage."
                )

        # Check if we have any non-null values

        # Create enhanced key function that prioritizes metrics
        def enhanced_key_fn(track: Track) -> Any:
            if not track.id:
                return key_fn(track)

            # Check if track ID exists in metrics - track IDs should be integers
            logger.debug(
                f"Sorting track {track.id} (type: {type(track.id)}), metric_dict keys: {list(metrics_dict.keys())[:5]}..."
            )
            if track.id in metrics_dict:
                # Use resolved metric if it exists and isn't None
                metric_value = metrics_dict[track.id]
                if metric_value is not None:
                    logger.debug(
                        f"Using metric value {metric_value} for track {track.id}"
                    )
                    return metric_value

            # For missing or None values, use a default that will sort appropriately
            logger.debug(f"Track {track.id} missing from metrics, using fallback value")
            if reverse:
                # When sorting in descending order (reverse=True), put None values at the end
                return float("-inf")  # Lowest possible value
            else:
                # When sorting in ascending order, put None values at the end
                return float("inf")  # Highest possible value

        # Sort tracks using the enhanced key function
        sorted_tracks = sorted(t.tracks, key=enhanced_key_fn, reverse=reverse)
        result = t.with_tracks(sorted_tracks)

        # Store metrics in tracklist metadata (preserving existing metrics)
        # Use integer track IDs for consistency
        track_metrics = {
            track.id: enhanced_key_fn(track)
            for track in t.tracks
            if track.id is not None
        }

        result = result.with_metadata(
            "metrics",
            {
                **result.metadata.get("metrics", {}),
                metric_name: track_metrics,
            },
        )

        return result

    return transform(tracklist) if tracklist is not None else transform


# === Track Selection ===


@curry
def limit(count: int, tracklist: TrackList | None = None) -> Transform | TrackList:
    """
    Limit to the first n tracks.

    Args:
        count: Maximum number of tracks to keep
        tracklist: Optional tracklist to transform immediately

    Returns:
        Transformation function or transformed tracklist if provided
    """

    def transform(t: TrackList) -> TrackList:
        return t.with_tracks(t.tracks[:count])

    return transform(tracklist) if tracklist is not None else transform


@curry
def take_last(
    count: int,
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """
    Take the last n tracks.

    Args:
        count: Number of tracks to keep from the end
        tracklist: Optional tracklist to transform immediately

    Returns:
        Transformation function or transformed tracklist if provided
    """

    def transform(t: TrackList) -> TrackList:
        n = min(count, len(t.tracks))
        return t.with_tracks(t.tracks[-n:])

    return transform(tracklist) if tracklist is not None else transform


@curry
def sample_random(
    count: int,
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """
    Randomly sample n tracks.

    Args:
        count: Number of tracks to sample
        tracklist: Optional tracklist to transform immediately

    Returns:
        Transformation function or transformed tracklist if provided
    """

    def transform(t: TrackList) -> TrackList:
        n = min(count, len(t.tracks))
        selected = random.sample(t.tracks, n)
        return t.with_tracks(selected)

    return transform(tracklist) if tracklist is not None else transform


@curry
def select_by_method(
    count: int,
    method: str = "first",
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """
    Select tracks using specified method.

    Args:
        count: Number of tracks to select
        method: Selection method ("first", "last", or "random")
        tracklist: Optional tracklist to transform immediately

    Returns:
        Transformation function or transformed tracklist if provided
    """
    if method == "first":
        transform_fn = limit(count)
    elif method == "last":
        transform_fn = take_last(count)
    elif method == "random":
        transform_fn = sample_random(count)
    else:
        raise ValueError(f"Invalid selection method: {method}")

    def transform(t: TrackList) -> TrackList:
        result = cast("Transform", transform_fn)(t)
        return (
            cast("TrackList", result)
            .with_metadata("selection_method", method)
            .with_metadata("original_count", len(t.tracks))
        )

    return transform(tracklist) if tracklist is not None else transform


# === Track List Combination ===


@curry
def concatenate(
    tracklists: list[TrackList],
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """
    Concatenate multiple tracklists.

    Args:
        tracklists: List of tracklists to combine
        tracklist: Optional tracklist to prepend (usually ignored)

    Returns:
        Transformation function or transformed tracklist if provided
    """

    def transform(_: TrackList) -> TrackList:
        all_tracks = []
        combined_track_sources = {}

        for t in tracklists:
            all_tracks.extend(t.tracks)
            # Merge track source information from each tracklist
            track_sources = t.metadata.get("track_sources", {})
            combined_track_sources.update(track_sources)

        return TrackList(
            tracks=all_tracks,
            metadata={
                "operation": "concatenate",
                "source_count": len(tracklists),
                "track_sources": combined_track_sources,
            },
        )

    return transform(tracklist or TrackList()) if tracklist is not None else transform


@curry
def interleave(
    tracklists: list[TrackList],
    stop_on_empty: bool = False,
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """
    Interleave tracks from multiple tracklists.

    Args:
        tracklists: List of tracklists to interleave
        stop_on_empty: Whether to stop when any tracklist is exhausted
        tracklist: Optional tracklist to transform (usually ignored)

    Returns:
        Transformation function or transformed tracklist if provided
    """

    def transform(_: TrackList) -> TrackList:
        interleaved_tracks = []
        iterators = [iter(t.tracks) for t in tracklists]
        exhausted = [False] * len(tracklists)

        while not all(exhausted) and not (stop_on_empty and any(exhausted)):
            for i, track_iter in enumerate(iterators):
                if exhausted[i]:
                    continue

                try:
                    track = next(track_iter)
                    interleaved_tracks.append(track)
                except StopIteration:
                    exhausted[i] = True
                    if stop_on_empty:
                        break

        return TrackList(
            tracks=interleaved_tracks,
            metadata={
                "operation": "alternate",
                "source_count": len(tracklists),
                "stop_on_empty": stop_on_empty,
            },
        )

    return transform(tracklist or TrackList()) if tracklist is not None else transform


# === Playlist Operations ===


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
            connector_playlist_ids=p.connector_playlist_ids.copy(),
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
            connector_playlist_ids=p.connector_playlist_ids.copy(),
        )

    return transform(playlist) if playlist is not None else transform


# === Play History Filtering ===


@curry
def time_range_predicate(
    days_back: int | None = None,
    after_date: datetime | None = None,
    before_date: datetime | None = None,
) -> Callable[[datetime | None], bool]:
    """Create time-based predicates using toolz functional utilities.

    Args:
        days_back: Number of days back from current time (overrides after_date)
        after_date: Include items after this date (inclusive)
        before_date: Include items before this date (exclusive)

    Returns:
        Predicate function that tests if a datetime falls within the range
    """
    from datetime import timedelta

    # Determine time boundaries
    if days_back is not None:
        start_time = datetime.now(UTC) - timedelta(days=days_back)
        end_time = before_date or datetime.now(UTC)
    else:
        start_time = after_date
        end_time = before_date

    def time_predicate(dt: datetime | None) -> bool:
        if dt is None:
            return False
        if start_time and dt < start_time:
            return False
        return not (end_time and dt >= end_time)

    return time_predicate


@curry
def filter_by_time_criteria(
    metadata_key: str,
    days_back: int | None = None,
    after_date: datetime | None = None,
    before_date: datetime | None = None,
    include_missing: bool = True,
    tracklist: TrackList | None = None,
) -> Callable[[TrackList], TrackList] | TrackList:
    """Filter tracks by time-based criteria using existing predicate pattern.

    Args:
        metadata_key: Key in tracklist metadata containing time data (e.g. "last_played_dates")
        days_back: Number of days back from current time (overrides after_date)
        after_date: Include tracks with time after this date
        before_date: Include tracks with time before this date
        include_missing: Whether to include tracks without time data
        tracklist: Optional tracklist to transform immediately

    Returns:
        Transformation function or transformed tracklist if provided
    """

    # Create time predicate using toolz
    time_pred: Callable[[datetime | None], bool] = time_range_predicate(
        days_back, after_date, before_date
    )  # type: ignore[assignment]

    def track_time_predicate(track: Track, current_tracklist: TrackList) -> bool:
        if not track.id:
            return include_missing

        # Use toolz get_in for safer nested access
        time_data = get_in([metadata_key, track.id], current_tracklist.metadata)

        if time_data is None:
            return include_missing

        # Handle string to datetime conversion with robust parsing
        if isinstance(time_data, str):
            try:
                # Try ISO format first (most common)
                time_data = datetime.fromisoformat(time_data)
            except ValueError:
                try:
                    # Try parsing as timestamp
                    timestamp = float(time_data)
                    time_data = datetime.fromtimestamp(timestamp, tz=UTC)
                except (ValueError, TypeError):
                    logger.debug(
                        "Failed to parse time data",
                        time_data=time_data,
                        metadata_key=metadata_key,
                    )
                    return include_missing
        elif not isinstance(time_data, datetime):
            logger.debug(
                "Invalid time data type",
                time_data_type=type(time_data),
                metadata_key=metadata_key,
            )
            return include_missing

        return time_pred(time_data)

    def transform(t: TrackList) -> TrackList:
        """Apply time-based filtering using existing predicate infrastructure."""

        # Create predicate function that captures current tracklist
        def predicate_with_tracklist(track: Track) -> bool:
            return track_time_predicate(track, t)

        # Use existing filter_by_predicate with our time predicate
        filter_func: Transform = filter_by_predicate(predicate_with_tracklist)  # type: ignore[assignment]
        result: TrackList = filter_func(t)

        # Add filter metadata
        return result.with_metadata(
            "time_filter_applied",
            {
                "metadata_key": metadata_key,
                "days_back": days_back,
                "after_date": after_date.isoformat() if after_date else None,
                "before_date": before_date.isoformat() if before_date else None,
                "include_missing": include_missing,
                "original_count": len(t.tracks),
                "filtered_count": len(result.tracks),
                "removed_count": len(t.tracks) - len(result.tracks),
            },
        )

    return transform(tracklist) if tracklist is not None else transform


@curry
def filter_by_play_history(
    min_plays: int | None = None,
    max_plays: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    min_days_back: int | None = None,
    max_days_back: int | None = None,
    include_missing: bool = False,
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """Filter tracks by play count and/or listening date constraints.

    Unified filter with three clear time window modes:
    - None: No date fields = all-time play counts
    - Absolute: start_date/end_date = ISO date strings
    - Relative: min_days_back/max_days_back = integer days from today

    Args:
        min_plays: Minimum play count (inclusive)
        max_plays: Maximum play count (inclusive)
        start_date: Include tracks played after this ISO date (absolute mode)
        end_date: Include tracks played before this ISO date (absolute mode)
        min_days_back: Start of time window, days from today (relative mode)
        max_days_back: End of time window, days from today (relative mode)
        include_missing: Whether to include tracks with no play data
        tracklist: Optional tracklist to transform immediately

    Returns:
        Transformation function or transformed tracklist if provided

    Examples:
        # Tracks played 5+ times in last month
        filter_by_play_history(min_plays=5, max_days_back=30)

        # Hidden gems: loved but not played recently
        filter_by_play_history(min_plays=3, min_days_back=180)

        # Tracks played 1-3 times between specific dates
        filter_by_play_history(
            min_plays=1, max_plays=3,
            start_date="2024-01-01",
            end_date="2024-03-31"
        )

        # Current obsessions: 8+ plays in last month
        filter_by_play_history(min_plays=8, max_days_back=30)
    """
    from datetime import timedelta

    # Validate at least one constraint is specified
    constraints = [
        min_plays is not None,
        max_plays is not None,
        start_date is not None,
        end_date is not None,
        min_days_back is not None,
        max_days_back is not None,
    ]
    if not any(constraints):
        raise ValueError(
            "Must specify at least one constraint: "
            "min_plays, max_plays, start_date, end_date, min_days_back, or max_days_back"
        )

    def transform(t: TrackList) -> TrackList:
        """Apply unified play history filtering."""
        # Calculate effective date range using clearer logic
        effective_after = None
        effective_before = None

        # Relative time mode takes precedence (clearer for users)
        # min_days_back = start of time window (furthest back, sets effective_before)
        # max_days_back = end of time window (closest to today, sets effective_after)
        if min_days_back is not None:
            effective_before = datetime.now(UTC) - timedelta(days=min_days_back)
        elif start_date is not None:
            try:
                effective_after = datetime.fromisoformat(start_date)
                if effective_after.tzinfo is None:
                    effective_after = effective_after.replace(tzinfo=UTC)
            except ValueError as e:
                raise ValueError(
                    f"Invalid start_date format: {start_date}. Use ISO format like '2024-01-01'"
                ) from e

        if max_days_back is not None:
            effective_after = datetime.now(UTC) - timedelta(days=max_days_back)
        elif end_date is not None:
            try:
                effective_before = datetime.fromisoformat(end_date)
                if effective_before.tzinfo is None:
                    effective_before = effective_before.replace(tzinfo=UTC)
            except ValueError as e:
                raise ValueError(
                    f"Invalid end_date format: {end_date}. Use ISO format like '2024-01-01'"
                ) from e

        # Get play data from metadata (try both nested and flat structure)
        play_counts = t.metadata.get("total_plays", {}) or t.metadata.get(
            "metrics", {}
        ).get("total_plays", {})
        last_played_dates = t.metadata.get("last_played_dates", {}) or t.metadata.get(
            "metrics", {}
        ).get("last_played_dates", {})

        def meets_play_history_criteria(track: Track) -> bool:
            if not track.id:
                return include_missing

            # Apply play count constraints
            if min_plays is not None or max_plays is not None:
                play_count = play_counts.get(track.id, 0)

                if min_plays is not None and play_count < min_plays:
                    return False
                if max_plays is not None and play_count > max_plays:
                    return False

            # Apply date constraints
            if effective_after is not None or effective_before is not None:
                last_played = last_played_dates.get(track.id)

                if last_played is None:
                    return include_missing

                # Convert string to datetime if needed
                if isinstance(last_played, str):
                    try:
                        last_played = datetime.fromisoformat(last_played)
                        if last_played.tzinfo is None:
                            last_played = last_played.replace(tzinfo=UTC)
                    except ValueError:
                        return include_missing

                if effective_after is not None and last_played < effective_after:
                    return False
                if effective_before is not None and last_played >= effective_before:
                    return False

            return True

        filtered_tracks = [
            track for track in t.tracks if meets_play_history_criteria(track)
        ]
        result = t.with_tracks(filtered_tracks)

        # Add comprehensive filter metadata with new parameter names
        filter_metadata = {
            "type": "play_history_filter",
            "min_plays": min_plays,
            "max_plays": max_plays,
            "start_date": start_date,
            "end_date": end_date,
            "min_days_back": min_days_back,
            "max_days_back": max_days_back,
            "effective_after_date": effective_after.isoformat()
            if effective_after
            else None,
            "effective_before_date": effective_before.isoformat()
            if effective_before
            else None,
            "include_missing": include_missing,
            "original_count": len(t.tracks),
            "filtered_count": len(filtered_tracks),
            "removed_count": len(t.tracks) - len(filtered_tracks),
        }

        return result.with_metadata("play_filter_applied", filter_metadata)

    return transform(tracklist) if tracklist is not None else transform


@curry
def sort_by_play_history(
    start_date: str | None = None,
    end_date: str | None = None,
    min_days_back: int | None = None,
    max_days_back: int | None = None,
    reverse: bool = True,
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """Sort tracks by play frequency within optional time windows.

    Sorts tracks based on play count within specified time windows using the same
    clear time window modes as filter_by_play_history:
    - None: No date fields = all-time play counts
    - Absolute: start_date/end_date = ISO date strings
    - Relative: min_days_back/max_days_back = integer days from today

    Args:
        start_date: Include tracks played after this ISO date (absolute mode)
        end_date: Include tracks played before this ISO date (absolute mode)
        min_days_back: Start of time window, days from today (relative mode)
        max_days_back: End of time window, days from today (relative mode)
        reverse: Sort order - True for most played first, False for least played first
        tracklist: Optional tracklist to transform immediately

    Returns:
        Transformation function or transformed tracklist if provided

    Examples:
        # Sort by all-time play count (most played first)
        sort_by_play_history(reverse=True)

        # Sort by plays in last month (least played first)
        sort_by_play_history(max_days_back=30, reverse=False)

        # Sort by plays between specific dates
        sort_by_play_history(
            start_date="2024-06-01",
            end_date="2024-08-31",
            reverse=True
        )

        # Sort by plays in time window (90-30 days ago)
        sort_by_play_history(min_days_back=90, max_days_back=30, reverse=True)
    """
    from datetime import timedelta

    def transform(t: TrackList) -> TrackList:
        """Apply play history sorting."""
        # Calculate effective date range using same logic as filter
        effective_after = None
        effective_before = None

        # Relative time mode takes precedence (clearer for users)
        # min_days_back = start of time window (furthest back, sets effective_before)
        # max_days_back = end of time window (closest to today, sets effective_after)
        if min_days_back is not None:
            effective_before = datetime.now(UTC) - timedelta(days=min_days_back)
        elif start_date is not None:
            try:
                effective_after = datetime.fromisoformat(start_date)
                if effective_after.tzinfo is None:
                    effective_after = effective_after.replace(tzinfo=UTC)
            except ValueError as e:
                raise ValueError(
                    f"Invalid start_date format: {start_date}. Use ISO format like '2024-01-01'"
                ) from e

        if max_days_back is not None:
            effective_after = datetime.now(UTC) - timedelta(days=max_days_back)
        elif end_date is not None:
            try:
                effective_before = datetime.fromisoformat(end_date)
                if effective_before.tzinfo is None:
                    effective_before = effective_before.replace(tzinfo=UTC)
            except ValueError as e:
                raise ValueError(
                    f"Invalid end_date format: {end_date}. Use ISO format like '2024-01-01'"
                ) from e

        # Get play data from metadata (try both nested and flat structure)
        all_play_counts = t.metadata.get("total_plays", {}) or t.metadata.get(
            "metrics", {}
        ).get("total_plays", {})
        last_played_dates = t.metadata.get("last_played_dates", {}) or t.metadata.get(
            "metrics", {}
        ).get("last_played_dates", {})

        # Calculate play counts within the time window for each track
        def get_play_count_for_sorting(track: Track) -> int:
            """Get play count for a track within the specified time window."""
            if not track.id:
                return 0

            # If no time constraints, use total play count
            if effective_after is None and effective_before is None:
                return all_play_counts.get(track.id, 0)

            # For time-constrained sorting, we need to check if the track was played
            # within the time window. Since we only have last_played_dates (not all play dates),
            # we'll use a heuristic: if the track was played within the window,
            # use its total play count, otherwise use 0.
            last_played = last_played_dates.get(track.id)
            if last_played is None:
                return 0

            # Convert string to datetime if needed
            if isinstance(last_played, str):
                try:
                    last_played = datetime.fromisoformat(last_played)
                    if last_played.tzinfo is None:
                        last_played = last_played.replace(tzinfo=UTC)
                except ValueError:
                    return 0

            # Check if last played date is within our time window
            if effective_after is not None and last_played < effective_after:
                return 0
            if effective_before is not None and last_played >= effective_before:
                return 0

            # Track was played within window, use its total play count as proxy
            return all_play_counts.get(track.id, 0)

        # Sort tracks by play count
        sorted_tracks = sorted(
            t.tracks, key=get_play_count_for_sorting, reverse=reverse
        )
        result = t.with_tracks(sorted_tracks)

        # Add sorting metadata
        track_play_counts = {
            track.id: get_play_count_for_sorting(track)
            for track in t.tracks
            if track.id is not None
        }

        sort_metadata = {
            "type": "play_history_sort",
            "start_date": start_date,
            "end_date": end_date,
            "min_days_back": min_days_back,
            "max_days_back": max_days_back,
            "effective_after_date": effective_after.isoformat()
            if effective_after
            else None,
            "effective_before_date": effective_before.isoformat()
            if effective_before
            else None,
            "reverse": reverse,
            "track_count": len(t.tracks),
        }

        # Store play counts in metadata for reference
        result = result.with_metadata(
            "metrics",
            {
                **result.metadata.get("metrics", {}),
                "sort_play_counts": track_play_counts,
            },
        )

        return result.with_metadata("play_sort_applied", sort_metadata)

    return transform(tracklist) if tracklist is not None else transform
