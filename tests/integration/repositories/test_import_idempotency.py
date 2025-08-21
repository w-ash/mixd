"""Critical integration test for import idempotency.

Tests repository layer behavior with real database operations to ensure:
1. Imports are idempotent (can be run multiple times safely)
2. Bulk upsert works efficiently with proper unique constraints
3. No duplicate plays are created under any import scenario

This test validates the critical data integrity constraint that prevents
duplicate plays from corrupting the user's music history data.
"""

from datetime import UTC, datetime

import pytest

from src.domain.entities import TrackPlay
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


@pytest.mark.integration
class TestImportIdempotency:
    """Integration tests for import idempotency with real database operations."""

    @pytest.mark.asyncio
    async def test_duplicate_import_creates_no_duplicates(
        self, db_session, test_data_tracker
    ):
        """CRITICAL: Test that importing the same play twice doesn't create duplicates."""
        from uuid import uuid4

        from src.domain.entities import Artist, Track

        uow = get_unit_of_work(db_session)
        plays_repo = uow.get_plays_repository()
        track_repo = uow.get_track_repository()

        # Create test track with automatic cleanup tracking
        test_track = Track(
            id=None,
            title="TEST_IdempotencyTrack",
            artists=[Artist(name="TEST_IdempotencyArtist")],
            connector_track_identifiers={},
        )
        saved_track = await track_repo.save_track(test_track)
        test_data_tracker.add_track(saved_track.id)

        # Create test batch ID with automatic cleanup tracking
        batch_id = f"TEST_BATCH_{uuid4()}"
        test_data_tracker.add_batch(batch_id)

        # Create identical play data using the test track and batch ID
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

        # First import
        await plays_repo.bulk_insert_plays([test_play])

        # Second import - same exact data
        await plays_repo.bulk_insert_plays([test_play])

        # Verify no duplicates were created
        all_plays = await plays_repo.get_plays_by_batch(batch_id)

        # CRITICAL: Should only have 1 play, not 2
        if len(all_plays) != 1:
            print(f"CRITICAL BUG: Import created {len(all_plays)} plays instead of 1!")
            print("This means imports are NOT idempotent and will create duplicates!")
            for i, play in enumerate(all_plays):
                print(
                    f"Play {i + 1}: track_id={play.track_id}, played_at={play.played_at}, id={play.id}"
                )

        assert len(all_plays) == 1, (
            f"Expected 1 play, got {len(all_plays)}. Import is NOT idempotent!"
        )

        # Verify the play has the expected data
        play = all_plays[0]
        assert play.track_id == saved_track.id
        assert play.service == "spotify"
        assert play.ms_played == 180000

    @pytest.mark.asyncio
    async def test_overlapping_batch_imports_prevent_duplicates(
        self, db_session, test_data_tracker
    ):
        """Test that overlapping imports with different batch IDs don't create duplicates."""
        from uuid import uuid4

        from src.domain.entities import Artist, Track

        uow = get_unit_of_work(db_session)
        plays_repo = uow.get_plays_repository()
        track_repo = uow.get_track_repository()

        # Create test track with automatic cleanup tracking
        test_track = Track(
            id=None,
            title="TEST_OverlapTrack",
            artists=[Artist(name="TEST_OverlapArtist")],
            connector_track_identifiers={},
        )
        saved_track = await track_repo.save_track(test_track)
        test_data_tracker.add_track(saved_track.id)

        # Create same play data but with different batch IDs
        batch_1 = f"TEST_BATCH_{uuid4()}"
        batch_2 = f"TEST_BATCH_{uuid4()}"
        test_data_tracker.add_batch(batch_1)
        test_data_tracker.add_batch(batch_2)

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

        play_2 = TrackPlay(
            track_id=saved_track.id,  # Same track
            service="lastfm",  # Same service
            played_at=datetime(2023, 2, 10, 15, 45, 30, tzinfo=UTC),  # Same play time
            ms_played=240000,  # Same duration
            context={"batch": "second"},  # Different context
            import_timestamp=datetime.now(UTC),
            import_source="lastfm_api",
            import_batch_id=batch_2,  # Different batch ID
        )

        # Import from first batch
        await plays_repo.bulk_insert_plays([play_1])

        # Import from second batch (should be treated as duplicate)
        await plays_repo.bulk_insert_plays([play_2])

        # Get all plays for this track
        all_plays_batch_1 = await plays_repo.get_plays_by_batch(batch_1)
        all_plays_batch_2 = await plays_repo.get_plays_by_batch(batch_2)

        # Check what actually happened
        total_plays = len(all_plays_batch_1) + len(all_plays_batch_2)

        if total_plays == 1:
            # Idempotent behavior - good!
            # One of the batches should have the play, the other should be empty
            assert (len(all_plays_batch_1) == 1 and len(all_plays_batch_2) == 0) or (
                len(all_plays_batch_1) == 0 and len(all_plays_batch_2) == 1
            )
        elif total_plays == 2:
            # Non-idempotent behavior - this indicates a problem with upsert logic
            pytest.fail(
                "Import created duplicates! "
                f"Batch 1 has {len(all_plays_batch_1)} plays, "
                f"Batch 2 has {len(all_plays_batch_2)} plays. "
                "This suggests the upsert logic is not working correctly."
            )
        else:
            pytest.fail(f"Unexpected number of plays: {total_plays}")
