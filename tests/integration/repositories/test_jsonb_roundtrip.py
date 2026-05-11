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

from src.infrastructure.persistence.database.db_models import DBUserSettings


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


class TestJsonbRoundTripBool:
    """``bool`` must survive JSONB round-trip as ``bool``, not ``int``."""

    async def test_true_preserved_as_bool(self, db_session: AsyncSession):
        settings = await _persist_and_reload(db_session, {"flag": True})
        assert settings["flag"] is True
        assert type(settings["flag"]) is bool

    async def test_false_preserved_as_bool(self, db_session: AsyncSession):
        settings = await _persist_and_reload(db_session, {"flag": False})
        assert settings["flag"] is False
        assert type(settings["flag"]) is bool

    async def test_bool_not_coerced_to_int(self, db_session: AsyncSession):
        """Regression guard: ``isinstance(True, int)`` is ``True`` at the Python
        level. JSONB must not flatten bools to ints on round-trip.
        """
        settings = await _persist_and_reload(db_session, {"flag": True, "num": 1})
        assert type(settings["flag"]) is bool
        assert type(settings["num"]) is int


class TestJsonbRoundTripNone:
    """``None`` must survive as ``None``, not missing and not the string ``"null"``."""

    async def test_none_value_preserved(self, db_session: AsyncSession):
        settings = await _persist_and_reload(db_session, {"maybe": None})
        assert "maybe" in settings
        assert settings["maybe"] is None

    async def test_none_not_coerced_to_string(self, db_session: AsyncSession):
        settings = await _persist_and_reload(db_session, {"maybe": None})
        assert settings["maybe"] != "null"
        assert settings["maybe"] != ""


class TestJsonbRoundTripNesting:
    """Nested structures must survive intact regardless of depth."""

    async def test_nested_dict_preserved(self, db_session: AsyncSession):
        payload = {"a": {"b": {"c": 1, "d": "two", "e": True}}}
        settings = await _persist_and_reload(db_session, payload)
        assert settings == payload

    async def test_mixed_type_array(self, db_session: AsyncSession):
        payload: dict[str, object] = {
            "items": [1, "two", True, None, {"x": 1}, [1, 2, 3]]
        }
        settings = await _persist_and_reload(db_session, payload)
        items = settings["items"]
        assert isinstance(items, list)
        assert items[0] == 1
        assert type(items[0]) is int
        assert items[1] == "two"
        assert items[2] is True
        assert type(items[2]) is bool
        assert items[3] is None
        assert items[4] == {"x": 1}
        assert items[5] == [1, 2, 3]

    async def test_empty_dict_preserved(self, db_session: AsyncSession):
        settings = await _persist_and_reload(db_session, {})
        assert settings == {}

    async def test_empty_array_preserved(self, db_session: AsyncSession):
        settings = await _persist_and_reload(db_session, {"items": []})
        assert settings["items"] == []


class TestJsonbRoundTripNumbers:
    """Integer vs float distinction — JSONB stores both as the JSON number type,
    so we need to verify PostgreSQL's driver rehydrates the correct Python type.
    """

    async def test_int_preserved_as_int(self, db_session: AsyncSession):
        settings = await _persist_and_reload(db_session, {"count": 42})
        assert settings["count"] == 42
        assert type(settings["count"]) is int

    async def test_float_preserved_as_float(self, db_session: AsyncSession):
        settings = await _persist_and_reload(db_session, {"ratio": 0.75})
        assert settings["ratio"] == 0.75
        assert type(settings["ratio"]) is float

    async def test_zero_preserved_as_int(self, db_session: AsyncSession):
        """``0`` is falsy — shouldn't be coerced to ``None`` or ``False``."""
        settings = await _persist_and_reload(db_session, {"count": 0})
        assert settings["count"] == 0
        assert type(settings["count"]) is int

    async def test_negative_int(self, db_session: AsyncSession):
        settings = await _persist_and_reload(db_session, {"delta": -17})
        assert settings["delta"] == -17


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
