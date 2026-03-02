"""Integration tests for TrackMergeService with real database operations."""

from datetime import UTC

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.persistence.database.db_models import (
    DBTrack,
    DBTrackLike,
    DBTrackPlay,
)
from src.infrastructure.persistence.unit_of_work import DatabaseUnitOfWork
from src.infrastructure.services.track_merge_service import TrackMergeService


class TestTrackMergeServiceIntegration:
    """Integration tests for track merging functionality with real database."""

    async def test_merge_tracks_moves_references(
        self, db_session: AsyncSession, test_data_tracker
    ):
        """Test that merge_tracks moves all foreign key references and hard-deletes loser."""
        # Create two test tracks
        winner_track_db = DBTrack(
            title="Test Song", artists={"names": ["Test Artist"]}, album="Test Album"
        )
        loser_track_db = DBTrack(
            title="Test Song (Duplicate)",
            artists={"names": ["Test Artist"]},
            album="Test Album",
        )

        db_session.add(winner_track_db)
        db_session.add(loser_track_db)
        await db_session.flush()

        # Track cleanup
        test_data_tracker.add_track(winner_track_db.id)
        test_data_tracker.add_track(loser_track_db.id)

        # Create some plays and likes for the loser track
        from datetime import datetime

        play = DBTrackPlay(
            track_id=loser_track_db.id,
            service="spotify",
            played_at=datetime(2025, 1, 1, tzinfo=UTC),
            ms_played=30000,
        )
        like = DBTrackLike(track_id=loser_track_db.id, service="spotify", is_liked=True)

        db_session.add(play)
        db_session.add(like)
        await db_session.flush()

        # Perform merge using UnitOfWork context manager
        uow = DatabaseUnitOfWork(db_session)
        merge_service = TrackMergeService()

        async with uow:
            await merge_service.merge_tracks(winner_track_db.id, loser_track_db.id, uow)

        # Verify foreign key references were moved
        await db_session.refresh(play)
        await db_session.refresh(like)

        assert play.track_id == winner_track_db.id
        assert like.track_id == winner_track_db.id

        # Verify loser track was hard-deleted
        from sqlalchemy import select

        result = await db_session.execute(
            select(DBTrack).where(DBTrack.id == loser_track_db.id)
        )
        deleted_track = result.scalar_one_or_none()
        assert deleted_track is None, "Loser track should be hard-deleted"

    async def test_merge_tracks_validates_input(self, db_session: AsyncSession):
        """Test that merge_tracks validates track IDs."""
        uow = DatabaseUnitOfWork(db_session)
        merge_service = TrackMergeService()

        # Test merging track with itself
        with pytest.raises(ValueError, match="Cannot merge track with itself"):
            async with uow:
                await merge_service.merge_tracks(1, 1, uow)

    async def test_merge_tracks_with_nonexistent_tracks(self, db_session: AsyncSession):
        """Test merge behavior with non-existent tracks."""
        uow = DatabaseUnitOfWork(db_session)
        merge_service = TrackMergeService()

        # Test with non-existent track IDs
        with pytest.raises(ValueError, match="Entity with ID 999 not found"):
            async with uow:
                await merge_service.merge_tracks(999, 1000, uow)
