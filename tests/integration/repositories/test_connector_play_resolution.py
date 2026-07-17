"""Integration tests for ledger resolution write-back (v0.10.0).

``bulk_update_resolution`` matches stored rows by the ledger natural key, not
entity id — re-imported duplicates keep their original stored ids while the
in-memory entities carry fresh ones, and the write-back must still land (that
is what lets a re-import heal rows whose first resolution attempt failed).
"""

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa

from src.domain.entities import ConnectorTrackPlay
from src.infrastructure.persistence.database.db_models import DBConnectorPlay
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import make_track

_PLAYED_AT = datetime(2024, 11, 5, 9, 15, 0, tzinfo=UTC)


def _scrobble(
    *,
    played_at: datetime = _PLAYED_AT,
    ms_played: int | None = None,
    track_name: str = "TEST_WriteBackTrack",
    user_id: str = "default",
) -> ConnectorTrackPlay:
    return ConnectorTrackPlay(
        service="lastfm",
        artist_name="TEST_WriteBackArtist",
        track_name=track_name,
        played_at=played_at,
        ms_played=ms_played,
        user_id=user_id,
        import_timestamp=datetime.now(UTC),
        import_source="lastfm_api",
        import_batch_id=f"TEST_BATCH_{uuid4()}",
    )


class TestBulkUpdateResolution:
    async def test_write_back_round_trip_by_natural_key(self, db_session):
        uow = get_unit_of_work(db_session)
        connector_repo = uow.get_connector_play_repository()
        track_repo = uow.get_track_repository()

        saved_track = await track_repo.save_track(
            make_track(
                title="TEST_WriteBackTrack",
                artist="TEST_WriteBackArtist",
                connector_track_identifiers={},
            )
        )

        stored_null_ms = _scrobble(ms_played=None)
        stored_with_ms = _scrobble(
            played_at=datetime(2024, 11, 5, 10, 0, 0, tzinfo=UTC), ms_played=201_000
        )
        inserted, _ = await connector_repo.bulk_insert_connector_plays([
            stored_null_ms,
            stored_with_ms,
        ])
        assert inserted == 2

        # Fresh in-memory entities with the SAME natural keys but different
        # ids — the re-import shape. Write-back must still match both rows
        # (NULL ms_played included, via IS NOT DISTINCT FROM).
        reimported_null_ms = _scrobble(ms_played=None)
        reimported_with_ms = _scrobble(
            played_at=datetime(2024, 11, 5, 10, 0, 0, tzinfo=UTC), ms_played=201_000
        )
        assert reimported_null_ms.id != stored_null_ms.id

        resolved_at = datetime.now(UTC)
        updated = await connector_repo.bulk_update_resolution(
            [
                (reimported_null_ms, saved_track.id),
                (reimported_with_ms, saved_track.id),
            ],
            resolved_at=resolved_at,
        )
        assert updated == 2

        rows = (
            await db_session.execute(
                sa.select(DBConnectorPlay).where(
                    DBConnectorPlay.connector_track_identifier
                    == stored_null_ms.connector_track_identifier
                )
            )
        ).scalars()
        for row in rows:
            assert row.resolved_track_id == saved_track.id
            assert row.resolved_at is not None

    async def test_unresolved_rows_query(self, db_session):
        """The partial-index query shape: unresolved = resolved_track_id IS NULL."""
        uow = get_unit_of_work(db_session)
        connector_repo = uow.get_connector_play_repository()
        track_repo = uow.get_track_repository()

        saved_track = await track_repo.save_track(
            make_track(
                title="TEST_UnresolvedTrack",
                artist="TEST_WriteBackArtist",
                connector_track_identifiers={},
            )
        )

        resolved_play = _scrobble(track_name="TEST_UnresolvedTrack_resolved")
        unresolved_play = _scrobble(track_name="TEST_UnresolvedTrack_pending")
        _ = await connector_repo.bulk_insert_connector_plays([
            resolved_play,
            unresolved_play,
        ])
        _ = await connector_repo.bulk_update_resolution(
            [(resolved_play, saved_track.id)], resolved_at=datetime.now(UTC)
        )

        unresolved = (
            (
                await db_session.execute(
                    sa.select(DBConnectorPlay.connector_track_identifier).where(
                        DBConnectorPlay.connector_name == "lastfm",
                        DBConnectorPlay.resolved_track_id.is_(None),
                        DBConnectorPlay.connector_track_identifier.ilike(
                            "%test_unresolvedtrack%"
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert unresolved == [unresolved_play.connector_track_identifier]

    async def test_ledger_rows_carry_entity_id_and_user_id(self, db_session):
        """Tenancy: rows persist the entity's user_id and id, not column defaults."""
        uow = get_unit_of_work(db_session)
        connector_repo = uow.get_connector_play_repository()

        play = _scrobble(track_name="TEST_TenancyTrack", user_id="TEST_user_x")
        inserted, _ = await connector_repo.bulk_insert_connector_plays([play])
        assert inserted == 1

        row = (
            await db_session.execute(
                sa.select(DBConnectorPlay).where(DBConnectorPlay.id == play.id)
            )
        ).scalar_one()
        assert row.user_id == "TEST_user_x"
