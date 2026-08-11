"""End-to-end test for migration 051 (track_mappings FK RESTRICT).

Everything 051 changes is a referential action, which ``metadata.create_all``
would happily reproduce from ``db_models.py`` without ever proving the DDL runs
— so this drives the real chain against a throwaway container instead, the same
way 044/050 do. The behaviour under test is what the database does when a
*delete* arrives from outside the repository layer: the v0.10.3 guard in
``hard_delete_track`` is invisible to a Core ``DELETE``, and a Core ``DELETE``
is exactly what took production's mapping history.

The subtle arm is the self-FK. Deferrability has to survive the flip, because
the write path stamps a predecessor with a successor id before that successor
exists — so the test asserts both halves: the *delete* action is now refused,
while the *insert* check is still deferred to commit. They are different
triggers, and a migration that traded one for the other would look correct in
``pg_constraint`` and break the hot write path.

Not covered here: the production data this closes over (5 supersession events
against 4 surviving mappings) is not repaired by the migration, only prevented
from recurring — there is no way to reconstruct a deleted row. Nor is the lock
profile of the FK rebuild exercised; at ~84k rows the re-validation scan is
milliseconds, but this container holds a handful of rows and would not notice a
regression there.

Marked ``slow``: spins a dedicated container and runs the chain to 050 first.
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
_PRE = "050_play_exclusion_reason"
_HEAD = "051_track_mappings_fk_restrict"

_NOW = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)
_USER = "default"

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


def _insert_track(conn: sa.Connection, tid: uuid.UUID, title: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO tracks (id, user_id, title, artists, version, "
            "created_at, updated_at) "
            "VALUES (:id, :user, :title, CAST(:artists AS JSONB), 1, :now, :now)"
        ),
        {
            "id": tid,
            "user": _USER,
            "title": title,
            "artists": json.dumps({"names": ["Someone"]}),
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
) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO track_mappings (id, user_id, track_id, connector_track_id, "
            "connector_name, match_method, confidence, origin, is_primary, "
            "created_at, updated_at) "
            "VALUES (:id, :user, :track_id, :ct_id, 'spotify', 'direct', 100, "
            "'automatic', :is_primary, :now, :now)"
        ),
        {
            "id": mid,
            "user": _USER,
            "track_id": track_id,
            "ct_id": connector_track_id,
            "is_primary": is_primary,
            "now": _NOW,
        },
    )


def _retire(conn: sa.Connection, mapping_id: uuid.UUID, successor: uuid.UUID) -> None:
    """Stamp a predecessor with its successor id — deferred until commit."""
    conn.execute(
        sa.text(
            "UPDATE track_mappings SET superseded_at = :now, "
            "supersession_reason = 'rematch', superseded_by_id = :successor, "
            "is_primary = FALSE WHERE id = :old"
        ),
        {"now": _NOW, "successor": successor, "old": mapping_id},
    )


def _delete_actions(engine: sa.Engine) -> dict[str, str]:
    with engine.connect() as conn:
        return {
            row.conname: row.confdeltype
            for row in conn.execute(
                sa.text(
                    "SELECT conname, confdeltype FROM pg_constraint "
                    "WHERE contype = 'f' "
                    "AND conrelid = 'track_mappings'::regclass"
                )
            )
        }


def test_051_refuses_deletes_that_would_erase_mapping_history(migration_db):
    cfg = _alembic_config()
    command.upgrade(cfg, _PRE)

    engine = sa.create_engine(migration_db)

    mapped_track, bare_track = uuid.uuid7(), uuid.uuid7()
    live_ct, chain_ct = uuid.uuid7(), uuid.uuid7()
    live_mapping = uuid.uuid7()
    predecessor, successor = uuid.uuid7(), uuid.uuid7()

    with engine.begin() as conn:
        _insert_track(conn, mapped_track, "Mapped")
        _insert_track(conn, bare_track, "Bare")
        _insert_connector_track(conn, live_ct, "sp_live")
        _insert_connector_track(conn, chain_ct, "sp_chain")
        _insert_mapping(conn, live_mapping, mapped_track, live_ct)
        # A completed supersession chain, written the way the application
        # writes one: retire the incumbent first, then insert the successor.
        # Both orderings matter — retiring first is what keeps
        # ``uq_track_mappings_live_connector`` satisfied, and neither row may
        # be primary because ``uq_primary_mapping`` already belongs to the
        # live mapping above.
        _insert_mapping(conn, predecessor, mapped_track, chain_ct, is_primary=False)
        _retire(conn, predecessor, successor)
        _insert_mapping(conn, successor, mapped_track, chain_ct, is_primary=False)

    command.upgrade(cfg, _HEAD)

    assert set(_delete_actions(engine).values()) == {"r"}

    # The finding itself: a Core DELETE never calls ``hard_delete_track``, so
    # only the constraint can stop it taking the mappings with it.
    with pytest.raises(
        sa.exc.IntegrityError, match="fk_track_mappings_track_id_tracks"
    ):
        with engine.begin() as conn:
            conn.execute(
                sa.text("DELETE FROM tracks WHERE id = :id"), {"id": mapped_track}
            )

    # ...and the refusal is scoped to tracks that actually hold history. A
    # RESTRICT that blocked every delete would be a different bug.
    with engine.begin() as conn:
        deleted = conn.execute(
            sa.text("DELETE FROM tracks WHERE id = :id"), {"id": bare_track}
        ).rowcount
    assert deleted == 1

    # Latent today — nothing evicts the shared connector-track cache — but the
    # first thing that does must not be able to delete user history sideways.
    with pytest.raises(
        sa.exc.IntegrityError,
        match="fk_track_mappings_connector_track_id_connector_tracks",
    ):
        with engine.begin() as conn:
            conn.execute(
                sa.text("DELETE FROM connector_tracks WHERE id = :id"), {"id": live_ct}
            )

    # The subtle arm: under SET NULL this deleted the successor and silently
    # blanked the predecessor's pointer, leaving a retired row with a reason
    # and no successor — a shape the coherence CHECK permits, because a
    # retirement with no replacement is legitimate. Nothing downstream could
    # tell the two apart afterwards.
    with pytest.raises(
        sa.exc.IntegrityError, match="fk_track_mappings_superseded_by_id_track_mappings"
    ):
        with engine.begin() as conn:
            conn.execute(
                sa.text("DELETE FROM track_mappings WHERE id = :id"), {"id": successor}
            )

    with engine.connect() as conn:
        still_pointing = conn.execute(
            sa.text("SELECT superseded_by_id FROM track_mappings WHERE id = :id"),
            {"id": predecessor},
        ).scalar_one()
    assert still_pointing == successor

    # Deferrability is a different trigger from the delete action, and the hot
    # write path depends on it: the predecessor is stamped with an id the
    # successor INSERT has not written yet. RESTRICT must not have cost this.
    late_successor = uuid.uuid7()
    with engine.begin() as conn:
        _retire(conn, live_mapping, late_successor)
        _insert_mapping(conn, late_successor, mapped_track, live_ct, is_primary=False)

    with engine.connect() as conn:
        assert (
            conn.execute(
                sa.text("SELECT superseded_by_id FROM track_mappings WHERE id = :id"),
                {"id": live_mapping},
            ).scalar_one()
            == late_successor
        )

    # Downgrade restores 044's actions — and with them the cascade, which is
    # the whole reason downgrading past this revision re-opens the hole.
    command.downgrade(cfg, _PRE)
    assert _delete_actions(engine) == {
        "fk_track_mappings_track_id_tracks": "c",
        "fk_track_mappings_connector_track_id_connector_tracks": "c",
        "fk_track_mappings_superseded_by_id_track_mappings": "n",
    }

    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM tracks WHERE id = :id"), {"id": mapped_track})
    with engine.connect() as conn:
        assert (
            conn.execute(sa.text("SELECT count(*) FROM track_mappings")).scalar() == 0
        )

    command.upgrade(cfg, _HEAD)
    assert set(_delete_actions(engine).values()) == {"r"}

    engine.dispose()
