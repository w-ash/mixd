"""End-to-end test for migration 059 (identity lives in mappings).

``metadata.create_all`` builds the post-059 schema straight from the ORM and
never runs this DDL, so the drop is only proven by driving the real chain
against a throwaway container, the way 051 does.

Two halves matter. The upgrade must take the columns without touching the
mappings: the mapping is where a canonical's Spotify id now lives, and a
migration that dropped the column and the row together would erase the
identity rather than move it. The downgrade must put the column back with the
value the *primary* mapping names — that is the state the promotion hook kept,
and a downgrade that recreated an empty column would silently strand any
reader of it.

Not covered here: a ``spotify_id`` with no live primary spotify mapping. The
upgrade loses it by design (production was repaired to zero such rows in
v0.12.0.1) and the downgrade cannot invent it back.

Marked ``slow``: spins a dedicated container and runs the chain to 058 first.
"""

from datetime import UTC, datetime
import json
from pathlib import Path
import uuid

from alembic.config import Config
import pytest
import sqlalchemy as sa

from alembic import command

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PRE = "058_resolution_event_entity_kind"
_HEAD = "059_identity_lives_in_mappings"

_NOW = datetime(2026, 9, 18, 12, 0, 0, tzinfo=UTC)
_USER = "default"
_LIVE_ID = "sp_live_identity"

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


def _columns(engine: sa.Engine) -> set[str]:
    with engine.connect() as conn:
        return {
            row[0]
            for row in conn.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'tracks'"
                )
            )
        }


def test_059_moves_spotify_identity_into_the_mapping_and_back(migration_db):
    cfg = _alembic_config()
    command.upgrade(cfg, _PRE)

    engine = sa.create_engine(migration_db)
    track_id, ct_id, mapping_id = uuid.uuid7(), uuid.uuid7(), uuid.uuid7()

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO tracks (id, user_id, title, artists, spotify_id, "
                "version, created_at, updated_at) "
                "VALUES (:id, :user, 'Gold Rush', CAST(:artists AS JSONB), "
                ":spotify_id, 1, :now, :now)"
            ),
            {
                "id": track_id,
                "user": _USER,
                "artists": json.dumps({"names": ["Neon Priest"]}),
                "spotify_id": _LIVE_ID,
                "now": _NOW,
            },
        )
        conn.execute(
            sa.text(
                "INSERT INTO connector_tracks (id, connector_name, "
                "connector_track_identifier, title, artists, raw_metadata, "
                "last_updated, created_at, updated_at) "
                "VALUES (:id, 'spotify', :identifier, 'Gold Rush', "
                "CAST(:artists AS JSONB), CAST('{}' AS JSONB), :now, :now, :now)"
            ),
            {
                "id": ct_id,
                "identifier": _LIVE_ID,
                "artists": json.dumps({"names": ["Neon Priest"]}),
                "now": _NOW,
            },
        )
        conn.execute(
            sa.text(
                "INSERT INTO track_mappings (id, user_id, track_id, "
                "connector_track_id, connector_name, match_method, confidence, "
                "origin, is_primary, created_at, updated_at) "
                "VALUES (:id, :user, :track_id, :ct_id, 'spotify', 'direct', 100, "
                "'automatic', TRUE, :now, :now)"
            ),
            {
                "id": mapping_id,
                "user": _USER,
                "track_id": track_id,
                "ct_id": ct_id,
                "now": _NOW,
            },
        )

    assert {"spotify_id", "mbid"} <= _columns(engine)

    command.upgrade(cfg, _HEAD)

    # The columns and their identity keys are gone...
    assert not {"spotify_id", "mbid"} & _columns(engine)
    with engine.connect() as conn:
        constraints = {
            row[0]
            for row in conn.execute(
                sa.text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'tracks'::regclass AND contype = 'u'"
                )
            )
        }
    assert "uq_tracks_user_isrc" in constraints
    assert not {"uq_tracks_user_spotify_id", "uq_tracks_user_mbid"} & constraints

    # ...and the identity itself is untouched, because it lives in the mapping.
    with engine.connect() as conn:
        mapped = conn.execute(
            sa.text(
                "SELECT ct.connector_track_identifier, m.is_primary "
                "FROM track_mappings m "
                "JOIN connector_tracks ct ON ct.id = m.connector_track_id "
                "WHERE m.track_id = :track_id"
            ),
            {"track_id": track_id},
        ).one()
    assert mapped.connector_track_identifier == _LIVE_ID
    assert mapped.is_primary is True

    # Downgrading refills the column from the live primary mapping, so a
    # reader of the old schema sees the same identity it had before.
    command.downgrade(cfg, _PRE)
    assert {"spotify_id", "mbid"} <= _columns(engine)
    with engine.connect() as conn:
        refilled = conn.execute(
            sa.text("SELECT spotify_id, mbid FROM tracks WHERE id = :id"),
            {"id": track_id},
        ).one()
    assert refilled.spotify_id == _LIVE_ID
    assert refilled.mbid is None

    command.upgrade(cfg, _HEAD)
    assert not {"spotify_id", "mbid"} & _columns(engine)

    engine.dispose()
