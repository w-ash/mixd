"""Migration 058's inlined ``entity_kind`` vocabulary must equal the domain's.

The migration inlines its value list so it stays a frozen record of the schema
at that revision; this is what notices drift. An ``EntityKind`` member added in
the domain without a follow-up migration fails here, and so does a constraint
whose rendered text differs between the ORM (which ``metadata.create_all``
gives integration tests) and the migration (which production runs).
"""

import importlib.util
from pathlib import Path
from typing import Any

from sqlalchemy import CheckConstraint

from src.domain.entities.resolution_event import ENTITY_KINDS
from src.infrastructure.persistence.database.models import DBResolutionEvent

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "alembic"
    / "versions"
    / "058_resolution_event_entity_kind.py"
)


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("migration_058", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_migration()


def _orm_check(name: str) -> str:
    constraint = next(
        c
        for c in DBResolutionEvent.__table__.constraints
        if isinstance(c, CheckConstraint) and c.name == name
    )
    return str(constraint.sqltext)


class TestVocabularyAgrees:
    def test_entity_kinds_match_the_domain(self) -> None:
        assert set(migration.ENTITY_KINDS) == set(ENTITY_KINDS)

    def test_list_is_sorted_so_the_ddl_is_deterministic(self) -> None:
        assert list(migration.ENTITY_KINDS) == sorted(migration.ENTITY_KINDS)

    def test_constraint_text_agrees_with_the_orm(self) -> None:
        assert _orm_check(
            "ck_resolution_events_entity_kind_vocabulary"
        ) == migration._in_list("entity_kind", migration.ENTITY_KINDS)

    def test_column_is_required_with_no_default(self) -> None:
        column = DBResolutionEvent.__table__.c.entity_kind
        assert not column.nullable
        assert column.server_default is None
        assert column.default is None
