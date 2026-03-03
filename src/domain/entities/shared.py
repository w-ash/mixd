"""Shared utilities and helper functions for domain entities.

Pure utility functions with zero external dependencies.
"""

from datetime import UTC, datetime

# Metric values stored per-track: play counts (int), averages (float),
# timestamps (datetime), or absent (None)
type MetricValue = int | float | datetime | None


def ensure_utc(dt: datetime | None) -> datetime | None:
    """Ensure datetime is timezone-aware with UTC."""
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def utc_now_factory() -> datetime:
    """Standard factory for UTC timestamp fields in attrs classes.

    Use with attrs field factory to get current UTC time:
        timestamp: datetime = field(factory=utc_now_factory)
    """
    return datetime.now(UTC)
