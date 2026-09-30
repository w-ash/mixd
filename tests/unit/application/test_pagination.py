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
    """Encode → decode keeps a float sort value, which no declared sort holds.

    Every declared sort's value types are covered by the property in
    ``TestEveryDeclaredSortRoundTrips``.
    """

    def test_float_sort_value(self) -> None:
        original = PageCursor(sort_key="score_desc", sort_value=0.95, last_id=uuid7())
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
    """Rejection paths of the cursor ↔ query-value coercion.

    The accepting paths (datetime ↔ ISO string, passthrough of strings, ints,
    and NULLs) are covered by the property in ``TestEveryDeclaredSortRoundTrips``.
    """

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
