"""Private helper utilities for application transform modules.

This module contains shared helper functions used by multiple transform modules
to eliminate code duplication. Functions are prefixed with underscore to indicate
they are private implementation details.

These helpers consolidate:
- Date/time window calculation logic
- Metadata extraction patterns
- Datetime parsing with timezone handling

Note: This module is private and should only be imported by other modules
in the application/metadata_transforms package.
"""

# Legitimate Any: use case results, OperationResult metadata, metric values

from datetime import UTC, datetime, timedelta
from typing import Literal, TypeIs
from uuid import UUID

from src.config import get_logger
from src.domain.entities.shared import MetricValue
from src.domain.entities.track import TrackList

logger = get_logger(__name__)

# === Type Guards ===


def is_datetime_string(value: object) -> TypeIs[str]:
    """Python 3.13 TypeIs guard for datetime string validation."""
    return isinstance(value, str) and bool(value.strip())


# === Time Window Calculation ===


def calculate_time_window(
    start_date: str | None,
    end_date: str | None,
    not_played_in_days: int | None,
    played_within_days: int | None,
) -> tuple[datetime | None, datetime | None]:
    """Calculate effective time window from various date parameters.

    Supports three time window modes:
    - None: No date fields = all-time
    - Absolute: start_date/end_date = ISO date strings
    - Relative: not_played_in_days/played_within_days = integer days from today

    Relative time mode takes precedence over absolute dates for clarity.

    Args:
        start_date: ISO format date string for absolute start
        end_date: ISO format date string for absolute end
        not_played_in_days: Start of time window (furthest back, sets effective_before)
        played_within_days: End of time window (closest to today, sets effective_after)

    Returns:
        Tuple of (effective_after, effective_before) datetime objects or None

    Raises:
        ValueError: If date strings are in invalid format
    """
    effective_after = None
    effective_before = None

    # Relative time mode takes precedence (clearer for users)
    # not_played_in_days = start of time window (furthest back, sets effective_before)
    # played_within_days = end of time window (closest to today, sets effective_after)
    if not_played_in_days is not None:
        effective_before = datetime.now(UTC) - timedelta(days=not_played_in_days)
    elif start_date is not None:
        try:
            effective_after = datetime.fromisoformat(start_date)
            if effective_after.tzinfo is None:
                effective_after = effective_after.replace(tzinfo=UTC)
        except ValueError as e:
            raise ValueError(
                f"Invalid start_date format: {start_date}. Use ISO format like '2024-01-01'"
            ) from e

    if played_within_days is not None:
        effective_after = datetime.now(UTC) - timedelta(days=played_within_days)
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

# A played-date source for play-history filters/sorters. Typed as a Literal so
# the DATE_SOURCE_METRIC_KEYS lookup below can never KeyError on a typo — the
# type checker rejects any other value at the call site.
type DateSource = Literal["first_played", "last_played"]

# Maps a date_source parameter to its metadata metric key. The single home for
# this mapping (sort_by_date in metric_transforms.py imports it back) so the
# play-history filter and sorter agree on which date a source name resolves to.
# Kept str-keyed because sort_by_date indexes it with a plain str (after handling
# its own "added_at" case); get_play_metrics' DateSource-typed param is what
# guarantees the lookup never misses.
DATE_SOURCE_METRIC_KEYS: dict[str, str] = {
    "first_played": "first_played_dates",
    "last_played": "last_played_dates",
}


def get_play_metrics(
    tracklist: TrackList,
    date_source: DateSource = "last_played",
) -> tuple[dict[UUID, MetricValue], dict[UUID, MetricValue]]:
    """Extract play count and a played-date metric from tracklist metadata.

    Reads from the canonical nested structure: metadata["metrics"][metric_name].

    Args:
        tracklist: TrackList with metadata containing play metrics
        date_source: Which date metric to return alongside the counts —
            "last_played" (default, preserves prior behavior) or "first_played"

    Returns:
        Tuple of (play_counts_dict, played_dates_dict) keyed by track ID, where
        the dates dict is the first- or last-played map per ``date_source``.
    """
    metrics = tracklist.metadata.get("metrics", {})
    play_counts = metrics.get("total_plays", {})
    played_dates = metrics.get(DATE_SOURCE_METRIC_KEYS[date_source], {})
    return play_counts, played_dates


# === Datetime Parsing ===


def parse_datetime_safe(value: object) -> datetime | None:
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
