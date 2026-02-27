"""Private helper utilities for application transform modules.

This module contains shared helper functions used by multiple transform modules
to eliminate code duplication. Functions are prefixed with underscore to indicate
they are private implementation details.

These helpers consolidate:
- Date/time window calculation logic
- Metadata extraction patterns
- Datetime parsing with timezone handling
- Generic metadata accessors

Note: This module is private and should only be imported by other modules
in the application/transforms package.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, TypeIs

from src.config import get_logger
from src.domain.entities.track import TrackList

logger = get_logger(__name__)

# === Type Guards ===


def is_datetime_string(value: Any) -> TypeIs[str]:
    """Python 3.13 TypeIs guard for datetime string validation."""
    return isinstance(value, str) and bool(value.strip())


# === Time Window Calculation ===


def calculate_time_window(
    start_date: str | None,
    end_date: str | None,
    min_days_back: int | None,
    max_days_back: int | None,
) -> tuple[datetime | None, datetime | None]:
    """Calculate effective time window from various date parameters.

    Supports three time window modes:
    - None: No date fields = all-time
    - Absolute: start_date/end_date = ISO date strings
    - Relative: min_days_back/max_days_back = integer days from today

    Relative time mode takes precedence over absolute dates for clarity.

    Args:
        start_date: ISO format date string for absolute start
        end_date: ISO format date string for absolute end
        min_days_back: Start of time window (furthest back, sets effective_before)
        max_days_back: End of time window (closest to today, sets effective_after)

    Returns:
        Tuple of (effective_after, effective_before) datetime objects or None

    Raises:
        ValueError: If date strings are in invalid format
    """
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

    return effective_after, effective_before


# === Metadata Extraction ===


def get_play_metrics(
    tracklist: TrackList,
) -> tuple[dict[int, int], dict[int, datetime | str]]:
    """Extract play count and last played date metrics from tracklist metadata.

    Reads from the canonical nested structure: metadata["metrics"][metric_name].

    Args:
        tracklist: TrackList with metadata containing play metrics

    Returns:
        Tuple of (play_counts_dict, last_played_dates_dict)
        where keys are track IDs and values are counts/dates
    """
    metrics = tracklist.metadata.get("metrics", {})
    play_counts = metrics.get("total_plays", {})
    last_played_dates = metrics.get("last_played_dates", {})
    return play_counts, last_played_dates


def get_metric_value(
    tracklist: TrackList,
    metric_name: str,
    track_id: int,
) -> Any | None:
    """Safely extract a single metric value from tracklist metadata.

    Reads from the canonical nested structure: metadata["metrics"][metric_name][track_id].

    Args:
        tracklist: TrackList with metadata
        metric_name: Name of the metric (e.g., "lastfm_user_playcount")
        track_id: Track ID to look up

    Returns:
        Metric value if found, None otherwise
    """
    return tracklist.metadata.get("metrics", {}).get(metric_name, {}).get(track_id)


# === Datetime Parsing ===


def parse_datetime_safe(value: Any) -> datetime | None:
    """Parse datetime with timezone handling and error tolerance.

    Handles multiple input formats:
    - Already datetime objects (ensures UTC timezone)
    - ISO format strings (most common)
    - Timestamp strings (fallback)
    - Invalid formats (returns None with debug logging)

    Always returns timezone-aware datetime in UTC or None.

    Args:
        value: Value to parse as datetime

    Returns:
        Timezone-aware datetime in UTC or None if parsing fails
    """
    # Already a datetime - ensure timezone
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    # String parsing with robust fallbacks
    if is_datetime_string(value):
        # Try ISO format first (most common)
        try:
            dt = datetime.fromisoformat(value)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            pass

        # Try parsing as timestamp
        try:
            timestamp = float(value)
            return datetime.fromtimestamp(timestamp, tz=UTC)
        except ValueError, TypeError:
            logger.debug(
                "Failed to parse datetime value", value=value, type=type(value)
            )
            return None

    # Not a datetime or string
    return None
