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

from src.domain.entities import TrackPlay
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

        all_plays = await plays_repo.get_plays_by_batch(batch_id)

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

        all_plays_batch_1 = await plays_repo.get_plays_by_batch(batch_1)
        all_plays_batch_2 = await plays_repo.get_plays_by_batch(batch_2)

        # ON CONFLICT DO NOTHING: the first batch's insert claims the row; the
        # second batch is a no-op, so its batch_id returns zero plays.
        assert len(all_plays_batch_1) == 1
        assert len(all_plays_batch_2) == 0
