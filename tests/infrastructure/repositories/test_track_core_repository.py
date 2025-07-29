"""Tests for TrackRepository - Database model operations and conversions.

Tests focus on DBTrack model operations and DBTrack ↔ Track conversion,
following Clean Architecture where repository layer works with database models.
"""

import pytest

from src.domain.entities import Track
from src.infrastructure.persistence.database.db_models import DBTrack
from src.infrastructure.persistence.repositories.track.core import TrackRepository


class TestTrackRepository:
    """Test cases for TrackRepository using DBTrack models."""

    @pytest.mark.asyncio
    async def test_save_db_track_model(self, db_session, db_track_with_relationships):
        """Test saving DBTrack model to database."""
        TrackRepository(db_session)

        # Execute: Save DBTrack model
        db_session.add(db_track_with_relationships)
        await db_session.commit()

        # Verify: Track was persisted with correct data
        saved_track = await db_session.get(DBTrack, db_track_with_relationships.id)
        assert saved_track is not None
        assert saved_track.title.startswith("Home")
        assert "Mac DeMarco" in saved_track.artists["names"][0]
        assert saved_track.album.startswith("Album")
        assert saved_track.duration_ms == 210000
        assert saved_track.spotify_id.startswith("spotify_")
        assert saved_track.isrc.startswith("ISRC")

    @pytest.mark.asyncio
    async def test_find_db_track_by_id(self, db_session, db_track_with_relationships):
        """Test finding DBTrack by ID using repository."""
        repository = TrackRepository(db_session)

        # Setup: Save track to database
        db_session.add(db_track_with_relationships)
        await db_session.commit()

        # Execute: Find track by ID using get_by_id method
        found_track = await repository.get_by_id(db_track_with_relationships.id)

        # Verify: Track found and converted to domain entity
        assert found_track is not None
        assert isinstance(found_track, Track)
        assert found_track.id == db_track_with_relationships.id
        assert found_track.title.startswith("Home")
        assert len(found_track.artists) == 1
        assert "Mac DeMarco" in found_track.artists[0].name

    @pytest.mark.asyncio
    async def test_db_track_to_domain_conversion(
        self, db_session, db_track_with_relationships
    ):
        """Test DBTrack → Track conversion via TrackMapper."""
        from src.infrastructure.persistence.repositories.track.mapper import TrackMapper

        # Execute: Convert DBTrack to domain Track
        domain_track = await TrackMapper.to_domain(db_track_with_relationships)

        # Verify: Proper conversion to domain entity
        assert isinstance(domain_track, Track)
        # Note: ID will be None until track is persisted to database
        assert domain_track.title.startswith("Home")
        assert len(domain_track.artists) == 1
        assert "Mac DeMarco" in domain_track.artists[0].name
        assert domain_track.album.startswith("Album")
        assert domain_track.duration_ms == 210000
        assert domain_track.isrc.startswith("ISRC")

        # Verify connector track IDs are properly mapped
        assert "spotify" in domain_track.connector_track_ids
        assert domain_track.connector_track_ids["spotify"].startswith("spotify_")
        assert "musicbrainz" in domain_track.connector_track_ids
        assert domain_track.connector_track_ids["musicbrainz"].startswith("mbid-")

    @pytest.mark.asyncio
    async def test_domain_track_to_db_conversion(self, persisted_track):
        """Test Track → DBTrack conversion via TrackMapper."""
        from src.infrastructure.persistence.repositories.track.mapper import TrackMapper

        # Execute: Convert domain Track to DBTrack
        db_track = TrackMapper.to_db(persisted_track)

        # Verify: Proper conversion to database model
        assert isinstance(db_track, DBTrack)
        # Note: ID is not preserved in to_db conversion - handled by database
        assert db_track.title == "Home"
        assert db_track.artists == {"names": ["Mac DeMarco"]}
        assert db_track.album == "2"
        assert db_track.duration_ms == 210000
        assert db_track.isrc == "USWB11300001"
        assert db_track.spotify_id == "4jbmgIyjGoXjY01XxatOx6"

    @pytest.mark.asyncio
    async def test_batch_save_db_tracks(self, db_session, db_tracks_with_relationships):
        """Test batch operations with multiple DBTrack models."""
        TrackRepository(db_session)

        # Execute: Save multiple DBTrack models
        for db_track in db_tracks_with_relationships:
            db_session.add(db_track)
        await db_session.commit()

        # Verify: All tracks were saved (IDs auto-generated)
        for i, db_track in enumerate(db_tracks_with_relationships, 1):
            assert db_track.id is not None, "Database should auto-generate IDs"
            saved_track = await db_session.get(DBTrack, db_track.id)
            assert saved_track is not None
            assert saved_track.title.startswith(f"Track {i}")
            assert f"Artist {i}" in saved_track.artists["names"][0]

    @pytest.mark.asyncio
    async def test_find_tracks_by_spotify_ids(
        self, db_session, db_tracks_with_relationships
    ):
        """Test finding tracks by Spotify IDs using database models."""
        repository = TrackRepository(db_session)

        # Setup: Save tracks with Spotify IDs
        for db_track in db_tracks_with_relationships:
            db_session.add(db_track)
        await db_session.commit()

        # Execute: Find by the actual Spotify IDs from fixtures using find_one_by method
        spotify_ids = [track.spotify_id for track in db_tracks_with_relationships[:2]]
        found_tracks = []
        for spotify_id in spotify_ids:
            track = await repository.find_one_by({"spotify_id": spotify_id})
            if track:
                found_tracks.append(track)

        # Verify: Found tracks are domain entities with correct data
        assert len(found_tracks) == 2
        for track in found_tracks:
            assert isinstance(track, Track)
            assert track.id is not None, "Found tracks should have database IDs"
            assert track.connector_track_ids.get("spotify") in spotify_ids

    @pytest.mark.asyncio
    async def test_repository_handles_empty_relationships(self, db_session):
        """Test repository handles DBTrack with empty relationships correctly."""
        TrackRepository(db_session)

        # Create DBTrack with minimal data and empty relationships
        db_track = DBTrack(
            title="Test Track",
            artists={"names": ["Test Artist"]},
        )
        # Explicitly set empty relationships (prevent lazy loading)
        db_track.mappings = []
        db_track.metrics = []
        db_track.likes = []
        db_track.plays = []
        db_track.playlist_tracks = []

        # Execute: Convert to domain entity
        from src.infrastructure.persistence.repositories.track.mapper import TrackMapper

        domain_track = await TrackMapper.to_domain(db_track)

        # Verify: Conversion succeeds with empty relationships
        assert isinstance(domain_track, Track)
        assert domain_track.title == "Test Track"
        assert len(domain_track.artists) == 1
        assert domain_track.artists[0].name == "Test Artist"
        # Note: DB ID will be None since track not persisted to database
        assert domain_track.id is None

    @pytest.mark.asyncio
    async def test_repository_architecture_compliance(
        self, db_session, db_track_with_relationships
    ):
        """Test that repository follows Clean Architecture boundaries."""
        repository = TrackRepository(db_session)

        # Setup: Save DBTrack model
        db_session.add(db_track_with_relationships)
        await db_session.commit()

        # Execute: Repository operations
        domain_track = await repository.get_by_id(db_track_with_relationships.id)

        # Verify: Repository returns domain entities, not database models
        assert isinstance(domain_track, Track), "Repository must return domain entities"
        assert not isinstance(domain_track, DBTrack), (
            "Repository must not leak database models"
        )

        # Verify: Domain entity has database ID (workflow compliance)
        assert domain_track.id is not None, "Repository tracks must have database IDs"
        assert isinstance(domain_track.id, int), "Database ID must be integer"
