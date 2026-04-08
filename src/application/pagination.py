"""Cursor-based keyset pagination utilities.

Provides opaque cursor encoding/decoding for keyset pagination on endpoints
with large result sets (e.g., the 15k+ track library). Cursors encode the
last row's sort value and ID so the next page can use a WHERE clause instead
of OFFSET — O(1) seeks regardless of page depth.
"""

import base64
from datetime import datetime
import json
from typing import Final, Literal, cast
from uuid import UUID

from attrs import define

# ── Track sort definitions ──────────────────────────────────────────────

type TrackSortBy = Literal[
    "title_asc",
    "title_desc",
    "artist_asc",
    "artist_desc",
    "added_desc",
    "added_asc",
    "duration_asc",
    "duration_desc",
]

# Canonical mapping: sort key → (db_column, direction)
# Single source of truth consumed by both the use case (cursor encoding)
# and the repository (ORDER BY construction).
TRACK_SORT_COLUMNS: Final[dict[TrackSortBy, tuple[str, str]]] = {
    "title_asc": ("title", "asc"),
    "title_desc": ("title", "desc"),
    "artist_asc": ("artists_text", "asc"),
    "artist_desc": ("artists_text", "desc"),
    "added_desc": ("created_at", "desc"),
    "added_asc": ("created_at", "asc"),
    "duration_asc": ("duration_ms", "asc"),
    "duration_desc": ("duration_ms", "desc"),
}

# Sort columns that store datetime values (ISO string in cursor)
_DATETIME_COLUMNS: Final = frozenset({"created_at"})


@define(frozen=True, slots=True)
class PageCursor:
    """Decoded cursor for keyset pagination.

    Attributes:
        sort_column: The DB column name used for ordering (e.g., "title").
        sort_value: The last row's value for that column. Datetimes stored as ISO strings.
        last_id: The last row's primary key (UUID), used as tiebreaker for stable ordering.
    """

    sort_column: str
    sort_value: str | int | float | None
    last_id: UUID


def encode_cursor(cursor: PageCursor) -> str:
    """Encode a PageCursor as an opaque base64 string for use in API responses.

    Format: base64(json({"c": column, "v": value, "id": id}))
    Compact keys minimize URL length.
    """
    payload = {
        "c": cursor.sort_column,
        "v": cursor.sort_value,
        "id": str(cursor.last_id),
    }
    json_bytes = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(json_bytes).decode()


def decode_cursor(encoded: str) -> PageCursor:
    """Decode an opaque cursor string back into a PageCursor.

    Raises:
        ValueError: If the cursor is malformed, tampered with, or has wrong types.
    """
    try:
        json_bytes = base64.urlsafe_b64decode(encoded)
        raw = cast(object, json.loads(json_bytes))
    except Exception as exc:
        raise ValueError(f"Invalid cursor encoding: {exc}") from exc

    if not isinstance(raw, dict):
        raise TypeError("Cursor payload must be a JSON object")

    payload = cast(dict[str, object], raw)
    try:
        sort_column = payload["c"]
        sort_value = payload["v"]
        last_id_raw = payload["id"]
    except KeyError as exc:
        raise ValueError(f"Cursor missing required key: {exc}") from exc

    if not isinstance(sort_column, str):
        raise TypeError("Cursor sort_column must be a string")
    if not isinstance(last_id_raw, str):
        raise TypeError("Cursor last_id must be a UUID string")
    try:
        last_id = UUID(last_id_raw)
    except ValueError as exc:
        raise TypeError(f"Cursor last_id is not a valid UUID: {exc}") from exc
    if sort_value is not None and not isinstance(sort_value, str | int | float):
        raise TypeError("Cursor sort_value must be str, int, float, or None")

    return PageCursor(sort_column=sort_column, sort_value=sort_value, last_id=last_id)


def cursor_sort_value_from_row(
    _column_name: str, value: object
) -> str | int | float | None:
    """Convert a database row value to a cursor-safe sort value.

    Datetimes are serialized as ISO strings; scalars pass through.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str | int | float):
        return value
    # Fallback: stringify unknown types
    return str(value)


def cursor_sort_value_to_query(
    column_name: str, sort_value: str | int | float | None
) -> str | int | float | datetime | None:
    """Convert a cursor's sort_value back to a query-compatible type.

    Datetime columns (created_at) are parsed from ISO strings.
    """
    if sort_value is None:
        return None
    if column_name in _DATETIME_COLUMNS and isinstance(sort_value, str):
        return datetime.fromisoformat(sort_value)
    return sort_value
