"""Tests for TrackMapper - DBTrack ↔ Track conversion logic.

Tests focus on the bidirectional conversion between database models (DBTrack)
and domain entities (Track), ensuring data integrity and relationship handling.
"""

from datetime import UTC, datetime

import pytest

from src.domain.entities import Artist, Track
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBTrack,
    DBTrackLike,
    DBTrackMapping,
)
from src.infrastructure.persistence.repositories.track.mapper import TrackMapper


class TestTrackMapper:
    """Test cases for TrackMapper conversion logic."""

    @pytest.mark.asyncio
    async def test_db_track_to_domain_basic_conversion(self, persisted_db_track):
        """Test basic DBTrack → Track conversion."""
        # Execute conversion
        domain_track = await TrackMapper.to_domain(persisted_db_track)

        # Verify basic fields
        assert isinstance(domain_track, Track)
        assert domain_track.id is not None, "Persisted track should have database ID"
        assert domain_track.title.startswith("Persisted Track")
        assert domain_track.album.startswith("Persisted Album")
        assert domain_track.duration_ms == 180000
        assert domain_track.isrc.startswith("PERSIST")

        # Verify artists conversion
        assert len(domain_track.artists) == 1
        assert isinstance(domain_track.artists[0], Artist)
        assert domain_track.artists[0].name.startswith("Persisted Artist")

    @pytest.mark.asyncio
    async def test_db_track_to_domain_connector_ids(self, persisted_db_track):
        """Test connector track IDs are properly extracted."""
        domain_track = await TrackMapper.to_domain(persisted_db_track)

        # Verify connector track IDs include database ID
        assert "db" in domain_track.connector_track_ids
        assert domain_track.connector_track_ids["db"] == str(persisted_db_track.id)

        # Verify direct connector IDs from DBTrack fields
        assert "spotify" in domain_track.connector_track_ids
        assert domain_track.connector_track_ids["spotify"].startswith("spotify_")

        assert "musicbrainz" in domain_track.connector_track_ids
        assert domain_track.connector_track_ids["musicbrainz"].startswith("mbid-")

    @pytest.mark.asyncio
    async def test_db_track_with_connector_mappings(self):
        """Test DBTrack with connector track mappings."""
        # Create DBTrack with connector mapping
        db_track = DBTrack(
            id=1,
            title="Test Track",
            artists={"names": ["Test Artist"]},
            spotify_id="spotify123",
        )

        # Create connector track and mapping
        connector_track = DBConnectorTrack(
            id=10,
            connector_name="lastfm",
            connector_track_id="lastfm_track_123",
            title="Test Track",
            artists={"names": ["Test Artist"]},
            raw_metadata={"lastfm_playcount": 1000},
        )

        mapping = DBTrackMapping(
            id=1,
            track_id=1,
            connector_track_id=10,
            match_method="artist_title",
            confidence=95,
        )

        # Set up relationships (simulating eager loading)
        mapping.connector_track = connector_track
        db_track.mappings = [mapping]
        db_track.likes = []
        db_track.metrics = []
        db_track.plays = []
        db_track.playlist_tracks = []

        # Execute conversion
        domain_track = await TrackMapper.to_domain(db_track)

        # Verify connector mapping was processed
        assert "lastfm" in domain_track.connector_track_ids
        assert domain_track.connector_track_ids["lastfm"] == "lastfm_track_123"
        assert "lastfm" in domain_track.connector_metadata
        assert domain_track.connector_metadata["lastfm"]["lastfm_playcount"] == 1000

    @pytest.mark.asyncio
    async def test_db_track_with_likes(self):
        """Test DBTrack with track likes in connector metadata."""
        # Create DBTrack with like
        db_track = DBTrack(id=1, title="Liked Track", artists={"names": ["Artist"]})

        like = DBTrackLike(
            id=1,
            track_id=1,
            service="spotify",
            is_liked=True,
            liked_at=datetime(2023, 1, 1, tzinfo=UTC),
        )

        # Set up relationships
        db_track.mappings = []
        db_track.likes = [like]
        db_track.metrics = []
        db_track.plays = []
        db_track.playlist_tracks = []

        # Execute conversion
        domain_track = await TrackMapper.to_domain(db_track)

        # Verify like data in connector metadata
        assert "spotify" in domain_track.connector_metadata
        spotify_meta = domain_track.connector_metadata["spotify"]
        assert spotify_meta["is_liked"] is True
        assert "liked_at" in spotify_meta
        assert spotify_meta["liked_at"] == "2023-01-01T00:00:00+00:00"

    @pytest.mark.asyncio
    async def test_domain_track_to_db_conversion(self, persisted_track):
        """Test Track → DBTrack conversion."""
        # Execute conversion
        db_track = TrackMapper.to_db(persisted_track)

        # Verify basic fields
        assert isinstance(db_track, DBTrack)
        # Note: ID is not preserved in to_db conversion - handled by database
        assert db_track.title == "Home"
        assert db_track.album == "2"
        assert db_track.duration_ms == 210000
        assert db_track.isrc == "USWB11300001"

        # Verify artists conversion
        assert db_track.artists == {"names": ["Mac DeMarco"]}

        # Verify connector IDs are extracted
        assert db_track.spotify_id == "4jbmgIyjGoXjY01XxatOx6"

    @pytest.mark.asyncio
    async def test_round_trip_conversion(self, db_track_with_relationships):
        """Test DBTrack → Track → DBTrack round trip conversion."""
        # Execute: DBTrack → Track
        domain_track = await TrackMapper.to_domain(db_track_with_relationships)

        # Execute: Track → DBTrack
        converted_db_track = TrackMapper.to_db(domain_track)

        # Verify: Round trip preserves core data (except ID which is database-managed)
        # Note: ID not compared as to_db doesn't preserve ID
        assert converted_db_track.title == db_track_with_relationships.title
        assert converted_db_track.artists == db_track_with_relationships.artists
        assert converted_db_track.album == db_track_with_relationships.album
        assert converted_db_track.duration_ms == db_track_with_relationships.duration_ms
        assert converted_db_track.isrc == db_track_with_relationships.isrc
        assert converted_db_track.spotify_id == db_track_with_relationships.spotify_id

    @pytest.mark.asyncio
    async def test_mapper_handles_none_input(self):
        """Test mapper handles None input gracefully."""
        # Execute: Convert None
        result = await TrackMapper.to_domain(None)

        # Verify: Returns None
        assert result is None

    @pytest.mark.asyncio
    async def test_mapper_handles_minimal_db_track(self):
        """Test mapper handles DBTrack with minimal required fields."""
        # Create minimal DBTrack
        minimal_db_track = DBTrack(
            id=1, title="Minimal Track", artists={"names": ["Artist"]}
        )
        # Set empty relationships
        minimal_db_track.mappings = []
        minimal_db_track.likes = []
        minimal_db_track.metrics = []
        minimal_db_track.plays = []
        minimal_db_track.playlist_tracks = []

        # Execute conversion
        domain_track = await TrackMapper.to_domain(minimal_db_track)

        # Verify: Conversion succeeds with defaults
        assert isinstance(domain_track, Track)
        assert domain_track.id == 1
        assert domain_track.title == "Minimal Track"
        assert len(domain_track.artists) == 1
        assert domain_track.album is None
        assert domain_track.duration_ms is None
        assert len(domain_track.connector_track_ids) >= 1  # At least "db" entry

    @pytest.mark.asyncio
    async def test_mapper_architecture_compliance(self, persisted_db_track):
        """Test mapper follows Clean Architecture conversion patterns."""
        # Execute conversion
        domain_track = await TrackMapper.to_domain(persisted_db_track)

        # Verify: Domain entity is immutable (attrs frozen)
        with pytest.raises(Exception):  # attrs.exceptions.FrozenInstanceError
            domain_track.title = "Modified Title"

        # Verify: Domain entity has no database-specific concerns
        assert not hasattr(domain_track, "created_at")
        assert not hasattr(domain_track, "updated_at")
        assert not hasattr(domain_track, "is_deleted")

        # Verify: Database ID properly set for workflow compliance
        assert domain_track.id is not None
        assert isinstance(domain_track.id, int)
        assert domain_track.id > 0
