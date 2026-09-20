"""Migration 060's inlined vocabularies must equal the domain's.

The migration inlines its value lists so it stays a frozen record of the schema
at that revision; this is what notices drift. A vocabulary member added in the
domain without a follow-up migration fails here, and so does a constraint whose
rendered text differs between the ORM (which ``metadata.create_all`` gives
integration tests) and the migration (which production runs).
"""

import importlib.util
from pathlib import Path
from typing import Any

from sqlalchemy import CheckConstraint
from sqlalchemy.sql.schema import Table

from src.domain.entities.artist import ARTIST_KINDS
from src.domain.entities.track_mapping import MAPPING_ORIGINS, MATCH_METHODS
from src.infrastructure.persistence.database.models import DBArtist, DBArtistMapping

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "alembic"
    / "versions"
    / "060_artist_tables.py"
)


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("migration_060", _MIGRATION_PATH)
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


class TestVocabulariesAgree:
    def test_artist_kinds_match_the_domain(self) -> None:
        assert set(migration.ARTIST_KINDS) == set(ARTIST_KINDS)

    def test_match_methods_match_the_domain(self) -> None:
        assert set(migration.MATCH_METHODS) == set(MATCH_METHODS)

    def test_mapping_origins_match_the_domain(self) -> None:
        assert set(migration.MAPPING_ORIGINS) == set(MAPPING_ORIGINS)

    def test_lists_are_sorted_so_the_ddl_is_deterministic(self) -> None:
        for vocabulary in (
            migration.ARTIST_KINDS,
            migration.MATCH_METHODS,
            migration.MAPPING_ORIGINS,
        ):
            assert list(vocabulary) == sorted(vocabulary)


class TestConstraintTextAgreesWithTheOrm:
    def test_artist_kind_check(self) -> None:
        assert _orm_check(
            DBArtist.__table__, "ck_artists_kind_vocabulary"
        ) == migration._in_list("kind", migration.ARTIST_KINDS)

    def test_match_method_check(self) -> None:
        assert _orm_check(
            DBArtistMapping.__table__, "ck_artist_mappings_match_method_vocabulary"
        ) == migration._in_list("match_method", migration.MATCH_METHODS)

    def test_origin_check(self) -> None:
        assert _orm_check(
            DBArtistMapping.__table__, "ck_artist_mappings_origin_vocabulary"
        ) == migration._in_list("origin", migration.MAPPING_ORIGINS)


class TestTenancy:
    def test_the_four_user_scoped_tables_get_rls(self) -> None:
        assert set(migration._RLS_TABLES) == {
            "artists",
            "artist_mappings",
            "artist_favorites",
            "track_artists",
        }

    def test_user_id_columns_carry_no_default(self) -> None:
        for table in (DBArtist.__table__, DBArtistMapping.__table__):
            column = table.c.user_id
            assert not column.nullable
            assert column.server_default is None
            assert column.default is None


class TestNoSupersession:
    def test_artist_mappings_has_no_supersession_columns(self) -> None:
        columns = set(DBArtistMapping.__table__.c.keys())
        assert not columns & {
            "superseded_at",
            "superseded_by_id",
            "supersession_reason",
            "supersession_scope",
            "next_verify_at",
        }
