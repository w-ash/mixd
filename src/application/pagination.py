"""Cursor-based keyset pagination utilities.

Provides opaque cursor encoding/decoding for keyset pagination on endpoints
with large result sets (e.g., the 15k+ track library). Cursors encode the
last row's sort value and ID so the next page can use a WHERE clause instead
of OFFSET — O(1) seeks regardless of page depth.
"""

import base64
from datetime import datetime
import json
from typing import cast
from uuid import UUID

from attrs import define

from src.domain.repositories.keyset import KeysetSort


@define(frozen=True, slots=True)
class PageCursor:
    """Decoded cursor for keyset pagination.

    Attributes:
        sort_key: The ``KeysetSort.key`` the page was minted under (e.g.
            "title_asc"). A reader compares it with the active sort and refuses
            a cursor that would seek from the wrong end.
        sort_value: The last row's value for the sort column. Datetimes stored as ISO strings.
        last_id: The last row's primary key (UUID), used as tiebreaker for stable ordering.
    """

    sort_key: str
    sort_value: str | int | float | None
    last_id: UUID


def encode_cursor(cursor: PageCursor) -> str:
    """Encode a PageCursor as an opaque base64 string for use in API responses.

    Format: base64(json({"c": sort key, "v": value, "id": id}))
    Compact keys minimize URL length.
    """
    payload = {
        "c": cursor.sort_key,
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
        sort_key = payload["c"]
        sort_value = payload["v"]
        last_id_raw = payload["id"]
    except KeyError as exc:
        raise ValueError(f"Cursor missing required key: {exc}") from exc

    if not isinstance(sort_key, str):
        raise TypeError("Cursor sort_key must be a string")
    if not isinstance(last_id_raw, str):
        raise TypeError("Cursor last_id must be a UUID string")
    try:
        last_id = UUID(last_id_raw)
    except ValueError as exc:
        raise TypeError(f"Cursor last_id is not a valid UUID: {exc}") from exc
    if sort_value is not None and not isinstance(sort_value, str | int | float):
        raise TypeError("Cursor sort_value must be str, int, float, or None")

    return PageCursor(sort_key=sort_key, sort_value=sort_value, last_id=last_id)


def cursor_sort_value_from_row(value: object) -> str | int | float | None:
    """Convert a database row value to a cursor-safe sort value.

    Datetimes are serialized as ISO strings; scalars pass through. The reader
    (``cursor_sort_value_to_query``) needs the sort declaration to undo this;
    the writer does not.
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
    sort: KeysetSort, sort_value: str | float | None
) -> str | int | float | datetime | None:
    """Convert a cursor's sort_value back to a query-compatible type.

    Datetime sorts are parsed from ISO strings.

    Raises:
        ValueError: If a datetime column carries a non-string or unparseable
            value — a cursor that cannot bound the page must fail, not fall
            through to page one.
    """
    if sort_value is None:
        return None
    if sort.is_datetime:
        if not isinstance(sort_value, str):
            raise ValueError(
                f"Cursor sort_value for {sort.column} must be an ISO datetime string"
            )
        return datetime.fromisoformat(sort_value)
    return sort_value


def cursor_datetime_bound(sort: KeysetSort, sort_value: str | float | None) -> datetime:
    """The datetime keyset bound a cursor carries for a datetime-sorted list.

    Raises:
        ValueError: If the value is absent or not an ISO datetime string.
    """
    match cursor_sort_value_to_query(sort, sort_value):
        case datetime() as bound:
            return bound
        case _:
            raise ValueError(f"Cursor sort_value for {sort.column} must be a datetime")
