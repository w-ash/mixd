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

Uses ``DBUserSettings.settings`` (``Mapped[dict[str, Any]]`` on a JSONB
column) as the test target — it's the simplest JSONB column without
relationship complications.
"""

from uuid import uuid4

from sqlalchemy import select
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
