"""End-to-end test for migration 050 (play exclusion reason backfill).

Drives the real Alembic chain against a throwaway Postgres because the
migration's whole behavior is SQL: three UPDATEs whose ordering makes the
classes disjoint, and a join between two tables that identify a Spotify track
differently — the ledger stores ``spotify:track:<id>``, ``connector_tracks``
the bare id. A first draft of that join omitted the prefix strip, matched
nothing, and quietly filed all 79,659 skips as resolution failures, which is
precisely the ambiguity the column exists to remove. A silent misclassification
looks identical to a correct run, so it needs a test that reads the values.

Not covered here: the ``NO FORCE ROW LEVEL SECURITY`` bracket around the
backfill. The testcontainers role is a superuser and bypasses RLS
unconditionally, so the backfill would pass this test even with the bracket
removed — on an owner without BYPASSRLS the three UPDATEs would instead match
zero rows and ship the column entirely NULL while reporting success. Reaching
that path needs a dedicated non-bypass owner role; migrations 035 and 040 carry
the same bracket and the same gap.

Marked ``slow``: spins a dedicated container and runs the chain to 049 first.
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
_PRE = "049_tracks_user_normalized"
_HEAD = "050_play_exclusion_reason"

_NOW = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)
_PLAYED_AT = datetime(2025, 6, 1, 9, 15, 0, tzinfo=UTC)
_USER = "test-user"
_SPOTIFY_ID = "4iV5W9uYEdYUVa79Axb7Rh"

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


def _insert_known_track(conn: sa.Connection) -> None:
    """A canonical track reachable from the bare Spotify id, as resolution leaves it."""
    track_id, connector_track_id = uuid.uuid7(), uuid.uuid7()
    conn.execute(
        sa.text(
            "INSERT INTO tracks (id, user_id, title, artists, version, "
            "created_at, updated_at) "
            "VALUES (:id, :user, 'Known Track', CAST(:artists AS JSONB), 1, "
            ":now, :now)"
        ),
        {
            "id": track_id,
            "user": _USER,
            "artists": json.dumps({"names": ["Someone"]}),
            "now": _NOW,
        },
    )
    conn.execute(
        sa.text(
            "INSERT INTO connector_tracks (id, connector_name, "
            "connector_track_identifier, title, artists, raw_metadata, "
            "last_updated, created_at, updated_at) "
            "VALUES (:id, 'spotify', :ident, 'Known Track', "
            "CAST(:artists AS JSONB), CAST('{}' AS JSONB), :now, :now, :now)"
        ),
        {
            "id": connector_track_id,
            # Bare id — deliberately NOT the URI the ledger stores.
            "ident": _SPOTIFY_ID,
            "artists": json.dumps({"names": ["Someone"]}),
            "now": _NOW,
        },
    )
    conn.execute(
        sa.text(
            "INSERT INTO track_mappings (id, user_id, track_id, "
            "connector_track_id, connector_name, match_method, confidence, "
            "is_primary, created_at, updated_at) "
            "VALUES (:id, :user, :track_id, :ct_id, 'spotify', 'direct', 100, "
            "TRUE, :now, :now)"
        ),
        {
            "id": uuid.uuid7(),
            "user": _USER,
            "track_id": track_id,
            "ct_id": connector_track_id,
            "now": _NOW,
        },
    )


def _insert_connector_play(
    conn: sa.Connection,
    pid: uuid.UUID,
    *,
    identifier: str,
    played_at: datetime,
    incognito: bool = False,
    resolved_track_id: uuid.UUID | None = None,
) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO connector_plays (id, user_id, connector_name, "
            "connector_track_identifier, played_at, ms_played, raw_metadata, "
            "resolved_track_id, import_source, created_at, updated_at) "
            "VALUES (:id, :user, 'spotify', :ident, :played_at, 20000, "
            "CAST(:raw AS JSONB), :resolved, 'spotify_export', :now, :now)"
        ),
        {
            "id": pid,
            "user": _USER,
            "ident": identifier,
            "played_at": played_at,
            "raw": json.dumps({
                "service_metadata": {"incognito_mode": incognito},
            }),
            "resolved": resolved_track_id,
            "now": _NOW,
        },
    )


def _reasons(engine: sa.Engine) -> dict[uuid.UUID, str | None]:
    with engine.begin() as conn:
        return {
            row.id: row.exclusion_reason
            for row in conn.execute(
                sa.text("SELECT id, exclusion_reason FROM connector_plays")
            )
        }


def test_050_separates_skips_from_genuine_resolution_failures(migration_db):
    cfg = _alembic_config()
    command.upgrade(cfg, _PRE)

    engine = sa.create_engine(migration_db)
    skipped = uuid.uuid7()
    unknown = uuid.uuid7()
    private = uuid.uuid7()
    counted = uuid.uuid7()
    counted_track = uuid.uuid7()

    with engine.begin() as conn:
        _insert_known_track(conn)
        # The track resolved; the play was dropped by the listen threshold.
        _insert_connector_play(
            conn,
            skipped,
            identifier=f"spotify:track:{_SPOTIFY_ID}",
            played_at=_PLAYED_AT,
        )
        # No connector track anywhere — a real failure.
        _insert_connector_play(
            conn,
            unknown,
            identifier="spotify:track:0000000000000000000000",
            played_at=_PLAYED_AT.replace(hour=10),
        )
        # Private session: classified first, before the track lookup.
        _insert_connector_play(
            conn,
            private,
            identifier=f"spotify:track:{_SPOTIFY_ID}",
            played_at=_PLAYED_AT.replace(hour=11),
            incognito=True,
        )
        # A counted play must come out of the backfill with no reason at all.
        conn.execute(
            sa.text(
                "INSERT INTO tracks (id, user_id, title, artists, version, "
                "created_at, updated_at) "
                "VALUES (:id, :user, 'Counted', CAST(:artists AS JSONB), 1, "
                ":now, :now)"
            ),
            {
                "id": counted_track,
                "user": _USER,
                "artists": json.dumps({"names": ["Someone"]}),
                "now": _NOW,
            },
        )
        _insert_connector_play(
            conn,
            counted,
            identifier=f"spotify:track:{_SPOTIFY_ID}",
            played_at=_PLAYED_AT.replace(hour=12),
            resolved_track_id=counted_track,
        )

    command.upgrade(cfg, _HEAD)

    reasons = _reasons(engine)
    # The load-bearing assertion: a skip whose track is known must NOT read as
    # a resolution failure. The prefix-stripping join is the only thing that
    # separates them, and getting it wrong fails silently.
    assert reasons[skipped] == "too_short"
    assert reasons[unknown] == "unresolved"
    assert reasons[private] == "incognito"
    assert reasons[counted] is None

    command.downgrade(cfg, _PRE)
    with engine.begin() as conn:
        remaining = conn.execute(
            sa.text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'connector_plays' "
                "AND column_name = 'exclusion_reason'"
            )
        ).scalar_one()
    assert remaining == 0
