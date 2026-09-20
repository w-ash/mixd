"""End-to-end test for migrations 060 (artist tables) and 061 (credit backfill).

``metadata.create_all`` builds the post-061 schema straight from the ORM and
never runs this DDL, so the tables, the RLS policies and the backfill are only
proven by driving the real chain against a throwaway container, the way 059
does.

Three things matter. The backfill must expand ``tracks.artists`` into one
credit row per name, keeping the JSON order as ``position``. The Various
Artists sentinel must keep its credit row with a null ``artist_id`` — it is an
album-level flag, not a favouritable entity, so dropping the row would lose the
credit and minting an artist would invent one. And the pair must round-trip:
061 down, 060 down, back up, with the JSONB still authoritative throughout.

Marked ``slow``: spins a dedicated container and runs the chain to 059 first.
"""

from datetime import UTC, datetime
import json
from pathlib import Path
import uuid

from alembic.config import Config
import pytest
import sqlalchemy as sa

from alembic import command
from src.infrastructure.persistence.database.backfills import (
    backfill_track_artists_sql,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PRE = "059_identity_lives_in_mappings"
_TABLES = "060_artist_tables"
_HEAD = "061_backfill_track_artists"

_NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)
_USER = "default"

pytestmark = pytest.mark.slow


@pytest.fixture
def migration_db(monkeypatch: pytest.MonkeyPatch):
    """A throwaway Postgres whose schema is owned by Alembic, not ``create_all``."""
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:17-alpine") as pg:
        url = pg.get_connection_url().replace("psycopg2://", "psycopg://")
        monkeypatch.setenv("DATABASE_URL", url)
        yield url


def _alembic_config() -> Config:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    return cfg


def _seed_track(conn: sa.Connection, title: str, names: list[str]) -> uuid.UUID:
    track_id = uuid.uuid7()
    conn.execute(
        sa.text(
            "INSERT INTO tracks (id, user_id, title, artists, version, "
            "created_at, updated_at) "
            "VALUES (:id, :user, :title, CAST(:artists AS JSONB), 1, :now, :now)"
        ),
        {
            "id": track_id,
            "user": _USER,
            "title": title,
            "artists": json.dumps({"names": names}),
            "now": _NOW,
        },
    )
    return track_id


def _credits(engine: sa.Engine, track_id: uuid.UUID) -> list[tuple]:
    with engine.connect() as conn:
        return [
            tuple(row)
            for row in conn.execute(
                sa.text(
                    "SELECT position, credited_name, artist_id, user_id "
                    "FROM track_artists WHERE track_id = :track ORDER BY position"
                ),
                {"track": track_id},
            )
        ]


def _tables(engine: sa.Engine) -> set[str]:
    with engine.connect() as conn:
        return {
            row[0]
            for row in conn.execute(
                sa.text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            )
        }


def _rls_forced(engine: sa.Engine) -> set[str]:
    with engine.connect() as conn:
        return {
            row[0]
            for row in conn.execute(
                sa.text(
                    "SELECT relname FROM pg_class "
                    "WHERE relrowsecurity AND relforcerowsecurity"
                )
            )
        }


_ARTIST_TABLES = frozenset({
    "artists",
    "connector_artists",
    "artist_mappings",
    "artist_favorites",
    "track_artists",
    "artist_aliases",
})


def test_060_061_build_the_tables_and_expand_the_credits(migration_db):
    cfg = _alembic_config()
    command.upgrade(cfg, _PRE)

    engine = sa.create_engine(migration_db)
    assert not _ARTIST_TABLES & _tables(engine)

    with engine.begin() as conn:
        duo = _seed_track(conn, "Odessa", ["Caribou", "Koushik"])
        compilation = _seed_track(conn, "Birdsong", ["Various Artists"])

    command.upgrade(cfg, _HEAD)

    # Every table arrives, and only the user-scoped four are under RLS: the
    # connector cache and its aliases are global service facts.
    assert _tables(engine) >= _ARTIST_TABLES
    forced = _rls_forced(engine)
    assert {"artists", "artist_mappings", "artist_favorites", "track_artists"} <= forced
    assert not {"connector_artists", "artist_aliases"} & forced

    # The JSON order is the position, and the tenant rides along.
    assert _credits(engine, duo) == [
        (0, "Caribou", None, _USER),
        (1, "Koushik", None, _USER),
    ]
    # The sentinel keeps its credit and gets no artist.
    assert _credits(engine, compilation) == [(0, "Various Artists", None, _USER)]

    # The migration-only indexes exist only here, never under create_all.
    with engine.connect() as conn:
        indexes = {
            row[0]
            for row in conn.execute(
                sa.text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
            )
        }
    assert {
        "ix_artists_name_trgm",
        "ix_connector_artists_name_trgm",
        "ix_connector_artists_raw_metadata_gin",
    } <= indexes

    engine.dispose()


def test_rerunning_the_backfill_converges(migration_db):
    cfg = _alembic_config()
    command.upgrade(cfg, _PRE)

    engine = sa.create_engine(migration_db)
    with engine.begin() as conn:
        track_id = _seed_track(conn, "Sun", ["Caribou", "Four Tet"])

    command.upgrade(cfg, _HEAD)

    with engine.begin() as conn:
        conn.execute(sa.text("ALTER TABLE track_artists NO FORCE ROW LEVEL SECURITY"))
        conn.execute(sa.text("ALTER TABLE tracks NO FORCE ROW LEVEL SECURITY"))
        conn.execute(sa.text(backfill_track_artists_sql()))
        conn.execute(sa.text("ALTER TABLE tracks FORCE ROW LEVEL SECURITY"))
        conn.execute(sa.text("ALTER TABLE track_artists FORCE ROW LEVEL SECURITY"))

    assert _credits(engine, track_id) == [
        (0, "Caribou", None, _USER),
        (1, "Four Tet", None, _USER),
    ]

    engine.dispose()


def test_the_pair_round_trips(migration_db):
    cfg = _alembic_config()
    command.upgrade(cfg, _PRE)

    engine = sa.create_engine(migration_db)
    with engine.begin() as conn:
        track_id = _seed_track(conn, "Bowls", ["Caribou"])

    command.upgrade(cfg, _HEAD)
    assert _credits(engine, track_id) == [(0, "Caribou", None, _USER)]

    # 061 down empties the credits; the JSONB is still authoritative, so
    # nothing is lost.
    command.downgrade(cfg, _TABLES)
    assert _credits(engine, track_id) == []
    assert _tables(engine) >= _ARTIST_TABLES

    # 060 down takes the tables with it.
    command.downgrade(cfg, _PRE)
    assert not _ARTIST_TABLES & _tables(engine)

    # And the whole thing rebuilds from the untouched JSONB.
    command.upgrade(cfg, _HEAD)
    assert _credits(engine, track_id) == [(0, "Caribou", None, _USER)]

    engine.dispose()
