"""Re-importing the same Last.fm span must add nothing (v0.10.2.7).

The Last.fm sibling of ``test_reimport_zero_delta.py``, run through the real
resolver: the first import creates canonicals via the batch-first plan/persist
path (one ``save_tracks`` + one ``map_tracks_to_connectors`` against a real
database), the second resolves the same identifiers through the mapping-lookup
fast path — no API enrichment, no new rows anywhere. A regression here either
inflates history silently or re-pays the enrichment cost on every re-run.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import sqlalchemy as sa

from src.application.services.play_projection_service import PlayProjectionService
from src.domain.entities import ConnectorTrackPlay
from src.infrastructure.connectors.lastfm.play_resolver import (
    LastfmConnectorPlayResolver,
)
from src.infrastructure.persistence.database.db_models import (
    DBConnectorPlay,
    DBPlaySource,
    DBTrack,
    DBTrackMapping,
    DBTrackPlay,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work

_FIRST = datetime(2024, 3, 9, 21, 4, 0, tzinfo=UTC)


def _scrobble_rows(user_id: str) -> list[ConnectorTrackPlay]:
    """One fetch of the span: two scrobbles of one track, fresh entity ids."""
    batch_id = f"TEST_{uuid4()}"
    return [
        ConnectorTrackPlay(
            service="lastfm",
            artist_name="carwash",
            track_name="striptease",
            played_at=_FIRST + offset,
            ms_played=None,
            user_id=user_id,
            service_metadata={"loved": False},
            import_timestamp=datetime.now(UTC),
            import_source="lastfm_api",
            import_batch_id=batch_id,
        )
        for offset in (timedelta(0), timedelta(days=1))
    ]


def _mock_client() -> AsyncMock:
    """LastFMAPIClient double: getInfo answers with corrected display names."""
    client = AsyncMock()
    client.get_track_info_comprehensive.return_value = MagicMock(
        lastfm_url="https://www.last.fm/music/Carwash/_/Striptease",
        lastfm_duration=201000,
        lastfm_album_name="Shimmer",
        lastfm_mbid=None,
        lastfm_artist_name="Carwash",
        lastfm_title="Striptease",
    )
    return client


async def _import_cycle(
    uow, user_id: str, rows: list[ConnectorTrackPlay], client: AsyncMock
) -> tuple[int, int, dict[str, int]]:
    """Ingest → resolve (real resolver) → project: a real Last.fm play import."""
    connector_repo = uow.get_connector_play_repository()
    inserted, duplicates = await connector_repo.bulk_insert_connector_plays(rows)

    resolver = LastfmConnectorPlayResolver(lastfm_client=client)
    outcome = await resolver.resolve_connector_plays(rows, uow, user_id=user_id)
    assert outcome.metrics["error_count"] == 0
    _ = await connector_repo.bulk_update_resolution(
        list(outcome.resolutions), resolved_at=datetime.now(UTC)
    )

    stats = await PlayProjectionService().project_observed_days(
        uow, user_id=user_id, played_at=[row.played_at for row in rows]
    )
    return inserted, duplicates, stats


async def _state(db_session, user_id: str):
    """(ledger count, track ids, mapping ids, play ids, source ids) per user."""

    async def _ids(model):
        return set(
            (
                await db_session.execute(
                    sa.select(model.id).where(model.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )

    ledger = (
        await db_session.execute(
            sa
            .select(sa.func.count())
            .select_from(DBConnectorPlay)
            .where(DBConnectorPlay.user_id == user_id)
        )
    ).scalar_one()
    return (
        ledger,
        await _ids(DBTrack),
        await _ids(DBTrackMapping),
        await _ids(DBTrackPlay),
        await _ids(DBPlaySource),
    )


class TestLastfmReimportDelta:
    """First import creates through the bulk path; re-import is zero-delta."""

    async def test_reimport_same_span_is_zero_delta(self, db_session):
        user_id = f"TEST_lastfm_reimport_{uuid4().hex[:8]}"
        uow = get_unit_of_work(db_session)

        first_client = _mock_client()
        inserted, duplicates, stats = await _import_cycle(
            uow, user_id, _scrobble_rows(user_id), first_client
        )
        assert (inserted, duplicates) == (2, 0)
        assert stats["groups_created"] == 2
        # Creation ran the batched enrichment path: one identifier, one probe.
        first_client.get_track_info_comprehensive.assert_awaited_once()

        before = await _state(db_session, user_id)
        ledger_before, tracks_before, mappings_before, plays_before, _ = before
        assert ledger_before == 2
        assert len(tracks_before) == 1  # one canonical for both scrobbles
        assert len(plays_before) == 2

        # Second fetch of the same span: same natural keys, new entity ids.
        second_client = _mock_client()
        inserted, duplicates, stats = await _import_cycle(
            uow, user_id, _scrobble_rows(user_id), second_client
        )

        # Every re-imported row is recognised at the ledger...
        assert inserted == 0
        assert duplicates == 2
        # ...the identifier resolves through the mapping-lookup fast path,
        # never re-running enrichment...
        second_client.get_track_info_comprehensive.assert_not_awaited()
        second_client.get_track_correction.assert_not_awaited()
        # ...and re-projecting the unchanged ledger creates nothing.
        assert stats["groups_created"] == 0
        assert stats["groups_unchanged"] == 2

        assert await _state(db_session, user_id) == before
