"""Play history-based transformations for track collections.

This module contains transformations that operate on tracks using play history data
stored in TrackList metadata. These transforms coordinate between domain entities
and application-layer play history enrichment.

Unlike pure domain transforms, these functions:
- Access nested metadata structures (metadata["metrics"]["total_plays"], etc.)
- Use logging for debugging datetime parsing issues
- Depend on play history enrichment having occurred first
- Handle complex time window logic with multiple date format parsing
"""

from collections.abc import Callable

from src.config import get_logger
from src.domain.entities.track import Track, TrackList

from ._helpers import (
    calculate_time_window,
    get_play_metrics,
    parse_datetime_safe,
)

logger = get_logger(__name__)

# Type alias for transformation functions
Transform = Callable[[TrackList], TrackList]


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
            "Must specify at least one constraint: min_plays, max_plays, start_date, end_date, min_days_back, or max_days_back"
        )

    def transform(t: TrackList) -> TrackList:
        """Apply unified play history filtering."""
        # Calculate effective date range using helper
        effective_after, effective_before = calculate_time_window(
            start_date, end_date, min_days_back, max_days_back
        )

        # Get play data from metadata using helper
        play_counts, last_played_dates = get_play_metrics(t)

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
                last_played_raw = last_played_dates.get(track.id)

                if last_played_raw is None:
                    return include_missing

                # Parse datetime using helper
                last_played = parse_datetime_safe(last_played_raw)
                if last_played is None:
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

        logger.debug(
            "Play history filter applied",
            min_plays=min_plays,
            max_plays=max_plays,
            start_date=start_date,
            end_date=end_date,
            min_days_back=min_days_back,
            max_days_back=max_days_back,
            effective_after_date=effective_after.isoformat()
            if effective_after
            else None,
            effective_before_date=effective_before.isoformat()
            if effective_before
            else None,
            include_missing=include_missing,
            original_count=len(t.tracks),
            filtered_count=len(filtered_tracks),
            removed_count=len(t.tracks) - len(filtered_tracks),
        )

        return result

    return transform(tracklist) if tracklist is not None else transform


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

    def transform(t: TrackList) -> TrackList:
        """Apply play history sorting."""
        # Calculate effective date range using helper
        effective_after, effective_before = calculate_time_window(
            start_date, end_date, min_days_back, max_days_back
        )

        # Get play data from metadata using helper
        all_play_counts, last_played_dates = get_play_metrics(t)

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
            last_played_raw = last_played_dates.get(track.id)
            if last_played_raw is None:
                return 0

            # Parse datetime using helper
            last_played = parse_datetime_safe(last_played_raw)
            if last_played is None:
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

        logger.debug(
            "Play history sort applied",
            start_date=start_date,
            end_date=end_date,
            min_days_back=min_days_back,
            max_days_back=max_days_back,
            effective_after_date=effective_after.isoformat()
            if effective_after
            else None,
            effective_before_date=effective_before.isoformat()
            if effective_before
            else None,
            reverse=reverse,
            track_count=len(t.tracks),
        )

        return result

    return transform(tracklist) if tracklist is not None else transform
