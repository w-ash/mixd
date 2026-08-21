"""Unit tests for cursor-based keyset pagination encoding/decoding.

Verifies round-trip encoding, error handling for malformed input, and
type coercion for different sort column types (strings, ints, datetimes).
"""

from datetime import UTC, datetime
from uuid import uuid7

import pytest

from src.application.pagination import (
    TRACK_SORT_COLUMNS,
    PageCursor,
    cursor_sort_value_from_row,
    cursor_sort_value_to_query,
    decode_cursor,
    encode_cursor,
)


class TestCursorRoundTrip:
    """Encode → decode produces the same PageCursor."""

    def test_string_sort_value(self) -> None:
        original = PageCursor(
            sort_column="title", sort_value="Radiohead", last_id=uuid7()
        )
        encoded = encode_cursor(original)
        decoded = decode_cursor(encoded)

        assert decoded == original

    def test_integer_sort_value(self) -> None:
        original = PageCursor(
            sort_column="duration_ms", sort_value=240000, last_id=uuid7()
        )
        encoded = encode_cursor(original)
        decoded = decode_cursor(encoded)

        assert decoded == original

    def test_none_sort_value(self) -> None:
        original = PageCursor(
            sort_column="duration_ms", sort_value=None, last_id=uuid7()
        )
        encoded = encode_cursor(original)
        decoded = decode_cursor(encoded)

        assert decoded == original

    def test_float_sort_value(self) -> None:
        original = PageCursor(sort_column="score", sort_value=0.95, last_id=uuid7())
        encoded = encode_cursor(original)
        decoded = decode_cursor(encoded)

        assert decoded == original

    def test_datetime_as_iso_string(self) -> None:
        """Datetimes are stored as ISO strings in the cursor."""
        dt = datetime(2025, 6, 15, 12, 30, 0, tzinfo=UTC)
        original = PageCursor(
            sort_column="created_at", sort_value=dt.isoformat(), last_id=uuid7()
        )
        encoded = encode_cursor(original)
        decoded = decode_cursor(encoded)

        assert decoded == original


class TestDecodeCursorErrors:
    """Invalid cursors raise ValueError."""

    def test_not_base64(self) -> None:
        with pytest.raises(ValueError, match="Invalid cursor encoding"):
            decode_cursor("not-valid-base64!!!")

    def test_not_json(self) -> None:
        import base64

        encoded = base64.urlsafe_b64encode(b"not json").decode()
        with pytest.raises(ValueError, match="Invalid cursor encoding"):
            decode_cursor(encoded)

    def test_missing_keys(self) -> None:
        import base64
        import json

        payload = json.dumps({"c": "title"}).encode()  # missing "v" and "id"
        encoded = base64.urlsafe_b64encode(payload).decode()
        with pytest.raises(ValueError, match="missing required key"):
            decode_cursor(encoded)

    def test_wrong_sort_column_type(self) -> None:
        import base64
        import json

        payload = json.dumps({"c": 123, "v": "x", "id": str(uuid7())}).encode()
        encoded = base64.urlsafe_b64encode(payload).decode()
        with pytest.raises(TypeError, match="sort_column must be a string"):
            decode_cursor(encoded)

    def test_wrong_last_id_type(self) -> None:
        import base64
        import json

        payload = json.dumps({"c": "title", "v": "x", "id": 12345}).encode()
        encoded = base64.urlsafe_b64encode(payload).decode()
        with pytest.raises(TypeError, match="last_id must be a UUID string"):
            decode_cursor(encoded)

    def test_invalid_uuid_last_id(self) -> None:
        import base64
        import json

        payload = json.dumps({"c": "title", "v": "x", "id": "not-a-uuid"}).encode()
        encoded = base64.urlsafe_b64encode(payload).decode()
        with pytest.raises(TypeError, match="not a valid UUID"):
            decode_cursor(encoded)

    def test_empty_string(self) -> None:
        with pytest.raises(ValueError):
            decode_cursor("")


class TestCursorSortValueConversion:
    """Type coercion between cursor values and database query values."""

    def test_datetime_column_round_trip(self) -> None:
        dt = datetime(2025, 3, 15, 10, 0, 0, tzinfo=UTC)

        # Row value → cursor value (datetime → ISO string)
        cursor_val = cursor_sort_value_from_row("created_at", dt)
        assert isinstance(cursor_val, str)

        # Cursor value → query value (ISO string → datetime)
        query_val = cursor_sort_value_to_query("created_at", cursor_val)
        assert isinstance(query_val, datetime)
        assert query_val == dt

    def test_string_column_passthrough(self) -> None:
        assert cursor_sort_value_from_row("title", "Hello") == "Hello"
        assert cursor_sort_value_to_query("title", "Hello") == "Hello"

    def test_int_column_passthrough(self) -> None:
        assert cursor_sort_value_from_row("duration_ms", 240000) == 240000
        assert cursor_sort_value_to_query("duration_ms", 240000) == 240000

    def test_none_passthrough(self) -> None:
        assert cursor_sort_value_from_row("title", None) is None
        assert cursor_sort_value_to_query("title", None) is None


class TestSortRegistriesStayInSync:
    """``TRACK_SORT_COLUMNS`` (application, for cursor encoding) and
    ``TrackRepository._SORT_SPECS`` (infrastructure, for ORDER BY) hold the
    same mapping and are kept in step by hand — the track repositories import
    nothing from ``src.application``, and reversing that would invert the
    dependency flow. So the duplication is deliberate; this test is what makes
    it safe, turning a "keep these in sync" comment into a failing build."""

    def test_the_two_registries_are_identical(self) -> None:
        from src.infrastructure.persistence.repositories.track.core import (
            TrackRepository,
        )

        assert TRACK_SORT_COLUMNS == TrackRepository._SORT_SPECS

    def test_every_sort_key_resolves_to_a_known_column(self) -> None:
        from src.infrastructure.persistence.repositories.track.core import (
            TrackRepository,
        )

        for column, _direction in TRACK_SORT_COLUMNS.values():
            assert column in TrackRepository._SORT_COLUMNS
