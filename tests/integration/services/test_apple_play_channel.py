"""End-to-end integration of the ``apple:plays`` sync target (v0.11.x).

``run_sync_target("apple:plays", ...)`` must reach the real pipeline: the
registry mints the Apple recently-played importer, the two-phase orchestrator
writes ledger rows on the ``("apple", "apple_api")`` channel, and the
checkpoint (fingerprint + poll time) becomes visible through
``get_all_checkpoint_statuses``. Only the HTTP edge is faked — the client's
``get_recently_played`` is patched class-level; everything below it is real.

The canonical track is pre-mapped to the Apple catalog id so resolution stays
a database lookup (no storefront/catalog HTTP, no Apple credentials).

Writes go through the process-global engine (``run_sync_target`` and
``run_import`` manage their own sessions and commit), so the test cleans up
its own user's rows instead of relying on savepoint rollback.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.sync_target_runner import run_sync_target
from src.application.use_cases.sync_likes import get_all_checkpoint_statuses
from src.infrastructure.connectors.apple_music.client import AppleMusicAPIClient
from src.infrastructure.connectors.apple_music.models import (
    AppleMusicRecentlyPlayedResponse,
    AppleMusicSong,
    AppleMusicSongAttributes,
)
from src.infrastructure.persistence.database.db_connection import get_engine
from src.infrastructure.persistence.database.db_models import (
    DBConnectorPlay,
    DBTrackPlay,
    metadata,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import make_track

pytestmark = pytest.mark.slow

_SONG_ID = "1440857781"


def _song(song_id: str = _SONG_ID) -> AppleMusicSong:
    return AppleMusicSong(
        id=song_id,
        attributes=AppleMusicSongAttributes(
            name="Achilles Last Stand",
            artist_name="Led Zeppelin",
            album_name="Presence",
            duration_in_millis=625_000,
            isrc="GBALB7600123",
        ),
    )


def _page(*songs: AppleMusicSong) -> AppleMusicRecentlyPlayedResponse:
    return AppleMusicRecentlyPlayedResponse(data=list(songs), next=None)


async def _seed_mapped_track(user_id: str) -> None:
    """Persist (and commit) a canonical track pre-mapped to the Apple id.

    A committed write on the global engine — the sync target's runs open
    their own sessions and would never see rows pending in a test savepoint.
    """
    async with AsyncSession(get_engine(), expire_on_commit=False) as session:
        uow = get_unit_of_work(session)
        track = await uow.get_track_repository().save_track(
            make_track(
                title="Achilles Last Stand",
                artist="Led Zeppelin",
                user_id=user_id,
                duration_ms=625_000,
                connector_track_identifiers={},
            )
        )
        _ = await uow.get_connector_repository().map_track_to_connector(
            track, "apple", _SONG_ID, "direct_import", 100
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


async def _ledger_rows(user_id: str) -> list[DBConnectorPlay]:
    async with AsyncSession(get_engine()) as session:
        return list(
            (
                await session.execute(
                    sa.select(DBConnectorPlay).where(DBConnectorPlay.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )


class TestApplePlaysSyncTarget:
    async def test_two_polls_land_ledger_rows_and_a_visible_checkpoint(
        self, db_session
    ):
        # db_session is requested for its side effect: it boots the
        # testcontainers database and points the process-global engine at it,
        # which the sync target's own sessions ride on.
        _ = db_session
        user_id = f"TEST_apple_{uuid4().hex[:8]}"
        await _seed_mapped_track(user_id)
        try:
            recently_played = AsyncMock(
                side_effect=[
                    _page(),  # first poll: empty window, seeds the fingerprint
                    _page(_song()),  # second poll: one new play at the head
                ]
            )
            aclose_spy = AsyncMock()
            with (
                patch.object(
                    AppleMusicAPIClient, "get_recently_played", recently_played
                ),
                patch.object(AppleMusicAPIClient, "aclose", aclose_spy),
            ):
                before = datetime.now(UTC)
                first = await run_sync_target(
                    user_id, "apple:plays", initiated_by="test"
                )
                second = await run_sync_target(
                    user_id, "apple:plays", initiated_by="test"
                )
                after = datetime.now(UTC)

            assert first.status == "completed"
            assert second.status == "completed"
            # No leaked httpx2 pools: each run closes its importer's client,
            # and the second run (which reaches Phase 2) closes the
            # resolver's client too.
            assert aclose_spy.await_count == 3

            rows = await _ledger_rows(user_id)
            assert len(rows) == 1
            row = rows[0]
            assert (row.connector_name, row.import_source) == ("apple", "apple_api")
            assert row.connector_track_identifier == _SONG_ID
            assert row.ms_played is None
            # Midpoint honesty: the stamp sits inside the poll interval.
            assert before <= row.played_at.replace(tzinfo=UTC) <= after
            # Resolution ran through the pre-seeded mapping.
            assert row.resolved_track_id is not None

            # The projection turned the observation into one canonical play.
            async with AsyncSession(get_engine()) as session:
                plays = list(
                    (
                        await session.execute(
                            sa.select(DBTrackPlay).where(DBTrackPlay.user_id == user_id)
                        )
                    )
                    .scalars()
                    .all()
                )
            assert len(plays) == 1
            assert plays[0].service == "apple"

            statuses = await get_all_checkpoint_statuses(user_id)
            apple = next(
                s for s in statuses if (s.service, s.entity_type) == ("apple", "plays")
            )
            assert apple.has_previous_sync is True
        finally:
            await _delete_user_rows(user_id)
