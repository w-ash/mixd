"""Schema gates for the per-aggregate ORM model package.

Two permanent CI fixtures (both ``slow``, so they run under ``-m ""``):

1. **Mapper-registry completeness** — every mapped class configures and every
   ``relationship()`` resolves to a class in the same registry. Models are
   spread over ``database/models/``, so a cross-module target such as
   ``DBTrack.mappings`` is a deferred annotation (PEP 649) that resolves lazily
   by class name at ``configure_mappers()`` time; a module that stops being imported turns into a runtime error on the
   first query that touches the relationship. This gate fails at import time
   instead.

2. **Empty autogenerate diff** — a fresh Postgres migrated to ``head`` compares
   equal to ``DatabaseModel.metadata``. This is what catches a model module the
   package no longer imports (the table is in the migrations but not in the
   metadata) and any ORM change that shipped without its migration.

The integration harness builds its schema with ``metadata.create_all`` and never
runs the migration chain, so gate 2 is the only place the two are compared.

Test-only ORM models must never be registered on ``DatabaseModel.metadata`` —
gate 2 would report them as tables missing from the migrations. Give such models
their own ``MetaData``.
"""

from collections.abc import Iterator
from pathlib import Path
from pprint import pformat
from typing import Final

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Mapper, configure_mappers

from alembic import command
from src.config.settings import database_host_and_mode, get_database_url
from src.infrastructure.persistence.database.models import DatabaseModel

pytestmark = pytest.mark.slow

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Indexes that exist only in migrations, by house convention: pg_trgm / GIN /
# BRIN indexes need the extension (or a physical layout) that
# ``metadata.create_all`` cannot express, so they are deliberately absent from
# the ORM and from test databases. Every other index must be declared on both
# sides — a new migration-only index fails the gate until it is listed here
# with the migration that owns it.
_MIGRATION_ONLY_INDEXES: Final[frozenset[str]] = frozenset({
    "ix_tracks_title_trgm",  # 002_pg_opt
    "ix_tracks_album_trgm",  # 002_pg_opt
    "ix_tracks_artists_text_trgm",  # 002_pg_opt
    "ix_tracks_artists_gin",  # 002_pg_opt
    "ix_track_plays_played_at_brin",  # 002_pg_opt
    "ix_track_tags_tag_trgm",  # c602c5a08631 (track_tags)
})


class TestMapperRegistryCompleteness:
    def test_every_mapper_configures_and_every_relationship_resolves(self) -> None:
        configure_mappers()
        mappers: list[Mapper[object]] = list(DatabaseModel.registry.mappers)
        assert mappers, "no mapped classes on DatabaseModel.registry"

        unresolved: list[str] = []
        for mapper in mappers:
            assert mapper.configured, f"{mapper.class_.__name__} did not configure"
            for rel in mapper.relationships:
                try:
                    target = rel.mapper
                except sa.exc.SQLAlchemyError as exc:
                    unresolved.append(f"{mapper.class_.__name__}.{rel.key}: {exc!r}")
                    continue
                if target not in DatabaseModel.registry.mappers:
                    unresolved.append(
                        f"{mapper.class_.__name__}.{rel.key} -> "
                        f"{target.class_.__name__} is mapped on another registry"
                    )
        assert not unresolved, "unresolved relationships:\n" + "\n".join(unresolved)

    def test_metadata_tables_match_mapped_classes(self) -> None:
        configure_mappers()
        mapped_tables = {
            mapper.class_.__tablename__
            for mapper in DatabaseModel.registry.mappers
            if getattr(mapper.class_, "__tablename__", None)
        }
        metadata_tables = set(DatabaseModel.metadata.tables)
        assert mapped_tables == metadata_tables, (
            f"tables without a mapped class: {sorted(metadata_tables - mapped_tables)}; "
            f"mapped classes without a table: {sorted(mapped_tables - metadata_tables)}"
        )
        assert len(metadata_tables) == len(mapped_tables)


@pytest.fixture
def migration_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A throwaway Postgres whose schema is owned by Alembic, not ``create_all``.

    ``alembic/env.py`` binds its engine from ``DATABASE_URL``; ``.env.local``
    points that at production, so the container URL is set on ``os.environ``
    (shell precedence beats the dotenv stack) and the test proves it took
    effect before running ``upgrade``.
    """
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:17-alpine") as pg:
        url = pg.get_connection_url().replace("psycopg2://", "psycopg://")
        monkeypatch.setenv("DATABASE_URL", url)
        yield url


def _alembic_config() -> Config:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    return cfg


def _include_object(
    obj: sa.schema.SchemaItem,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: sa.schema.SchemaItem | None,
) -> bool:
    """Autogenerate filter for DDL the house convention keeps migration-only.

    - CHECK constraints may live in migrations alone (house convention, see
      the database-schema skill: ``schedules``, ``operation_runs`` and the
      status/kind vocabularies are enforced only there). A *reflected* CHECK
      the ORM does not declare is therefore not drift. An ORM-declared CHECK
      missing from the database still is, and passes through.
    - The named pg_trgm / GIN / BRIN indexes in ``_MIGRATION_ONLY_INDEXES``.
    """
    del obj
    if type_ == "check_constraint" and reflected and compare_to is None:
        return False
    return not (type_ == "index" and name in _MIGRATION_ONLY_INDEXES)


class TestEmptyAutogenerateDiff:
    def test_migrated_schema_matches_orm_metadata(self, migration_db: str) -> None:
        # Prove the container URL is the one in effect: it is what the settings
        # resolver returns, it is local, and the database is still empty.
        assert get_database_url() == migration_db
        assert database_host_and_mode(migration_db)[1] == "local"
        engine = sa.create_engine(migration_db)
        try:
            with engine.connect() as conn:
                assert not sa.inspect(conn).get_table_names(), (
                    "fresh container is not empty"
                )

            cfg = _alembic_config()
            command.upgrade(cfg, "head")

            expected_head = ScriptDirectory.from_config(cfg).get_current_head()
            with engine.connect() as conn:
                applied = conn.execute(
                    sa.text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                assert applied == expected_head, (
                    "upgrade did not run against the container"
                )

                ctx = MigrationContext.configure(
                    conn,
                    opts={
                        "compare_type": True,
                        "compare_server_default": True,
                        "include_object": _include_object,
                    },
                )
                diff = compare_metadata(ctx, DatabaseModel.metadata)
        finally:
            engine.dispose()

        assert diff == [], (
            f"{len(diff)} autogenerate difference(s) between migrations and "
            f"DatabaseModel.metadata — an ORM change shipped without its migration, "
            f"or a model module is no longer imported:\n{pformat(diff, width=120)}"
        )
