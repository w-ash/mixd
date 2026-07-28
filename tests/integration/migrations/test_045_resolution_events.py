"""End-to-end test for migration 045 (resolution event log + negative cache).

Drives the real Alembic chain against a throwaway Postgres because the parts of
045 worth testing are the parts ``metadata.create_all`` never exercises in
anger: the ``clock_timestamp()`` default on ``recorded_at``, the two *partial* unique
indexes that keep ``no_match`` and ``rejected_pair`` rows in separate key
spaces, and the CASCADE behaviour of the negative table's foreign keys.

The event table's most important property is negative — it has no foreign keys
at all — and that is asserted directly, because a well-meaning later migration
adding one would silently make the log deletable by cascade.

Marked ``slow``: spins a dedicated container and runs the whole chain.
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
_PRE = "044_track_mappings_supersession"
_HEAD = "045_resolution_events"

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


def _seed_entities(conn: sa.Connection) -> tuple[uuid.UUID, uuid.UUID]:
    """One canonical track and one connector track for the FKs to point at."""
    track_id, ct_id = uuid.uuid7(), uuid.uuid7()
    conn.execute(
        sa.text(
            "INSERT INTO tracks (id, user_id, title, artists, version, "
            "created_at, updated_at) "
            "VALUES (:id, 'default', 'A Song', CAST(:artists AS JSONB), 1, :now, :now)"
        ),
        {"id": track_id, "artists": json.dumps({"names": ["Someone"]}), "now": _NOW},
    )
    conn.execute(
        sa.text(
            "INSERT INTO connector_tracks (id, connector_name, "
            "connector_track_identifier, title, artists, raw_metadata, "
            "last_updated, created_at, updated_at) "
            "VALUES (:id, 'spotify', 'sp_1', 'A Song', CAST(:artists AS JSONB), "
            "CAST('{}' AS JSONB), :now, :now, :now)"
        ),
        {"id": ct_id, "artists": json.dumps({"names": ["Someone"]}), "now": _NOW},
    )
    return track_id, ct_id


def _insert_negative(
    conn: sa.Connection,
    *,
    kind: str,
    ct_id: uuid.UUID,
    candidate: uuid.UUID | None,
) -> uuid.UUID:
    row_id = uuid.uuid7()
    conn.execute(
        sa.text(
            "INSERT INTO resolution_negatives (id, user_id, kind, connector_name, "
            "connector_track_id, candidate_track_id, matcher_version, "
            "consecutive_misses, created_at, updated_at) "
            "VALUES (:id, 'default', :kind, 'spotify', :ct, :candidate, 'v1', 0, "
            ":now, :now)"
        ),
        {
            "id": row_id,
            "kind": kind,
            "ct": ct_id,
            "candidate": candidate,
            "now": _NOW,
        },
    )
    return row_id


def test_045_upgrade_downgrade_upgrade(migration_db: str):
    cfg = _alembic_config()
    command.upgrade(cfg, _PRE)
    engine = sa.create_engine(migration_db.replace("+psycopg", "+psycopg"))

    command.upgrade(cfg, _HEAD)

    with engine.begin() as conn:
        track_id, ct_id = _seed_entities(conn)

    # recorded_at is the database's clock: the insert names every other column
    # and still gets a timestamp.
    event_id = uuid.uuid7()
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO resolution_events (id, user_id, event_type, "
                "matcher_version, connector_name, connector_track_id, track_id) "
                "VALUES (:id, 'default', 'accepted', 'abc123', 'spotify', :ct, :track)"
            ),
            {"id": event_id, "ct": ct_id, "track": track_id},
        )

    with engine.connect() as conn:
        recorded_at, payload = conn.execute(
            sa.text(
                "SELECT recorded_at, payload FROM resolution_events WHERE id = :id"
            ),
            {"id": event_id},
        ).one()
        assert recorded_at is not None
        assert payload == {}

        # No FKs in or out — the log must outlive everything it describes.
        fks = conn.execute(
            sa.text(
                "SELECT count(*) FROM pg_constraint WHERE contype = 'f' "
                "AND (conrelid = 'resolution_events'::regclass "
                "OR confrelid = 'resolution_events'::regclass)"
            )
        ).scalar()
        assert fks == 0

    # Two partial uniques, two key spaces. A second no_match for the same
    # connector track collides...
    with engine.begin() as conn:
        _ = _insert_negative(conn, kind="no_match", ct_id=ct_id, candidate=None)
    with pytest.raises(sa.exc.IntegrityError, match="uq_resolution_negatives_no_match"):
        with engine.begin() as conn:
            _ = _insert_negative(conn, kind="no_match", ct_id=ct_id, candidate=None)

    # ...while a rejected_pair on the same connector track does not, because it
    # lives in the other index entirely.
    with engine.begin() as conn:
        pair_id = _insert_negative(
            conn, kind="rejected_pair", ct_id=ct_id, candidate=track_id
        )
    with pytest.raises(sa.exc.IntegrityError, match="uq_resolution_negatives_pair"):
        with engine.begin() as conn:
            _ = _insert_negative(
                conn, kind="rejected_pair", ct_id=ct_id, candidate=track_id
            )

    # Negative-cache rows are state about live entities: deleting the candidate
    # cascades, while the event log is untouched.
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM tracks WHERE id = :id"), {"id": track_id})
    with engine.connect() as conn:
        assert (
            conn.execute(
                sa.text("SELECT count(*) FROM resolution_negatives WHERE id = :id"),
                {"id": pair_id},
            ).scalar()
            == 0
        )
        assert (
            conn.execute(
                sa.text("SELECT count(*) FROM resolution_events WHERE id = :id"),
                {"id": event_id},
            ).scalar()
            == 1
        )

    command.downgrade(cfg, _PRE)
    with engine.connect() as conn:
        assert (
            conn.execute(
                sa.text("SELECT to_regclass('public.resolution_events')")
            ).scalar()
            is None
        )
        assert (
            conn.execute(
                sa.text("SELECT to_regclass('public.resolution_negatives')")
            ).scalar()
            is None
        )

    command.upgrade(cfg, _HEAD)
    with engine.connect() as conn:
        assert (
            conn.execute(sa.text("SELECT count(*) FROM resolution_events")).scalar()
            == 0
        )

    engine.dispose()


def test_045_new_tables_are_tenant_isolated(migration_db: str):
    """Both tables enabled *and* forced, each with the isolation policy.

    ``ENABLE`` alone exempts the table owner, which is the role the app
    connects as — so an un-``FORCE``d table has RLS that reads as on and does
    nothing (precedent: test_035_lastfm_identifier_fold).
    """
    cfg = _alembic_config()
    command.upgrade(cfg, _HEAD)
    engine = sa.create_engine(migration_db)

    with engine.connect() as conn:
        for table in ("resolution_events", "resolution_negatives"):
            enabled, forced = conn.execute(
                sa.text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relname = :table"
                ),
                {"table": table},
            ).one()
            assert enabled is True, table
            assert forced is True, table
            policies = conn.execute(
                sa.text(
                    "SELECT count(*) FROM pg_policies WHERE tablename = :table "
                    "AND policyname = 'user_isolation'"
                ),
                {"table": table},
            ).scalar()
            assert policies == 1, table

    engine.dispose()


def test_045_kind_and_candidate_must_agree(migration_db: str):
    """The CHECK that makes the two partial key spaces exhaustive.

    A ``no_match`` row carrying a candidate would fall outside both unique
    indexes and duplicate without limit — which is the pathology the
    kind split exists to prevent, so it is a constraint rather than a
    convention.
    """
    cfg = _alembic_config()
    command.upgrade(cfg, _HEAD)
    engine = sa.create_engine(migration_db)

    with engine.begin() as conn:
        track_id, ct_id = _seed_entities(conn)

    with pytest.raises(
        sa.exc.IntegrityError, match="ck_resolution_negatives_kind_candidate"
    ):
        with engine.begin() as conn:
            _ = _insert_negative(conn, kind="no_match", ct_id=ct_id, candidate=track_id)
    with pytest.raises(
        sa.exc.IntegrityError, match="ck_resolution_negatives_kind_candidate"
    ):
        with engine.begin() as conn:
            _ = _insert_negative(
                conn, kind="rejected_pair", ct_id=ct_id, candidate=None
            )

    engine.dispose()


def test_045_recorded_at_orders_events_inside_one_transaction(migration_db: str):
    """``clock_timestamp()``, not ``now()``.

    ``now()`` is the transaction's start instant, so every event a single
    transaction wrote would tie — and the suspect-streak window, which asks for
    events *after* the last success, would never truncate.
    """
    cfg = _alembic_config()
    command.upgrade(cfg, _HEAD)
    engine = sa.create_engine(migration_db)

    with engine.begin() as conn:
        track_id, ct_id = _seed_entities(conn)
        for event_type in ("accepted", "suspect"):
            conn.execute(
                sa.text(
                    "INSERT INTO resolution_events (id, user_id, event_type, "
                    "matcher_version, connector_name, connector_track_id, track_id) "
                    "VALUES (:id, 'default', :event_type, 'abc123', 'spotify', "
                    ":ct, :track)"
                ),
                {
                    "id": uuid.uuid7(),
                    "event_type": event_type,
                    "ct": ct_id,
                    "track": track_id,
                },
            )

    with engine.connect() as conn:
        stamps = [
            row.recorded_at
            for row in conn.execute(
                sa.text(
                    "SELECT recorded_at FROM resolution_events "
                    "WHERE track_id = :track ORDER BY recorded_at"
                ),
                {"track": track_id},
            )
        ]
        assert len(stamps) == 2
        assert stamps[0] < stamps[1]

    engine.dispose()
