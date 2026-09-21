"""End-to-end test for migrations 060 (artist tables) and 061 (credit backfill).

``metadata.create_all`` builds the post-061 schema straight from the ORM and
never runs this DDL, so the tables, the RLS policies and the backfill are only
proven by driving the real chain against a throwaway container, the way 059
does.

Four things matter. The backfill must expand ``tracks.artists`` into one
credit row per name, keeping the JSON order as ``position``. The Various
Artists sentinel must keep its credit row with a null ``artist_id`` — it is an
album-level flag, not a favouritable entity, so dropping the row would lose the
credit and minting an artist would invent one. The connector side must mint
``connector_artists`` from the stored payloads' ``artists`` dumps and expand
``connector_tracks.artists`` into ``connector_track_artists`` rows pointing at
them (null where the dump has no id). And the pair must round-trip: 061 down,
060 down, back up, with the JSONB still authoritative throughout.

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
    backfill_connector_artists_sql,
    backfill_connector_track_artists_sql,
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


def _seed_connector_track(
    conn: sa.Connection,
    connector: str,
    identifier: str,
    names: list[str],
    raw_metadata: dict[str, object],
) -> uuid.UUID:
    row_id = uuid.uuid7()
    conn.execute(
        sa.text(
            "INSERT INTO connector_tracks (id, connector_name, "
            "connector_track_identifier, title, artists, raw_metadata, "
            "last_updated, created_at, updated_at) "
            "VALUES (:id, :connector, :identifier, :title, CAST(:artists AS JSONB), "
            "CAST(:raw AS JSONB), :now, :now, :now)"
        ),
        {
            "id": row_id,
            "connector": connector,
            "identifier": identifier,
            "title": identifier,
            "artists": json.dumps({"names": names}),
            "raw": json.dumps(raw_metadata),
            "now": _NOW,
        },
    )
    return row_id


def _connector_credits(engine: sa.Engine, connector_track_id: uuid.UUID) -> list[tuple]:
    """``(position, credited name, service artist id)`` per connector credit row."""
    with engine.connect() as conn:
        return [
            tuple(row)
            for row in conn.execute(
                sa.text(
                    "SELECT cta.position, cta.credited_name, "
                    "ca.connector_artist_identifier "
                    "FROM connector_track_artists cta "
                    "LEFT JOIN connector_artists ca ON ca.id = cta.connector_artist_id "
                    "WHERE cta.connector_track_id = :track ORDER BY cta.position"
                ),
                {"track": connector_track_id},
            )
        ]


def _connector_artists(engine: sa.Engine) -> dict[tuple[str, str], tuple[str, dict]]:
    with engine.connect() as conn:
        return {
            (row[0], row[1]): (row[2], row[3])
            for row in conn.execute(
                sa.text(
                    "SELECT connector_name, connector_artist_identifier, name, "
                    "raw_metadata FROM connector_artists"
                )
            )
        }


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
    "connector_track_artists",
    "artist_aliases",
})

_SPOTIFY_DUMP = {
    "id": "sp-t1",
    "artists": [
        {"id": "sp-a1", "name": "Caribou", "type": "artist"},
        {"id": "sp-a2", "name": "Koushik", "type": "artist"},
    ],
}


def test_060_061_build_the_tables_and_expand_the_credits(migration_db):
    cfg = _alembic_config()
    command.upgrade(cfg, _PRE)

    engine = sa.create_engine(migration_db)
    assert not _ARTIST_TABLES & _tables(engine)

    with engine.begin() as conn:
        duo = _seed_track(conn, "Odessa", ["Caribou", "Koushik"])
        compilation = _seed_track(conn, "Birdsong", ["Various Artists"])
        spotify = _seed_connector_track(
            conn, "spotify", "sp-t1", ["Caribou", "Koushik"], _SPOTIFY_DUMP
        )
        # The same dump under a second track: one record, not two.
        spotify_again = _seed_connector_track(
            conn,
            "spotify",
            "sp-t2",
            ["Caribou"],
            {
                "id": "sp-t2",
                "artists": [{"id": "sp-a1", "name": "Caribou", "type": "artist"}],
            },
        )
        apple = _seed_connector_track(
            conn, "apple", "ap-1", ["Tycho"], {"id": "ap-1", "artistName": "Tycho"}
        )
        # An ``artists`` that is not a list of objects mints nothing.
        odd = _seed_connector_track(
            conn, "lastfm", "a::b", ["Bonobo"], {"artists": "nope"}
        )

    command.upgrade(cfg, _HEAD)

    # Every table arrives, and only the user-scoped four are under RLS: the
    # connector cache, its credits and its aliases are global service facts.
    assert _tables(engine) >= _ARTIST_TABLES
    forced = _rls_forced(engine)
    assert {"artists", "artist_mappings", "artist_favorites", "track_artists"} <= forced
    assert (
        not {"connector_artists", "connector_track_artists", "artist_aliases"} & forced
    )

    # The connector records are minted from the dumps, once per identity,
    # carrying the dump as their payload.
    records = _connector_artists(engine)
    assert set(records) == {("spotify", "sp-a1"), ("spotify", "sp-a2")}
    assert records["spotify", "sp-a1"] == (
        "Caribou",
        {"id": "sp-a1", "name": "Caribou", "type": "artist"},
    )
    # And every connector credit points at its record, or at nothing.
    assert _connector_credits(engine, spotify) == [
        (0, "Caribou", "sp-a1"),
        (1, "Koushik", "sp-a2"),
    ]
    assert _connector_credits(engine, spotify_again) == [(0, "Caribou", "sp-a1")]
    assert _connector_credits(engine, apple) == [(0, "Tycho", None)]
    assert _connector_credits(engine, odd) == [(0, "Bonobo", None)]

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
        spotify = _seed_connector_track(
            conn, "spotify", "sp-t1", ["Caribou", "Koushik"], _SPOTIFY_DUMP
        )

    command.upgrade(cfg, _HEAD)

    with engine.begin() as conn:
        conn.execute(sa.text("ALTER TABLE track_artists NO FORCE ROW LEVEL SECURITY"))
        conn.execute(sa.text("ALTER TABLE tracks NO FORCE ROW LEVEL SECURITY"))
        conn.execute(sa.text(backfill_track_artists_sql()))
        conn.execute(sa.text("ALTER TABLE tracks FORCE ROW LEVEL SECURITY"))
        conn.execute(sa.text("ALTER TABLE track_artists FORCE ROW LEVEL SECURITY"))
        conn.execute(sa.text(backfill_connector_artists_sql()))
        conn.execute(sa.text(backfill_connector_track_artists_sql()))

    assert _credits(engine, track_id) == [
        (0, "Caribou", None, _USER),
        (1, "Four Tet", None, _USER),
    ]
    assert len(_connector_artists(engine)) == 2
    assert _connector_credits(engine, spotify) == [
        (0, "Caribou", "sp-a1"),
        (1, "Koushik", "sp-a2"),
    ]

    engine.dispose()


def test_the_pair_round_trips(migration_db):
    cfg = _alembic_config()
    command.upgrade(cfg, _PRE)

    engine = sa.create_engine(migration_db)
    with engine.begin() as conn:
        track_id = _seed_track(conn, "Bowls", ["Caribou"])
        spotify = _seed_connector_track(
            conn, "spotify", "sp-t1", ["Caribou", "Koushik"], _SPOTIFY_DUMP
        )

    command.upgrade(cfg, _HEAD)
    assert _credits(engine, track_id) == [(0, "Caribou", None, _USER)]
    assert _connector_credits(engine, spotify) == [
        (0, "Caribou", "sp-a1"),
        (1, "Koushik", "sp-a2"),
    ]

    # 061 down empties both credit tables; the JSONB is still authoritative,
    # so nothing is lost. The minted connector records stay.
    command.downgrade(cfg, _TABLES)
    assert _credits(engine, track_id) == []
    assert _connector_credits(engine, spotify) == []
    assert len(_connector_artists(engine)) == 2
    assert _tables(engine) >= _ARTIST_TABLES

    # 060 down takes the tables with it.
    command.downgrade(cfg, _PRE)
    assert not _ARTIST_TABLES & _tables(engine)

    # And the whole thing rebuilds from the untouched JSONB.
    command.upgrade(cfg, _HEAD)
    assert _credits(engine, track_id) == [(0, "Caribou", None, _USER)]
    assert _connector_credits(engine, spotify) == [
        (0, "Caribou", "sp-a1"),
        (1, "Koushik", "sp-a2"),
    ]

    engine.dispose()
