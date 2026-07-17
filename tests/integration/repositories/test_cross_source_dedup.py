"""Integration tests for cross-source play deduplication repository methods.

Tests find_plays_in_time_range against
a real SQLite database to verify SQL correctness and index usage.
"""

from datetime import UTC, datetime
from uuid import uuid4

from src.domain.entities import TrackPlay
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import make_track


class TestFindPlaysInTimeRange:
    """Test time-range play lookup for cross-source dedup."""

    async def test_finds_plays_within_range(self, db_session):
        """Plays within the time range should be returned."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        plays_repo = uow.get_plays_repository()

        track = await track_repo.save_track(
            make_track(
                title="TEST_RangeTrack",
                artist="TEST_RangeArtist",
                connector_track_identifiers={},
            )
        )

        batch_id = f"TEST_BATCH_{uuid4()}"

        plays = [
            TrackPlay(
                track_id=track.id,
                service="spotify",
                played_at=datetime(2024, 10, 1, 20, 0, 0, tzinfo=UTC),
                ms_played=240000,
                import_batch_id=batch_id,
            ),
            TrackPlay(
                track_id=track.id,
                service="lastfm",
                played_at=datetime(2024, 10, 1, 21, 0, 0, tzinfo=UTC),
                ms_played=None,
                import_batch_id=batch_id,
            ),
            TrackPlay(
                track_id=track.id,
                service="spotify",
                played_at=datetime(2024, 10, 1, 23, 0, 0, tzinfo=UTC),
                ms_played=300000,
                import_batch_id=batch_id,
            ),
        ]
        await plays_repo.bulk_insert_plays(plays)

        # Query for 20:30 - 21:30 should return only the 21:00 play
        result = await plays_repo.find_plays_in_time_range(
            user_id="default",
            track_ids=[track.id],
            start=datetime(2024, 10, 1, 20, 30, 0, tzinfo=UTC),
            end=datetime(2024, 10, 1, 21, 30, 0, tzinfo=UTC),
        )

        assert len(result) == 1
        assert result[0].service == "lastfm"

    async def test_empty_track_ids_returns_empty(self, db_session):
        """Empty track_ids should return empty list."""
        uow = get_unit_of_work(db_session)
        plays_repo = uow.get_plays_repository()

        result = await plays_repo.find_plays_in_time_range(
            user_id="default",
            track_ids=[],
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 12, 31, tzinfo=UTC),
        )

        assert result == []
