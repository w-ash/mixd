"""Migration 054's inlined vocabularies must equal the domain's.

The migration inlines its value lists so it stays a frozen record of the schema
at that revision (importing the live ``frozenset`` would make an old migration
change meaning whenever the vocabulary grows). That freedom is only safe while
something notices drift — this is that something. A vocabulary member added in
the domain without a follow-up migration fails here.

The constraint *text* is checked too: the ORM declares these constraints so
``metadata.create_all`` gives integration tests the production schema, and the
two renderings have to agree character for character.
"""

import importlib.util
from pathlib import Path
from typing import Any

from sqlalchemy import CheckConstraint

from src.domain.entities.track_mapping import MAPPING_ORIGINS, MATCH_METHODS
from src.infrastructure.persistence.database.models import (
    DBMatchReview,
    DBTrackMapping,
)

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "alembic"
    / "versions"
    / "054_vocabulary_check_constraints.py"
)


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("migration_054", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_migration()


def _orm_check(model: Any, name: str) -> str:
    constraint = next(
        c
        for c in model.__table__.constraints
        if isinstance(c, CheckConstraint) and c.name == name
    )
    return str(constraint.sqltext)


class TestVocabulariesAgree:
    def test_match_methods_match_the_domain(self) -> None:
        assert set(migration.MATCH_METHODS) == set(MATCH_METHODS)

    def test_mapping_origins_match_the_domain(self) -> None:
        assert set(migration.MAPPING_ORIGINS) == set(MAPPING_ORIGINS)

    def test_lists_are_sorted_so_the_ddl_is_deterministic(self) -> None:
        assert list(migration.MATCH_METHODS) == sorted(migration.MATCH_METHODS)
        assert list(migration.MAPPING_ORIGINS) == sorted(migration.MAPPING_ORIGINS)


class TestConstraintTextAgrees:
    def test_track_mappings_match_method(self) -> None:
        assert _orm_check(
            DBTrackMapping, "ck_track_mappings_match_method_vocabulary"
        ) == migration._in_list("match_method", migration.MATCH_METHODS)

    def test_track_mappings_origin(self) -> None:
        assert _orm_check(
            DBTrackMapping, "ck_track_mappings_origin_vocabulary"
        ) == migration._in_list("origin", migration.MAPPING_ORIGINS)

    def test_match_reviews_match_method(self) -> None:
        assert _orm_check(
            DBMatchReview, "ck_match_reviews_match_method_vocabulary"
        ) == migration._in_list("match_method", migration.MATCH_METHODS)
