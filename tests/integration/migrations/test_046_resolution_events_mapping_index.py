"""End-to-end test for migration 046 (swap the event log's indexes).

An index swap is exactly the kind of migration ``metadata.create_all`` cannot
vouch for: the model file declares the *destination* schema, so a test built
from it would pass whether or not the migration that gets production there
exists. So this drives the real chain and reads ``pg_indexes`` — the definition
Postgres actually built, including the partial predicate, which is where an
index silently stops serving the query it was written for.

The downgrade leg matters more than usual here. 046 drops an index it did not
create, so its downgrade has to reconstruct 045's definition from memory rather
than reverse an operation; a paraphrase (a missing ``DESC``, a stray predicate)
would leave a rolled-back deploy on a schema neither release ever shipped.

Marked ``slow``: spins a dedicated container and runs the whole chain.
"""

from pathlib import Path

from alembic.config import Config
import pytest
import sqlalchemy as sa

from alembic import command

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PRE = "045_resolution_events"
# Shorter than the migration's file name: ``alembic_version.version_num`` is
# VARCHAR(32), and a longer id fails only after the DDL has already run.
_HEAD = "046_resolution_events_mapping"

_EVENTS = "resolution_events"
_MAPPING_INDEX = "ix_resolution_events_mapping"
_MATCHER_INDEX = "ix_resolution_events_matcher_version"

pytestmark = pytest.mark.slow


@pytest.fixture
def migration_db(monkeypatch: pytest.MonkeyPatch):
    """A throwaway Postgres whose schema is owned by Alembic, not ``create_all``."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:17-alpine") as pg:
        url = pg.get_connection_url().replace("psycopg2://", "psycopg://")
        monkeypatch.setenv("DATABASE_URL", url)
        yield url


def _alembic_config() -> Config:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    return cfg


def _event_indexes(conn: sa.Connection) -> dict[str, str]:
    """Every index on the event table, name -> the definition Postgres built."""
    return {
        row.indexname: row.indexdef
        for row in conn.execute(
            sa.text(
                "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = :table"
            ),
            {"table": _EVENTS},
        )
    }


def test_upgrade_creates_partial_mapping_index_and_drops_matcher_version(
    migration_db: str,
):
    """The swap, asserted on both sides.

    The new index has to lead with the ``events_for_mapping`` predicate columns
    *and* carry ``recorded_at DESC``, or the LIMIT still costs a sort; and it
    has to keep the partial predicate, or the write it was meant to cheapen is
    paid on every event again.
    """
    cfg = _alembic_config()
    command.upgrade(cfg, _PRE)
    engine = sa.create_engine(migration_db)

    with engine.connect() as conn:
        assert _MATCHER_INDEX in _event_indexes(conn)

    command.upgrade(cfg, _HEAD)

    with engine.connect() as conn:
        indexes = _event_indexes(conn)
        assert _MATCHER_INDEX not in indexes
        definition = indexes[_MAPPING_INDEX]
        assert "user_id, resulting_mapping_id, recorded_at DESC" in definition
        assert "WHERE (resulting_mapping_id IS NOT NULL)" in definition
        # The timeline and connector-track indexes are none of 046's business.
        assert "ix_resolution_events_user_time" in indexes
        assert "ix_resolution_events_connector_track" in indexes

    engine.dispose()


def test_downgrade_restores_matcher_version_index(migration_db: str):
    """A rolled-back deploy must land on 045's schema exactly, then re-upgrade.

    ``matcher_version, recorded_at DESC`` with no predicate is 045's definition
    verbatim; asserting the whole tail of the statement is what catches a
    downgrade that recreated *an* index by that name rather than *the* index.
    """
    cfg = _alembic_config()
    command.upgrade(cfg, _HEAD)
    engine = sa.create_engine(migration_db)

    command.downgrade(cfg, _PRE)

    with engine.connect() as conn:
        indexes = _event_indexes(conn)
        assert _MAPPING_INDEX not in indexes
        restored = indexes[_MATCHER_INDEX]
        assert restored.endswith(
            f"ON public.{_EVENTS} USING btree (matcher_version, recorded_at DESC)"
        )

    # Upgrade-downgrade-upgrade: the second pass has to be as valid as the
    # first, which it is not if the downgrade left the table subtly off-schema.
    command.upgrade(cfg, _HEAD)

    with engine.connect() as conn:
        indexes = _event_indexes(conn)
        assert _MATCHER_INDEX not in indexes
        assert "WHERE (resulting_mapping_id IS NOT NULL)" in indexes[_MAPPING_INDEX]

    engine.dispose()
