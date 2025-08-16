"""Tests for TrackMergeService."""

from datetime import UTC

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.track_merge_service import TrackMergeService
from src.domain.entities import Artist, Track
from src.infrastructure.persistence.database.db_models import (
    DBTrack,
    DBTrackLike,
    DBTrackPlay,
)
from src.infrastructure.persistence.unit_of_work import DatabaseUnitOfWork


class TestTrackMergeService:
    """Test track merging functionality."""
    
    async def test_merge_tracks_moves_references(self, db_session: AsyncSession):
        """Test that merge_tracks moves all foreign key references."""
        # Create two test tracks
        winner_track_db = DBTrack(
            title="Test Song",
            artists={"names": ["Test Artist"]},
            album="Test Album"
        )
        loser_track_db = DBTrack(
            title="Test Song (Duplicate)",
            artists={"names": ["Test Artist"]}, 
            album="Test Album"
        )
        
        db_session.add(winner_track_db)
        db_session.add(loser_track_db)
        await db_session.flush()
        
        # Create some plays and likes for the loser track
        from datetime import datetime
        play = DBTrackPlay(
            track_id=loser_track_db.id,
            service="spotify",
            played_at=datetime(2025, 1, 1, tzinfo=UTC),
            ms_played=30000
        )
        like = DBTrackLike(
            track_id=loser_track_db.id,
            service="spotify",
            is_liked=True
        )
        
        db_session.add(play)
        db_session.add(like)
        await db_session.flush()
        
        # Perform merge using UnitOfWork context manager
        uow = DatabaseUnitOfWork(db_session)
        merge_service = TrackMergeService()
        
        async with uow:
            await merge_service.merge_tracks(
                winner_track_db.id, 
                loser_track_db.id,
                uow
            )
        
        # Verify foreign key references were moved
        await db_session.refresh(play)
        await db_session.refresh(like)
        
        assert play.track_id == winner_track_db.id
        assert like.track_id == winner_track_db.id
        
        # Verify loser track is soft-deleted
        await db_session.refresh(loser_track_db)
        assert loser_track_db.is_deleted is True
        assert loser_track_db.deleted_at is not None
    
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


class TestTrackIdentityMethod:
    """Test Track.has_same_identity_as method."""
    
    def test_tracks_with_same_isrc(self):
        """Test tracks with same ISRC are considered identical."""
        track1 = Track(
            title="Test Song",
            artists=[Artist(name="Test Artist")],
            isrc="USUM71703861"
        )
        track2 = Track(
            title="Test Song (Different Title)",
            artists=[Artist(name="Different Artist")],
            isrc="USUM71703861"  # Same ISRC
        )
        
        assert track1.has_same_identity_as(track2)
        assert track2.has_same_identity_as(track1)
    
    def test_tracks_with_same_connector_id(self):
        """Test tracks with same connector track ID are considered identical."""
        track1 = Track(
            title="Test Song",
            artists=[Artist(name="Test Artist")],
            connector_track_ids={"spotify": "4iV5W9uYEdYUVa79Axb7Rh"}
        )
        track2 = Track(
            title="Test Song",
            artists=[Artist(name="Test Artist")],
            connector_track_ids={"spotify": "4iV5W9uYEdYUVa79Axb7Rh"}  # Same Spotify ID
        )
        
        assert track1.has_same_identity_as(track2)
    
    def test_tracks_with_different_identifiers(self):
        """Test tracks with different identifiers are not considered identical."""
        track1 = Track(
            title="Test Song",
            artists=[Artist(name="Test Artist")],
            isrc="USUM71703861"
        )
        track2 = Track(
            title="Test Song",
            artists=[Artist(name="Test Artist")],
            isrc="GBUM71505078"  # Different ISRC
        )
        
        assert not track1.has_same_identity_as(track2)
    
    def test_track_with_non_track_object(self):
        """Test comparison with non-Track object returns False."""
        track = Track(
            title="Test Song",
            artists=[Artist(name="Test Artist")]
        )
        
        assert not track.has_same_identity_as("not a track")
        assert not track.has_same_identity_as(None)