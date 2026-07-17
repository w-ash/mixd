"""Critical integration test for import idempotency.

Tests repository layer behavior with real database operations to ensure:
1. Imports are idempotent (can be run multiple times safely)
2. Bulk upsert works efficiently with proper unique constraints
3. No duplicate plays are created under any import scenario

This test validates the critical data integrity constraint that prevents
duplicate plays from corrupting the user's music history data.
"""

from datetime import UTC, datetime
from uuid import uuid4

from src.domain.entities import ConnectorTrackPlay, TrackPlay
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import make_track


class TestImportIdempotency:
    """Integration tests for import idempotency with real database operations."""

    async def test_duplicate_import_creates_no_duplicates(self, db_session):
        """CRITICAL: Test that importing the same play twice doesn't create duplicates."""
        uow = get_unit_of_work(db_session)
        plays_repo = uow.get_plays_repository()
        track_repo = uow.get_track_repository()

        test_track = make_track(
            title="TEST_IdempotencyTrack",
            artist="TEST_IdempotencyArtist",
            connector_track_identifiers={},
        )
        saved_track = await track_repo.save_track(test_track)

        batch_id = f"TEST_BATCH_{uuid4()}"

        test_play = TrackPlay(
            track_id=saved_track.id,
            service="spotify",
            played_at=datetime(2023, 1, 15, 14, 30, 22, tzinfo=UTC),
            ms_played=180000,
            context={"test": "data"},
            import_timestamp=datetime.now(UTC),
            import_source="test_import",
            import_batch_id=batch_id,
        )

        await plays_repo.bulk_insert_plays([test_play])
        await plays_repo.bulk_insert_plays([test_play])

        all_plays = await plays_repo.find_plays_in_time_range(
            [saved_track.id],
            datetime(2023, 1, 15, tzinfo=UTC),
            datetime(2023, 1, 16, tzinfo=UTC),
            user_id="default",
        )

        assert len(all_plays) == 1, (
            f"Expected 1 play, got {len(all_plays)}. Import is NOT idempotent!"
        )

        play = all_plays[0]
        assert play.track_id == saved_track.id
        assert play.service == "spotify"
        assert play.ms_played == 180000

    async def test_overlapping_batch_imports_prevent_duplicates(self, db_session):
        """Test that overlapping imports with different batch IDs don't create duplicates."""
        uow = get_unit_of_work(db_session)
        plays_repo = uow.get_plays_repository()
        track_repo = uow.get_track_repository()

        test_track = make_track(
            title="TEST_OverlapTrack",
            artist="TEST_OverlapArtist",
            connector_track_identifiers={},
        )
        saved_track = await track_repo.save_track(test_track)

        batch_1 = f"TEST_BATCH_{uuid4()}"
        batch_2 = f"TEST_BATCH_{uuid4()}"

        play_1 = TrackPlay(
            track_id=saved_track.id,
            service="lastfm",
            played_at=datetime(2023, 2, 10, 15, 45, 30, tzinfo=UTC),
            ms_played=240000,
            context={"batch": "first"},
            import_timestamp=datetime.now(UTC),
            import_source="lastfm_api",
            import_batch_id=batch_1,
        )

        # Same (track, service, played_at) as play_1 — only batch_id and context differ.
        play_2 = TrackPlay(
            track_id=saved_track.id,
            service="lastfm",
            played_at=datetime(2023, 2, 10, 15, 45, 30, tzinfo=UTC),
            ms_played=240000,
            context={"batch": "second"},
            import_timestamp=datetime.now(UTC),
            import_source="lastfm_api",
            import_batch_id=batch_2,
        )

        await plays_repo.bulk_insert_plays([play_1])

        await plays_repo.bulk_insert_plays([play_2])

        all_plays = await plays_repo.find_plays_in_time_range(
            [saved_track.id],
            datetime(2023, 2, 10, tzinfo=UTC),
            datetime(2023, 2, 11, tzinfo=UTC),
            user_id="default",
        )

        # ON CONFLICT DO NOTHING: the first batch's insert claims the row; the
        # second batch is a no-op, so only batch_1's play exists.
        assert len(all_plays) == 1
        assert all_plays[0].import_batch_id == batch_1


class TestNullMsPlayedIdempotency:
    """NULLS NOT DISTINCT (migration 040): NULL ms_played rows must still collide.

    Last.fm rows always carry ``ms_played=None``; before migration 040 the
    dedup constraints treated NULL ≠ NULL, so a full re-import would duplicate
    every scrobble (convergence findings §5c).
    """

    async def test_null_ms_played_track_play_reinsert_is_deduplicated(self, db_session):
        uow = get_unit_of_work(db_session)
        plays_repo = uow.get_plays_repository()
        track_repo = uow.get_track_repository()

        saved_track = await track_repo.save_track(
            make_track(
                title="TEST_NullMsTrack",
                artist="TEST_NullMsArtist",
                connector_track_identifiers={},
            )
        )
        played_at = datetime(2024, 11, 5, 9, 15, 0, tzinfo=UTC)

        def scrobble(batch: str) -> TrackPlay:
            return TrackPlay(
                track_id=saved_track.id,
                service="lastfm",
                played_at=played_at,
                ms_played=None,
                import_timestamp=datetime.now(UTC),
                import_source="lastfm_api",
                import_batch_id=batch,
            )

        await plays_repo.bulk_insert_plays([scrobble("TEST_BATCH_1")])
        await plays_repo.bulk_insert_plays([scrobble("TEST_BATCH_2")])

        all_plays = await plays_repo.find_plays_in_time_range(
            [saved_track.id],
            datetime(2024, 11, 5, tzinfo=UTC),
            datetime(2024, 11, 6, tzinfo=UTC),
            user_id="default",
        )
        assert len(all_plays) == 1

        # NULL vs a concrete ms_played remains a distinct observation.
        richer = TrackPlay(
            track_id=saved_track.id,
            service="lastfm",
            played_at=played_at,
            ms_played=201_000,
            import_timestamp=datetime.now(UTC),
            import_source="lastfm_api",
            import_batch_id="TEST_BATCH_3",
        )
        await plays_repo.bulk_insert_plays([richer])
        all_plays = await plays_repo.find_plays_in_time_range(
            [saved_track.id],
            datetime(2024, 11, 5, tzinfo=UTC),
            datetime(2024, 11, 6, tzinfo=UTC),
            user_id="default",
        )
        assert len(all_plays) == 2

    async def test_null_ms_played_connector_play_reinsert_is_deduplicated(
        self, db_session
    ):
        uow = get_unit_of_work(db_session)
        connector_repo = uow.get_connector_play_repository()

        def scrobble(batch: str) -> ConnectorTrackPlay:
            return ConnectorTrackPlay(
                service="lastfm",
                artist_name="TEST_NullMsArtist",
                track_name="TEST_NullMsLedger",
                played_at=datetime(2024, 11, 5, 9, 15, 0, tzinfo=UTC),
                ms_played=None,
                import_timestamp=datetime.now(UTC),
                import_source="lastfm_api",
                import_batch_id=batch,
            )

        inserted, duplicates = await connector_repo.bulk_insert_connector_plays([
            scrobble("TEST_BATCH_1")
        ])
        assert (inserted, duplicates) == (1, 0)

        inserted, duplicates = await connector_repo.bulk_insert_connector_plays([
            scrobble("TEST_BATCH_2")
        ])
        assert (inserted, duplicates) == (0, 1)
