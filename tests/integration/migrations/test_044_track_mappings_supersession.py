"""End-to-end test for migration 044 (track-mapping supersession).

Drives the real Alembic chain against a throwaway Postgres container because
everything 044 does lives in SQL the integration harness cannot reach: the
harness builds its schema with ``metadata.create_all``, so the pre-pass DML
(duplicate collapse + FM4d denorm repair, both in an ``autocommit_block``) and
the constraint swap from a full unique to a live-scoped partial unique never
run there.

Marked ``slow``: spins a dedicated container and runs the chain to 043 first.
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
_PRE = "043_adaptive_polling"
_HEAD = "044_track_mappings_supersession"

_NOW = datetime(2026, 7, 27, 12, 0, 0, tzinfo=UTC)

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


def _insert_track(
    conn: sa.Connection, tid: uuid.UUID, title: str, *, spotify_id: str | None
) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO tracks (id, user_id, title, artists, spotify_id, version, "
            "created_at, updated_at) "
            "VALUES (:id, 'default', :title, CAST(:artists AS JSONB), :spotify_id, "
            "1, :now, :now)"
        ),
        {
            "id": tid,
            "title": title,
            "artists": json.dumps({"names": ["Someone"]}),
            "spotify_id": spotify_id,
            "now": _NOW,
        },
    )


def _insert_connector_track(
    conn: sa.Connection, ctid: uuid.UUID, identifier: str
) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO connector_tracks (id, connector_name, "
            "connector_track_identifier, title, artists, raw_metadata, "
            "last_updated, created_at, updated_at) "
            "VALUES (:id, 'spotify', :identifier, 'Some Track', "
            "CAST(:artists AS JSONB), CAST('{}' AS JSONB), :now, :now, :now)"
        ),
        {
            "id": ctid,
            "identifier": identifier,
            "artists": json.dumps({"names": ["Someone"]}),
            "now": _NOW,
        },
    )


def _insert_mapping(
    conn: sa.Connection,
    mid: uuid.UUID,
    track_id: uuid.UUID,
    connector_track_id: uuid.UUID,
    *,
    is_primary: bool = True,
    confidence: int = 100,
) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO track_mappings (id, user_id, track_id, connector_track_id, "
            "connector_name, match_method, confidence, origin, is_primary, "
            "created_at, updated_at) "
            "VALUES (:id, 'default', :track_id, :ct_id, 'spotify', 'direct', "
            ":confidence, 'automatic', :is_primary, :now, :now)"
        ),
        {
            "id": mid,
            "track_id": track_id,
            "ct_id": connector_track_id,
            "confidence": confidence,
            "is_primary": is_primary,
            "now": _NOW,
        },
    )


def test_044_repairs_then_moves_uniqueness_to_live_rows(migration_db):
    cfg = _alembic_config()
    command.upgrade(cfg, _PRE)

    engine = sa.create_engine(migration_db)

    dup_track, dup_ct = uuid.uuid7(), uuid.uuid7()
    dup_keep, dup_drop = sorted((uuid.uuid7(), uuid.uuid7()))
    stale_track, stale_ct, stale_mapping = uuid.uuid7(), uuid.uuid7(), uuid.uuid7()
    orphan_track = uuid.uuid7()
    null_track, null_ct, null_mapping = uuid.uuid7(), uuid.uuid7(), uuid.uuid7()

    with engine.begin() as conn:
        # The constraint 044 replaces would itself block the duplicate seed, so
        # drop it first: the collapse pre-pass exists for databases where this
        # constraint was missing, and that is the state being simulated.
        conn.execute(
            sa.text(
                "ALTER TABLE track_mappings "
                "DROP CONSTRAINT uq_track_mappings_user_connector"
            )
        )

        # (a) Two rows on one live key — only the lowest id may survive.
        _insert_track(conn, dup_track, "Dup", spotify_id="sp_dup")
        _insert_connector_track(conn, dup_ct, "sp_dup")
        _insert_mapping(conn, dup_keep, dup_track, dup_ct)
        _insert_mapping(conn, dup_drop, dup_track, dup_ct, is_primary=False)

        # (b) FM4d arm 1: column disagrees with the primary mapping.
        _insert_track(conn, stale_track, "Stale", spotify_id="sp_dead")
        _insert_connector_track(conn, stale_ct, "sp_live")
        _insert_mapping(conn, stale_mapping, stale_track, stale_ct)

        # (c) FM4d arm 2: column set, no spotify mapping at all.
        _insert_track(conn, orphan_track, "Orphan", spotify_id="sp_ghost")

        # (d) NULL case: column empty but a primary mapping exists — the repair
        # fills it in rather than leaving the fast path blind.
        _insert_track(conn, null_track, "Null", spotify_id=None)
        _insert_connector_track(conn, null_ct, "sp_null")
        _insert_mapping(conn, null_mapping, null_track, null_ct)

    command.upgrade(cfg, _HEAD)

    with engine.connect() as conn:
        surviving = {
            row.id
            for row in conn.execute(
                sa.text("SELECT id FROM track_mappings WHERE connector_track_id = :ct"),
                {"ct": dup_ct},
            )
        }
        assert surviving == {dup_keep}

        repaired = dict(
            conn.execute(sa.text("SELECT id, spotify_id FROM tracks")).all()
        )
        assert repaired[stale_track] == "sp_live"
        assert repaired[orphan_track] is None
        assert repaired[null_track] == "sp_null"
        assert repaired[dup_track] == "sp_dup"

    # Live uniqueness is now enforced by the partial index, not the constraint.
    with pytest.raises(sa.exc.IntegrityError, match="uq_track_mappings_live_connector"):
        with engine.begin() as conn:
            _insert_mapping(
                conn, uuid.uuid7(), dup_track, dup_ct, is_primary=False, confidence=50
            )

    # ...and a superseded row shares that key freely — the whole point. The
    # successor id is stamped before the successor exists, which only works
    # because the self-FK is DEFERRABLE INITIALLY DEFERRED.
    successor_id = uuid.uuid7()
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE track_mappings SET superseded_at = :now, "
                "supersession_reason = 'rematch', superseded_by_id = :successor "
                "WHERE id = :old"
            ),
            {"now": _NOW, "successor": successor_id, "old": dup_keep},
        )
        _insert_mapping(conn, successor_id, dup_track, dup_ct, confidence=90)

    with engine.connect() as conn:
        assert (
            conn.execute(
                sa.text(
                    "SELECT count(*) FROM track_mappings WHERE connector_track_id = :ct"
                ),
                {"ct": dup_ct},
            ).scalar()
            == 2
        )

    # Downgrade deletes superseded rows (they cannot coexist with the restored
    # full unique constraint) and re-upgrading converges.
    command.downgrade(cfg, _PRE)

    with engine.connect() as conn:
        remaining = {
            row.id
            for row in conn.execute(
                sa.text("SELECT id FROM track_mappings WHERE connector_track_id = :ct"),
                {"ct": dup_ct},
            )
        }
        assert remaining == {successor_id}

    command.upgrade(cfg, _HEAD)

    with engine.connect() as conn:
        assert (
            conn.execute(sa.text("SELECT count(*) FROM track_mappings")).scalar() == 3
        )

        # The pre-pass runs its cross-tenant repair under NO FORCE ROW LEVEL
        # SECURITY, in an autocommit block — so a crash mid-loop would leave
        # the bracket open and every later query would silently bypass the
        # tenant policy on the table owner. Both tables must come out forced
        # (precedent: test_035_lastfm_identifier_fold).
        forced = conn.execute(
            sa.text(
                "SELECT bool_and(relforcerowsecurity) FROM pg_class "
                "WHERE relname IN ('track_mappings','tracks')"
            )
        ).scalar_one()
        assert forced is True

    engine.dispose()


def test_044_re_forces_a_bracket_a_previous_run_leaked(migration_db):
    """A crashed pre-pass leaves NO FORCE behind; re-running must repair it.

    Autocommit means the ``finally`` that restores FORCE is not transactional,
    so the leak survives the failure — and RLS still reads as *enabled*, which
    is what makes it invisible. Re-asserting FORCE at the top of ``upgrade``
    costs nothing on a healthy database and is the only thing that heals this.
    """
    cfg = _alembic_config()
    command.upgrade(cfg, _PRE)
    engine = sa.create_engine(migration_db)

    with engine.begin() as conn:
        for table in ("track_mappings", "tracks"):
            conn.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))

    command.upgrade(cfg, _HEAD)

    with engine.connect() as conn:
        forced = conn.execute(
            sa.text(
                "SELECT bool_and(relforcerowsecurity) FROM pg_class "
                "WHERE relname IN ('track_mappings','tracks')"
            )
        ).scalar_one()
        assert forced is True

    engine.dispose()
