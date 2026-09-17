"""Unit tests for cursor-based keyset pagination encoding/decoding.

Verifies round-trip encoding, error handling for malformed input, and
type coercion for every declared sort (strings, ints, datetimes, NULLs).
"""

from datetime import UTC, datetime
from uuid import UUID, uuid7

from hypothesis import given, strategies as st
import pytest

from src.application.pagination import (
    PageCursor,
    cursor_datetime_bound,
    cursor_sort_value_from_row,
    cursor_sort_value_to_query,
    decode_cursor,
    encode_cursor,
)
from src.domain.repositories.keyset import KeysetSort
from src.domain.repositories.operation_run import OPERATION_RUN_SORT
from src.domain.repositories.play import PLAY_EVENT_SORT
from src.domain.repositories.track import TRACK_SORTS

ALL_SORTS: tuple[KeysetSort, ...] = (
    *TRACK_SORTS.values(),
    PLAY_EVENT_SORT,
    OPERATION_RUN_SORT,
)


class TestCursorRoundTrip:
    """Encode → decode produces the same PageCursor."""

    def test_string_sort_value(self) -> None:
        original = PageCursor(
            sort_key="title_asc", sort_value="Radiohead", last_id=uuid7()
        )
        encoded = encode_cursor(original)
        decoded = decode_cursor(encoded)

        assert decoded == original

    def test_integer_sort_value(self) -> None:
        original = PageCursor(
            sort_key="duration_asc", sort_value=240000, last_id=uuid7()
        )
        encoded = encode_cursor(original)
        decoded = decode_cursor(encoded)

        assert decoded == original

    def test_none_sort_value(self) -> None:
        original = PageCursor(sort_key="duration_asc", sort_value=None, last_id=uuid7())
        encoded = encode_cursor(original)
        decoded = decode_cursor(encoded)

        assert decoded == original

    def test_float_sort_value(self) -> None:
        original = PageCursor(sort_key="score_desc", sort_value=0.95, last_id=uuid7())
        encoded = encode_cursor(original)
        decoded = decode_cursor(encoded)

        assert decoded == original

    def test_datetime_as_iso_string(self) -> None:
        """Datetimes are stored as ISO strings in the cursor."""
        dt = datetime(2025, 6, 15, 12, 30, 0, tzinfo=UTC)
        original = PageCursor(
            sort_key="added_desc", sort_value=dt.isoformat(), last_id=uuid7()
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

    def test_wrong_sort_key_type(self) -> None:
        import base64
        import json

        payload = json.dumps({"c": 123, "v": "x", "id": str(uuid7())}).encode()
        encoded = base64.urlsafe_b64encode(payload).decode()
        with pytest.raises(TypeError, match="sort_key must be a string"):
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

    def test_datetime_sort_round_trip(self) -> None:
        dt = datetime(2025, 3, 15, 10, 0, 0, tzinfo=UTC)
        sort = TRACK_SORTS["added_desc"]

        # Row value → cursor value (datetime → ISO string)
        cursor_val = cursor_sort_value_from_row(dt)
        assert isinstance(cursor_val, str)

        # Cursor value → query value (ISO string → datetime)
        query_val = cursor_sort_value_to_query(sort, cursor_val)
        assert isinstance(query_val, datetime)
        assert query_val == dt

    def test_string_sort_passthrough(self) -> None:
        sort = TRACK_SORTS["title_asc"]
        assert cursor_sort_value_from_row("Hello") == "Hello"
        assert cursor_sort_value_to_query(sort, "Hello") == "Hello"

    def test_int_sort_passthrough(self) -> None:
        sort = TRACK_SORTS["duration_asc"]
        assert cursor_sort_value_from_row(240000) == 240000
        assert cursor_sort_value_to_query(sort, 240000) == 240000

    def test_none_passthrough(self) -> None:
        sort = TRACK_SORTS["title_asc"]
        assert cursor_sort_value_from_row(None) is None
        assert cursor_sort_value_to_query(sort, None) is None

    def test_numeric_value_on_datetime_sort_raises(self) -> None:
        # A tampered or foreign cursor carrying a number for played_at used to
        # fall through as "no bound" and replay page one forever.
        with pytest.raises(ValueError, match="ISO datetime string"):
            _ = cursor_sort_value_to_query(PLAY_EVENT_SORT, 123)
        with pytest.raises(ValueError, match="ISO datetime string"):
            _ = cursor_sort_value_to_query(OPERATION_RUN_SORT, 1.5)

    def test_datetime_bound_requires_a_value(self) -> None:
        dt = datetime(2025, 3, 15, 10, 0, 0, tzinfo=UTC)
        assert cursor_datetime_bound(PLAY_EVENT_SORT, dt.isoformat()) == dt
        with pytest.raises(ValueError, match="must be a datetime"):
            _ = cursor_datetime_bound(PLAY_EVENT_SORT, None)


def _row_values(sort: KeysetSort) -> st.SearchStrategy[object]:
    """Row values a column declared like ``sort`` can hold."""
    if sort.is_datetime:
        base: st.SearchStrategy[object] = st.datetimes(timezones=st.just(UTC))
    else:
        base = st.one_of(st.text(), st.integers())
    return st.one_of(base, st.none()) if sort.nullable else base


class TestEveryDeclaredSortRoundTrips:
    """The codec is total over the declarations: for every sort, a row value
    survives encode → decode → query conversion unchanged."""

    @given(
        st.sampled_from(ALL_SORTS).flatmap(
            lambda sort: st.tuples(st.just(sort), _row_values(sort), st.uuids())
        )
    )
    def test_round_trip(self, case: tuple[KeysetSort, object, UUID]) -> None:
        sort, value, last_id = case
        cursor = PageCursor(
            sort_key=sort.key,
            sort_value=cursor_sort_value_from_row(value),
            last_id=last_id,
        )
        decoded = decode_cursor(encode_cursor(cursor))
        assert decoded.sort_key == sort.key
        assert decoded.last_id == last_id
        assert cursor_sort_value_to_query(sort, decoded.sort_value) == value
