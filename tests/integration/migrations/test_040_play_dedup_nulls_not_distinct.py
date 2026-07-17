"""End-to-end test for migration 040 (play dedup NULLS NOT DISTINCT).

Drives the real Alembic chain against a throwaway Postgres container because
the migration's behavior lives in SQL the integration harness cannot reach:
the batched NULL-``ms_played`` duplicate collapse (lowest id survives) runs in
an ``autocommit_block`` before the constraint rebuild, and the rebuilt
constraints must reject NULL-``ms_played`` re-inserts that the old
NULL-distinct semantics silently admitted (convergence findings §5c).

Marked ``slow``: spins a dedicated container and runs the chain to 039 first.
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
_PRE = "039_oauth_as_tables"
_HEAD = "040_plays_nulls_not_distinct"

_NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)
_PLAYED_AT = datetime(2024, 11, 5, 9, 15, 0, tzinfo=UTC)

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


def _insert_track(conn: sa.Connection, tid: uuid.UUID) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO tracks (id, user_id, title, artists, version, "
            "created_at, updated_at) "
            "VALUES (:id, 'default', 'Dup Track', CAST(:artists AS JSONB), "
            "1, :now, :now)"
        ),
        {"id": tid, "artists": json.dumps({"names": ["Someone"]}), "now": _NOW},
    )


def _insert_track_play(
    conn: sa.Connection,
    pid: uuid.UUID,
    track_id: uuid.UUID,
    *,
    ms_played: int | None,
    played_at: datetime = _PLAYED_AT,
) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO track_plays (id, user_id, track_id, service, "
            "played_at, ms_played, created_at, updated_at) "
            "VALUES (:id, 'default', :track_id, 'lastfm', :played_at, :ms, "
            ":now, :now)"
        ),
        {
            "id": pid,
            "track_id": track_id,
            "played_at": played_at,
            "ms": ms_played,
            "now": _NOW,
        },
    )


def _insert_connector_play(
    conn: sa.Connection,
    pid: uuid.UUID,
    *,
    ms_played: int | None,
    played_at: datetime = _PLAYED_AT,
) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO connector_plays (id, user_id, connector_name, "
            "connector_track_identifier, played_at, ms_played, raw_metadata, "
            "created_at, updated_at) "
            "VALUES (:id, 'default', 'lastfm', 'someone::dup track', "
            ":played_at, :ms, CAST(:raw AS JSONB), :now, :now)"
        ),
        {"id": pid, "played_at": played_at, "ms": ms_played, "raw": "{}", "now": _NOW},
    )


def _ordered_ids(count: int) -> list[uuid.UUID]:
    return sorted(uuid.uuid7() for _ in range(count))


def test_040_collapses_null_ms_duplicates_and_hardens_constraints(migration_db):
    cfg = _alembic_config()
    command.upgrade(cfg, _PRE)

    engine = sa.create_engine(migration_db)
    track_id = uuid.uuid7()
    tp_dupes = _ordered_ids(3)
    cp_dupes = _ordered_ids(2)
    cp_distinct = uuid.uuid7()

    with engine.begin() as conn:
        _insert_track(conn, track_id)
        # Three exact NULL-ms duplicates (admitted under NULL-distinct
        # semantics) + one same-key row with concrete ms_played.
        for pid in tp_dupes:
            _insert_track_play(conn, pid, track_id, ms_played=None)
        _insert_track_play(conn, uuid.uuid7(), track_id, ms_played=201_000)
        # Ledger: two NULL-ms duplicates + one at a different timestamp.
        for pid in cp_dupes:
            _insert_connector_play(conn, pid, ms_played=None)
        _insert_connector_play(
            conn,
            cp_distinct,
            ms_played=None,
            played_at=datetime(2024, 11, 5, 10, 0, 0, tzinfo=UTC),
        )

    command.upgrade(cfg, _HEAD)

    with engine.connect() as conn:
        tp_rows = conn.execute(
            sa.text(
                "SELECT id, ms_played FROM track_plays ORDER BY ms_played NULLS FIRST"
            )
        ).all()
        # Collapse kept the lowest-id NULL-ms row; the concrete-ms row is a
        # distinct observation and survives.
        assert len(tp_rows) == 2
        assert tp_rows[0].id == tp_dupes[0]
        assert tp_rows[1].ms_played == 201_000

        cp_ids = {
            row.id for row in conn.execute(sa.text("SELECT id FROM connector_plays"))
        }
        assert cp_ids == {cp_dupes[0], cp_distinct}

    # The rebuilt constraints now treat NULL = NULL: exact re-inserts are
    # rejected instead of silently admitted.
    with pytest.raises(sa.exc.IntegrityError, match="uq_track_plays_deduplication"):
        with engine.begin() as conn:
            _insert_track_play(conn, uuid.uuid7(), track_id, ms_played=None)
    with pytest.raises(sa.exc.IntegrityError, match="uq_connector_plays_deduplication"):
        with engine.begin() as conn:
            _insert_connector_play(conn, uuid.uuid7(), ms_played=None)

    # Downgrade restores NULL-distinct semantics; re-upgrading (now with a
    # clean table) converges — the collapse pre-pass is a no-op the second
    # time and the constraints rebuild identically.
    command.downgrade(cfg, _PRE)
    command.upgrade(cfg, _HEAD)

    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM track_plays")).scalar() == 2

    engine.dispose()
