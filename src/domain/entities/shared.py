"""Shared utilities and helper functions for domain entities.

Pure utility functions and reusable attrs field validators. Depends only on
the standard library and ``attrs`` (the domain's modelling foundation) — no
infrastructure, application, or third-party service dependencies.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import NewType

from attrs import Attribute

# Metric values stored per-track: play counts (int), averages (float),
# timestamps (datetime), or absent (None)
type MetricValue = int | float | datetime | None

# Sort key extractors return one of these comparable types
type SortKey = str | int | float | datetime

# JSON-shaped data using covariant containers (Sequence/Mapping) so that
# list[str] is assignable to Sequence[JsonValue] and dict[str, int] is
# assignable to Mapping[str, JsonValue]. Invariant list/dict would break
# at every construction site. See: pydantic#9701, pyright#2115.
type JsonValue = (
    str | int | float | bool | Sequence[JsonValue] | Mapping[str, JsonValue] | None
)

# Concrete JSON object shape. Use as the mutable/owned form of JSON-shaped data.
# Pair with Mapping[str, JsonValue] for covariant read-only parameters.
# Registered in SQLAlchemy's type_annotation_map so Mapped[JsonDict] resolves
# to postgresql.JSONB automatically — see infrastructure/persistence/database/db_models.py.
type JsonDict = dict[str, JsonValue]


# A third-party music service's own playlist ID (e.g., Spotify base62, Last.fm slug).
# Distinct from ``Playlist.id: UUID`` (mixd canonical) and ``ConnectorPlaylist.id: UUID``
# (PK of the local cache row) — the brand prevents mixing the three at call sites.
ConnectorPlaylistIdentifier = NewType("ConnectorPlaylistIdentifier", str)


def empty_json_map() -> JsonDict:
    """Typed factory for ``Mapping[str, JsonValue]`` attrs fields."""
    return {}


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


def validate_timezone_aware[T](
    _instance: object,
    attribute: Attribute[T],
    value: T,
) -> None:
    """attrs field validator: reject naive datetimes.

    Shared across entities (e.g. ``operations.py``, ``schedule.py``) so the
    timezone-aware invariant has one definition. Use with an attrs field:
        played_at: datetime = field(validator=validate_timezone_aware)
    """
    if isinstance(value, datetime) and value.tzinfo is None:
        raise ValueError(
            f"Field '{attribute.name}' must be timezone-aware. Use datetime.now(UTC) or datetime.replace(tzinfo=UTC) for naive datetimes."
        )


# ---------------------------------------------------------------------------
# JsonValue narrowing helpers
# ---------------------------------------------------------------------------
# Use at boundaries where raw JSON values (JsonValue) need to be narrowed to
# concrete types. Avoids repeating isinstance patterns across connector files.


def json_str(val: JsonValue, default: str = "") -> str:
    """Narrow a JsonValue to str, returning default if not a string."""
    return val if isinstance(val, str) else default


def json_int(val: JsonValue, default: int = 0) -> int:
    """Narrow a JsonValue to int, returning default if not an int.

    Guards against bool (isinstance(True, int) is True in Python).
    """
    return val if isinstance(val, int) and not isinstance(val, bool) else default


def json_bool(val: JsonValue, default: bool = False) -> bool:
    """Narrow a JsonValue to bool, returning default if not a bool."""
    return val if isinstance(val, bool) else default
