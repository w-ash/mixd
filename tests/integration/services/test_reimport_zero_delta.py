"""Re-importing the same source file must add nothing (v0.10.3 audit).

Users re-run a GDPR export import — after a crash, to pick up a corrected
resolution, or simply because they forgot they already had. The guarantee is
that the second run is a no-op end to end: the ledger's
``uq_connector_plays_deduplication`` constraint absorbs every row on the way in,
and the projection that follows finds nothing new to create. A regression here
inflates listening history silently, which no downstream count can un-inflate.

The re-imported entities are rebuilt from scratch, so they carry fresh ``id``s
and a fresh ``import_batch_id`` while sharing the natural key — exactly what a
real second parse of the same file produces.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa

from src.application.services.play_projection_service import PlayProjectionService
from src.domain.entities import ConnectorTrackPlay
from src.infrastructure.persistence.database.db_models import (
    DBConnectorPlay,
    DBPlaySource,
    DBTrackPlay,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import make_track

_FIRST = datetime(2024, 3, 9, 21, 4, 0, tzinfo=UTC)
_MS = 187_000
_WINDOW = (_FIRST - timedelta(days=1), _FIRST + timedelta(days=2))


def _export_rows(user_id: str) -> list[ConnectorTrackPlay]:
    """One parse of the export: two listens of the same track, fresh entity ids."""
    batch_id = f"TEST_{uuid4()}"
    return [
        ConnectorTrackPlay(
            service="spotify",
            artist_name="Robert Johnson",
            track_name="Come On in My Kitchen",
            played_at=_FIRST + offset,
            ms_played=_MS,
            user_id=user_id,
            service_metadata={
                "track_uri": "spotify:track:1PmMcnr8f5xGeBmBOtc0Ow",
                "platform": "ios",
            },
            import_timestamp=datetime.now(UTC),
            import_source="spotify_export",
            import_batch_id=batch_id,
        )
        for offset in (timedelta(0), timedelta(days=1))
    ]


async def _import_cycle(
    uow, user_id: str, rows: list[ConnectorTrackPlay], track_id: UUID
) -> tuple[int, int, dict[str, int]]:
    """Ingest → resolve → project, the three phases of a real play import."""
    connector_repo = uow.get_connector_play_repository()
    inserted, duplicates = await connector_repo.bulk_insert_connector_plays(rows)
    _ = await connector_repo.bulk_update_resolution(
        [(row, track_id) for row in rows], resolved_at=datetime.now(UTC)
    )
    stats = await PlayProjectionService().project_range(
        uow, user_id=user_id, start=_WINDOW[0], end=_WINDOW[1]
    )
    return inserted, duplicates, stats


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


class TestReimportDelta:
    """The audit's re-import delta check, as an executable guarantee."""

    async def test_reimport_same_source_is_zero_delta(self, db_session):
        user_id = f"TEST_reimport_{uuid4().hex[:8]}"
        uow = get_unit_of_work(db_session)
        track = await uow.get_track_repository().save_track(
            make_track(
                title="Come On in My Kitchen",
                artist="Robert Johnson",
                user_id=user_id,
                connector_track_identifiers={},
            )
        )

        first_rows = _export_rows(user_id)
        inserted, duplicates, stats = await _import_cycle(
            uow, user_id, first_rows, track.id
        )
        assert (inserted, duplicates) == (2, 0)
        assert stats["groups_created"] == 2

        ledger_before, plays_before, sources_before = await _state(db_session, user_id)
        assert ledger_before == 2
        assert len(plays_before) == 2

        # Second parse of the same file: same natural keys, new entity ids.
        second_rows = _export_rows(user_id)
        assert {row.id for row in second_rows} & {row.id for row in first_rows} == set()

        inserted, duplicates, stats = await _import_cycle(
            uow, user_id, second_rows, track.id
        )

        # Every re-imported row is recognised — the duplicate count is the batch.
        assert inserted == 0
        assert duplicates == len(second_rows)
        # And re-projecting the unchanged ledger creates no canonical plays.
        assert stats["groups_created"] == 0
        assert stats["groups_unchanged"] == 2

        ledger_after, plays_after, sources_after = await _state(db_session, user_id)
        assert ledger_after == ledger_before
        assert plays_after == plays_before
        assert sources_after == sources_before
