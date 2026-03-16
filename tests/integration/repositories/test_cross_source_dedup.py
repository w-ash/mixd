"""Integration tests for cross-source play deduplication repository methods.

Tests find_plays_in_time_range and bulk_update_play_source_services against
a real SQLite database to verify SQL correctness and index usage.
"""

from datetime import UTC, datetime
from uuid import uuid4

from src.domain.entities import Artist, Track, TrackPlay
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


class TestFindPlaysInTimeRange:
    """Test time-range play lookup for cross-source dedup."""

    async def test_finds_plays_within_range(self, db_session, test_data_tracker):
        """Plays within the time range should be returned."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        plays_repo = uow.get_plays_repository()

        track = await track_repo.save_track(
            Track(
                id=None,
                title="TEST_RangeTrack",
                artists=[Artist(name="TEST_RangeArtist")],
                connector_track_identifiers={},
            )
        )
        test_data_tracker.add_track(track.id)

        batch_id = f"TEST_BATCH_{uuid4()}"
        test_data_tracker.add_batch(batch_id)

        # Insert plays at different times
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
            track_ids=[track.id],
            start=datetime(2024, 10, 1, 20, 30, 0, tzinfo=UTC),
            end=datetime(2024, 10, 1, 21, 30, 0, tzinfo=UTC),
        )

        assert len(result) == 1
        assert result[0].service == "lastfm"

    async def test_empty_track_ids_returns_empty(self, db_session, test_data_tracker):
        """Empty track_ids should return empty list."""
        uow = get_unit_of_work(db_session)
        plays_repo = uow.get_plays_repository()

        result = await plays_repo.find_plays_in_time_range(
            track_ids=[],
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 12, 31, tzinfo=UTC),
        )

        assert result == []


class TestBulkUpdatePlaySourceServices:
    """Test bulk updating existing plays with cross-source dedup metadata."""

    async def test_updates_source_services(self, db_session, test_data_tracker):
        """source_services should be updated on existing play."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        plays_repo = uow.get_plays_repository()

        track = await track_repo.save_track(
            Track(
                id=None,
                title="TEST_UpdateTrack",
                artists=[Artist(name="TEST_UpdateArtist")],
                connector_track_identifiers={},
            )
        )
        test_data_tracker.add_track(track.id)

        batch_id = f"TEST_BATCH_{uuid4()}"
        test_data_tracker.add_batch(batch_id)

        play = TrackPlay(
            track_id=track.id,
            service="spotify",
            played_at=datetime(2024, 10, 1, 21, 0, 0, tzinfo=UTC),
            ms_played=240000,
            context={"platform": "osx"},
            import_batch_id=batch_id,
        )
        await plays_repo.bulk_insert_plays([play])

        # Fetch the inserted play to get its ID
        batch_plays = await plays_repo.get_plays_by_batch(batch_id)
        assert len(batch_plays) == 1
        play_id = batch_plays[0].id
        assert play_id is not None

        # Bulk update with cross-source metadata
        await plays_repo.bulk_update_play_source_services([
            (play_id, {
                "source_services": ["spotify", "lastfm"],
                "context": {"platform": "osx", "merged_from_lastfm": {"mbid": "abc"}},
            }),
        ])

        # Verify by re-fetching
        updated_plays = await plays_repo.get_plays_by_batch(batch_id)
        assert len(updated_plays) == 1
        updated = updated_plays[0]
        assert updated.source_services == ["spotify", "lastfm"]
        assert updated.context is not None
        assert "merged_from_lastfm" in updated.context

    async def test_backfills_ms_played(self, db_session, test_data_tracker):
        """ms_played should be backfilled when existing play lacks it."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        plays_repo = uow.get_plays_repository()

        track = await track_repo.save_track(
            Track(
                id=None,
                title="TEST_BackfillTrack",
                artists=[Artist(name="TEST_BackfillArtist")],
                connector_track_identifiers={},
            )
        )
        test_data_tracker.add_track(track.id)

        batch_id = f"TEST_BATCH_{uuid4()}"
        test_data_tracker.add_batch(batch_id)

        # Insert play without ms_played (like a Last.fm play)
        play = TrackPlay(
            track_id=track.id,
            service="lastfm",
            played_at=datetime(2024, 10, 1, 21, 0, 0, tzinfo=UTC),
            ms_played=None,
            import_batch_id=batch_id,
        )
        await plays_repo.bulk_insert_plays([play])

        batch_plays = await plays_repo.get_plays_by_batch(batch_id)
        play_id = batch_plays[0].id

        # Backfill ms_played from Spotify match via bulk update
        await plays_repo.bulk_update_play_source_services([
            (play_id, {
                "source_services": ["lastfm", "spotify"],
                "ms_played": 240000,
            }),
        ])

        updated_plays = await plays_repo.get_plays_by_batch(batch_id)
        assert updated_plays[0].ms_played == 240000
