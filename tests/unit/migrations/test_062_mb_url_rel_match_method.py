"""Migration 062 owns the live ``match_method`` vocabulary.

054 and 060 created the three CHECK constraints; 062 recreated them, so from
this revision on it is 062's inlined list that production enforces and 062's
rendered text the ORM has to agree with character for character. A vocabulary
member added in the domain without a follow-up migration fails here.
"""

import importlib.util
from pathlib import Path
from typing import Any

from sqlalchemy import CheckConstraint
from sqlalchemy.sql.schema import Table

from src.domain.entities.track_mapping import MATCH_METHODS
from src.infrastructure.persistence.database.models import (
    DBArtistMapping,
    DBMatchReview,
    DBTrackMapping,
)

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "alembic"
    / "versions"
    / "062_mb_url_rel_match_method.py"
)


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("migration_062", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_migration()


def _orm_check(table: Table, name: str) -> str:
    constraint = next(
        c
        for c in table.constraints
        if isinstance(c, CheckConstraint) and c.name == name
    )
    return str(constraint.sqltext)


class TestVocabularyAgrees:
    def test_match_methods_match_the_domain(self) -> None:
        assert set(migration.MATCH_METHODS) == set(MATCH_METHODS)

    def test_list_is_sorted_so_the_ddl_is_deterministic(self) -> None:
        assert list(migration.MATCH_METHODS) == sorted(migration.MATCH_METHODS)

    def test_mb_url_rel_is_what_this_revision_added(self) -> None:
        added = set(migration.MATCH_METHODS) - set(migration._PREVIOUS_MATCH_METHODS)
        assert added == {"mb_url_rel"}


class TestConstraintTextAgreesWithTheOrm:
    def test_track_mappings_match_method(self) -> None:
        assert _orm_check(
            DBTrackMapping.__table__, "ck_track_mappings_match_method_vocabulary"
        ) == migration._in_list("match_method", migration.MATCH_METHODS)

    def test_match_reviews_match_method(self) -> None:
        assert _orm_check(
            DBMatchReview.__table__, "ck_match_reviews_match_method_vocabulary"
        ) == migration._in_list("match_method", migration.MATCH_METHODS)

    def test_artist_mappings_match_method(self) -> None:
        assert _orm_check(
            DBArtistMapping.__table__, "ck_artist_mappings_match_method_vocabulary"
        ) == migration._in_list("match_method", migration.MATCH_METHODS)

    def test_it_recreates_all_three_match_method_constraints(self) -> None:
        assert {table for table, _ in migration._CHECKS} == {
            "track_mappings",
            "match_reviews",
            "artist_mappings",
        }
