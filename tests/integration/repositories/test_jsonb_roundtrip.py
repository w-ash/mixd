"""JSONB round-trip integration tests.

Validates that Python values survive a PostgreSQL JSONB round-trip intact.
These tests exist to back the Phase 3a ``type_annotation_map`` migration: we
need to know that ``Mapped[JsonDict]`` resolves to the same runtime behaviour
as the existing ``Mapped[dict[str, Any]]`` columns before relying on it
everywhere.

The critical cases:

- **``bool`` preservation**: Python ``True`` must come back as ``bool``, not
  ``int``. The Phase 1 learning was that ``isinstance(True, int)`` is ``True``,
  so a silent bool→int collapse would be a real bug.
- **``None`` preservation**: stored ``None`` must come back as ``None``, not
  missing and not the string ``"null"``.
- **Nested dicts**: arbitrary nesting round-trips intact (keys stay keys,
  values stay values, types stay types).
- **Mixed-type arrays**: JSON arrays with heterogeneous element types survive.
- **Empty dict**: ``{}`` is a valid value, not coerced to ``None``.
- **UUID and datetime values**: backed by orjson registered as the
  psycopg JSON dumper via ``set_json_dumps`` in ``db_connection.py``.
  Raw UUID / datetime values written to a JSONB column should serialize
  to ISO / canonical strings rather than crash psycopg's default adapter.

Uses ``DBUserSettings.settings`` (``Mapped[dict[str, Any]]`` on a JSONB
column) as the test target — it's the simplest JSONB column without
relationship complications.
"""

from datetime import UTC, date, datetime, time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.persistence.database.models import DBUserSettings


async def _persist_and_reload(
    db_session: AsyncSession, payload: dict[str, object]
) -> dict[str, object]:
    """Store ``payload`` in a DBUserSettings row and reload it from the DB.

    Uses a unique (user_id, key) pair per call so tests are independent even
    inside the same savepoint-rolled-back transaction. The reload fetches by
    ID from the database — not from the identity map — so we validate what
    PostgreSQL actually returns, not what SQLAlchemy cached.
    """
    unique = str(uuid4())[:8]
    row = DBUserSettings(
        user_id=f"test_{unique}",
        key=f"test_{unique}",
        settings=payload,
    )
    db_session.add(row)
    await db_session.flush()
    row_id = row.id

    # Expire the object so the reload hits the DB, not the identity map.
    db_session.expire(row)
    result = await db_session.execute(
        select(DBUserSettings).where(DBUserSettings.id == row_id)
    )
    reloaded = result.scalar_one()
    return reloaded.settings


def _type_tree(value: object) -> object:
    """Mirror ``value`` with every leaf replaced by its exact type.

    ``==`` alone cannot tell ``True`` from ``1`` or ``0`` from ``False``, so the
    round-trip compares both the values and this type skeleton.
    """
    if isinstance(value, dict):
        return {key: _type_tree(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_type_tree(item) for item in value]
    return type(value)


class TestJsonbRoundTripNativeValues:
    """JSON-native values come back with the same value and the same type."""

    async def test_json_native_values_round_trip_with_their_types(
        self, db_session: AsyncSession
    ):
        """bool stays bool (not int), None stays None (not ``"null"``), int and
        float stay distinct, falsy values (0, False, [], {}) are not coerced,
        and nesting of any depth survives intact.
        """
        payload: dict[str, object] = {
            "true": True,
            "false": False,
            "one": 1,
            "zero": 0,
            "negative": -17,
            "ratio": 0.75,
            "text": "two",
            "maybe": None,
            "empty_list": [],
            "empty_dict": {},
            "items": [1, "two", True, None, {"x": 1}, [1, 2, 3]],
            "nested": {"a": {"b": {"c": 1, "d": "two", "e": True}}},
        }

        settings = await _persist_and_reload(db_session, payload)

        assert settings == payload
        assert _type_tree(settings) == _type_tree(payload)

    async def test_empty_dict_preserved(self, db_session: AsyncSession):
        settings = await _persist_and_reload(db_session, {})
        assert settings == {}


class TestJsonbEncoderUuidDatetime:
    """orjson (registered via ``set_json_dumps`` in ``db_connection.py``)
    must let raw UUID / datetime values reach a JSONB column without
    crashing psycopg.

    Builders that produce JSONB payloads still stringify at the
    application boundary for in-process consumers (preview, CLI). These
    tests verify the *fallback* path — any value that bypasses the
    builder contract must still serialize at the driver layer.
    """

    async def test_uuid_value_serialized_to_string(
        self, db_session: AsyncSession
    ) -> None:
        raw_uuid = uuid4()
        settings = await _persist_and_reload(db_session, {"track_id": raw_uuid})
        assert settings["track_id"] == str(raw_uuid)
        # Round-trip parses cleanly as a UUID — encoder produced the canonical form.
        assert UUID(str(settings["track_id"])) == raw_uuid

    async def test_datetime_value_serialized_to_iso_string(
        self, db_session: AsyncSession
    ) -> None:
        raw_dt = datetime(2026, 5, 10, 17, 26, 8, tzinfo=UTC)
        settings = await _persist_and_reload(db_session, {"started_at": raw_dt})
        assert settings["started_at"] == raw_dt.isoformat()

    async def test_date_and_time_values_serialized_to_iso_strings(
        self, db_session: AsyncSession
    ) -> None:
        raw_date = date(2026, 5, 10)
        raw_time = time(17, 26, 8)
        settings = await _persist_and_reload(
            db_session, {"day": raw_date, "moment": raw_time}
        )
        assert settings["day"] == raw_date.isoformat()
        assert settings["moment"] == raw_time.isoformat()

    async def test_uuid_inside_nested_structure(self, db_session: AsyncSession) -> None:
        """The bug shape from v0.7.8.14 / v0.7.8.15: a UUID nested several
        levels deep inside a dict/list payload. The encoder must walk into
        nested structures, not just top-level values.
        """
        ids = [uuid4(), uuid4(), uuid4()]
        payload = {
            "tracks_added": [{"track_id": ids[0]}, {"track_id": ids[1]}],
            "tracks_moved": [{"track_id": ids[2]}],
        }
        settings = await _persist_and_reload(db_session, payload)
        added = settings["tracks_added"]
        assert isinstance(added, list)
        assert added[0]["track_id"] == str(ids[0])
        assert added[1]["track_id"] == str(ids[1])
        moved = settings["tracks_moved"]
        assert isinstance(moved, list)
        assert moved[0]["track_id"] == str(ids[2])

    async def test_unsupported_type_raises_typeerror(
        self, db_session: AsyncSession
    ) -> None:
        """orjson is intentionally strict — anything outside its native
        type set (UUID, datetime, date, time, dataclass, Enum, numpy,
        and JSON-native types) raises ``TypeError`` at dump time. Loud
        failure is preferable to silent ``repr()`` coercion.
        """
        with pytest.raises((TypeError, StatementError)):
            await _persist_and_reload(db_session, {"weird": {1, 2, 3}})
