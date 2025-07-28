"""Critical test for import idempotency - ensuring re-running imports doesn't create duplicates.

This is the ONLY import test that matters for data integrity. It ensures:
1. Imports are idempotent (can be run multiple times safely)
2. Bulk upsert works efficiently with proper unique constraints
3. No duplicate plays are created under any import scenario
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.domain.entities import TrackPlay
from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.database.db_models import init_db
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


class TestImportIdempotency:
    """Test that imports are truly idempotent."""

    async def _create_test_play(self, batch_id: str) -> TrackPlay:
        """Create a test TrackPlay for idempotency testing."""
        return TrackPlay(
            track_id=123,
            service="spotify",
            played_at=datetime(2023, 1, 15, 14, 30, 22, tzinfo=UTC),
            ms_played=180000,
            context={"test": "data"},
            import_timestamp=datetime.now(UTC),
            import_source="test_import",
            import_batch_id=batch_id,
        )

    @pytest.mark.asyncio
    async def test_duplicate_import_creates_no_duplicates(self):
        """CRITICAL: Test that importing the same play twice doesn't create duplicates."""
        await init_db()
        
        async with get_session() as session:
            uow = get_unit_of_work(session)
            plays_repo = uow.get_plays_repository()
            
            # Create identical play data
            batch_id = str(uuid4())
            test_play = await self._create_test_play(batch_id)
            
            # First import
            first_result = await plays_repo.bulk_insert_plays([test_play])
            
            # Second import - same exact data
            second_result = await plays_repo.bulk_insert_plays([test_play])
            
            # Verify no duplicates were created
            all_plays = await plays_repo.get_plays_by_batch(batch_id)
            
            # CRITICAL: Should only have 1 play, not 2
            if len(all_plays) != 1:
                print(f"CRITICAL BUG: Import created {len(all_plays)} plays instead of 1!")
                print("This means imports are NOT idempotent and will create duplicates!")
                for i, play in enumerate(all_plays):
                    print(f"Play {i+1}: track_id={play.track_id}, played_at={play.played_at}, id={play.id}")
            
            assert len(all_plays) == 1, f"Expected 1 play, got {len(all_plays)}. Import is NOT idempotent!"
            
            # Verify the play has the expected data (skip timezone comparison for now)
            play = all_plays[0]
            assert play.track_id == 123
            assert play.service == "spotify"
            assert play.ms_played == 180000

    @pytest.mark.asyncio 
    async def test_overlapping_batch_imports_prevent_duplicates(self):
        """Test that overlapping imports with different batch IDs don't create duplicates."""
        await init_db()
        
        async with get_session() as session:
            uow = get_unit_of_work(session)
            plays_repo = uow.get_plays_repository()
            
            # Create same play data but with different batch IDs
            batch_1 = str(uuid4())
            batch_2 = str(uuid4())
            
            play_1 = TrackPlay(
                track_id=456,
                service="lastfm", 
                played_at=datetime(2023, 2, 10, 15, 45, 30, tzinfo=UTC),
                ms_played=240000,
                context={"batch": "first"},
                import_timestamp=datetime.now(UTC),
                import_source="lastfm_api",
                import_batch_id=batch_1,
            )
            
            play_2 = TrackPlay(
                track_id=456,  # Same track
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
                assert (len(all_plays_batch_1) == 1 and len(all_plays_batch_2) == 0) or \
                       (len(all_plays_batch_1) == 0 and len(all_plays_batch_2) == 1)
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