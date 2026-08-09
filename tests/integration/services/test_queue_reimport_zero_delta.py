"""Re-queueing an already-imported file through the import queue is zero-delta.

The v0.10.2.3 guarantee — a second import of the same source adds nothing —
was pinned for the direct pipeline in ``test_reimport_zero_delta.py``. The
v0.10.2.6 queue is a new road into that same pipeline (``start_queue`` →
``launch_sse_operation`` → ``run_import``), and "re-queueing anything the user
is unsure about costs them nothing" is a design decision of the queue itself,
so the guarantee is re-asserted through the queue path end to end: real
launcher, real audit rows, real ledger + projection.

The canonical track is pre-mapped to the export's Spotify id so resolution
stays a database lookup — no live connector credentials exist here, and the
guarantee under test is about the ledger and projection, not about matching.

Writes go through the process-global engine (the queue's runs manage their own
sessions and commit), so the test cleans up its own user's rows instead of
relying on savepoint rollback.
"""

import asyncio
import json
from pathlib import Path
import time
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.constants import SSEConstants
from src.infrastructure.persistence.database.db_connection import get_engine
from src.infrastructure.persistence.database.db_models import (
    DBConnectorPlay,
    DBPlaySource,
    DBTrackPlay,
    metadata,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from src.interface.api.services import import_queue
from src.interface.api.services.background import _background_tasks
from src.interface.api.services.import_queue import QueueEntry, start_queue
from tests.fixtures import make_track

_SPOTIFY_ID = "2374M0fQpWi3dLnB54qaLX"
_TRACK_URI = f"spotify:track:{_SPOTIFY_ID}"
_EXPORT_TIMESTAMPS = ("2024-04-02T19:21:07Z", "2024-04-03T19:21:07Z")


def _write_export(directory: Path, name: str) -> Path:
    """A minimal but real Spotify GDPR export file (two listens, a day apart)."""
    file_path = directory / name
    _ = file_path.write_text(
        json.dumps([
            {
                "ts": ts,
                "spotify_track_uri": _TRACK_URI,
                "master_metadata_track_name": "Achilles Last Stand",
                "master_metadata_album_artist_name": "Led Zeppelin",
                "master_metadata_album_album_name": "Presence",
                "ms_played": 214_000,
                "platform": "ios",
                "conn_country": "GB",
                "reason_start": "trackdone",
                "reason_end": "trackdone",
                "shuffle": False,
                "skipped": False,
                "offline": False,
                "incognito_mode": False,
            }
            for ts in _EXPORT_TIMESTAMPS
        ]),
        encoding="utf-8",
    )
    return file_path


async def _seed_mapped_track(user_id: str) -> None:
    """Persist (and commit) a canonical track pre-mapped to the export's id.

    A committed write on the global engine — the queue's runs open their own
    sessions and would never see rows pending inside a test savepoint.
    """
    async with AsyncSession(get_engine(), expire_on_commit=False) as session:
        uow = get_unit_of_work(session)
        track = await uow.get_track_repository().save_track(
            make_track(
                title="Achilles Last Stand",
                artist="Led Zeppelin",
                user_id=user_id,
                # The play resolver's duration filter needs the canonical
                # duration; without it every play is skipped as unverifiable.
                duration_ms=214_000,
                connector_track_identifiers={},
            )
        )
        _ = await uow.get_connector_repository().map_track_to_connector(
            track, "spotify", _SPOTIFY_ID, "direct_import", 100
        )
        await session.commit()


async def _delete_user_rows(user_id: str) -> None:
    """Best-effort removal of every committed row this test's user produced."""
    async with AsyncSession(get_engine()) as session:
        for table in reversed(metadata.sorted_tables):
            if "user_id" in table.columns:
                _ = await session.execute(
                    table.delete().where(table.c.user_id == user_id)
                )
        await session.commit()


async def _drain_queue_pass(user_id: str, tmpdir: Path, export: Path) -> None:
    """One POST-equivalent: start the queue with one entry, wait until drained."""
    queue = start_queue(
        user_id=user_id,
        tmpdir=tmpdir,
        entries=[QueueEntry(filename=export.name, position=0, path=export)],
    )
    deadline = time.monotonic() + 60
    while not queue.is_drained:
        if time.monotonic() > deadline:
            raise AssertionError("queue pass did not drain within the timeout")
        await asyncio.sleep(0.05)
    while _background_tasks:
        if time.monotonic() > deadline:
            raise AssertionError("queue tasks did not settle within the timeout")
        await asyncio.sleep(0.02)
    assert [e.status for e in queue.entries] == ["complete"]


async def _state(db_session, user_id: str):
    """(ledger row count, canonical play ids, play-source ids) for one user."""
    ledger = (
        await db_session.execute(
            sa
            .select(sa.func.count())
            .select_from(DBConnectorPlay)
            .where(DBConnectorPlay.user_id == user_id)
        )
    ).scalar_one()
    plays = (
        (
            await db_session.execute(
                sa.select(DBTrackPlay.id).where(DBTrackPlay.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    sources = (
        (
            await db_session.execute(
                sa.select(DBPlaySource.id).where(DBPlaySource.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    return ledger, set(plays), set(sources)


class TestQueueReimportZeroDelta:
    @pytest.fixture(autouse=True)
    def _queue_environment(self, monkeypatch):
        """Zero the SSE grace window (each pass would otherwise idle 30s per
        file) and leave no queue registration or slot token behind."""
        monkeypatch.setattr(SSEConstants, "GRACE_PERIOD_SECONDS", 0)
        yield
        for queue in list(import_queue._queues.values()):
            import_queue.release_operation_slot(
                import_queue._queue_slot_token(queue.queue_id)
            )
        import_queue._queues.clear()

    async def test_requeueing_the_same_file_is_zero_delta(self, db_session, tmp_path):
        user_id = f"TEST_queue_reimport_{uuid4().hex[:8]}"
        await _seed_mapped_track(user_id)
        try:
            first_dir = tmp_path / "pass-1"
            first_dir.mkdir()
            await _drain_queue_pass(
                user_id, first_dir, _write_export(first_dir, "000.json")
            )

            ledger_first, plays_first, sources_first = await _state(db_session, user_id)
            assert ledger_first == 2
            assert len(plays_first) == 2

            # The user re-queues the same export file, uncertain what landed.
            second_dir = tmp_path / "pass-2"
            second_dir.mkdir()
            await _drain_queue_pass(
                user_id, second_dir, _write_export(second_dir, "000.json")
            )

            ledger_second, plays_second, sources_second = await _state(
                db_session, user_id
            )
            assert ledger_second == ledger_first
            assert plays_second == plays_first
            assert sources_second == sources_first
        finally:
            await _delete_user_rows(user_id)
