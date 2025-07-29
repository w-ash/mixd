"""Domain layer tests for track operations and business logic.

Tests focus on track entity behavior, connector operations, and business rules.
Following TDD principles - write tests first, then implement domain services.
"""

from datetime import UTC, datetime

import pytest

from src.domain.entities.track import (
    Artist,
    ConnectorTrackMapping,
    Track,
    TrackLike,
    TrackList,
)


class TestTrackEntity:
    """Test core track entity behavior and business rules."""

    def test_track_creation_with_valid_data(self):
        """Test creating a track with valid data."""
        artist = Artist(name="Radiohead")
        track = Track(
            title="Paranoid Android",
            artists=[artist],
            album="OK Computer",
            duration_ms=383000,
            isrc="GBUM71505078",
        )

        assert track.title == "Paranoid Android"
        assert track.artists == [artist]
        assert track.album == "OK Computer"
        assert track.duration_ms == 383000
        assert track.isrc == "GBUM71505078"
        assert track.id is None
        assert track.connector_track_ids == {}
        assert track.connector_metadata == {}

    def test_track_requires_at_least_one_artist(self):
        """Test that track creation fails without artists."""
        with pytest.raises(ValueError, match="Length of 'artists' must be >= 1"):
            Track(title="Test Song", artists=[])

    def test_track_with_connector_track_id(self):
        """Test adding connector track ID."""
        track = Track(title="Test Song", artists=[Artist(name="Test Artist")])

        updated_track = track.with_connector_track_id(
            "spotify", "4iV5W9uYEdYUVa79Axb7Rh"
        )

        assert updated_track.connector_track_ids["spotify"] == "4iV5W9uYEdYUVa79Axb7Rh"
        assert updated_track != track  # Immutability check
        assert track.connector_track_ids == {}  # Original unchanged

    def test_track_with_multiple_connector_ids(self):
        """Test adding multiple connector IDs."""
        track = Track(title="Test Song", artists=[Artist(name="Test Artist")])

        track = track.with_connector_track_id("spotify", "spotify_id")
        track = track.with_connector_track_id("lastfm", "lastfm_id")

        assert track.connector_track_ids["spotify"] == "spotify_id"
        assert track.connector_track_ids["lastfm"] == "lastfm_id"

    def test_track_with_id_validation(self):
        """Test database ID validation."""
        track = Track(title="Test Song", artists=[Artist(name="Test Artist")])

        # Valid ID
        updated_track = track.with_id(123)
        assert updated_track.id == 123

        # Invalid IDs
        with pytest.raises(ValueError, match="Invalid database ID"):
            track.with_id(0)

        with pytest.raises(ValueError, match="Invalid database ID"):
            track.with_id(-1)

    def test_track_like_status_operations(self):
        """Test like status business logic."""
        track = Track(title="Test Song", artists=[Artist(name="Test Artist")])
        timestamp = datetime.now(UTC)

        # Add like status
        liked_track = track.with_like_status("spotify", True, timestamp)

        # Check like status
        assert liked_track.is_liked_on("spotify") is True
        assert liked_track.is_liked_on("lastfm") is False
        assert liked_track.get_liked_timestamp("spotify") == timestamp
        assert liked_track.get_liked_timestamp("lastfm") is None

        # Remove like status
        unliked_track = liked_track.with_like_status("spotify", False)
        assert unliked_track.is_liked_on("spotify") is False

    def test_track_connector_metadata_operations(self):
        """Test connector metadata business logic."""
        track = Track(title="Test Song", artists=[Artist(name="Test Artist")])

        metadata = {"popularity": 85, "genres": ["rock", "alternative"]}
        updated_track = track.with_connector_metadata("spotify", metadata)

        assert updated_track.get_connector_attribute("spotify", "popularity") == 85
        assert updated_track.get_connector_attribute("spotify", "genres") == [
            "rock",
            "alternative",
        ]
        assert updated_track.get_connector_attribute("spotify", "nonexistent") is None
        assert (
            updated_track.get_connector_attribute("spotify", "nonexistent", "default")
            == "default"
        )

    def test_track_connector_metadata_merging(self):
        """Test that connector metadata merges correctly."""
        track = Track(title="Test Song", artists=[Artist(name="Test Artist")])

        # Add initial metadata
        track = track.with_connector_metadata("spotify", {"popularity": 85})

        # Add more metadata - should merge, not replace
        track = track.with_connector_metadata("spotify", {"genres": ["rock"]})

        assert track.get_connector_attribute("spotify", "popularity") == 85
        assert track.get_connector_attribute("spotify", "genres") == ["rock"]


class TestTrackListEntity:
    """Test track list entity behavior for processing pipelines."""

    def test_track_list_creation(self):
        """Test creating a track list."""
        tracks = [
            Track(title="Song 1", artists=[Artist(name="Artist 1")]),
            Track(title="Song 2", artists=[Artist(name="Artist 2")]),
        ]

        track_list = TrackList(tracks=tracks)

        assert track_list.tracks == tracks
        assert track_list.metadata == {}

    def test_track_list_with_tracks(self):
        """Test creating new track list with different tracks."""
        original_tracks = [Track(title="Song 1", artists=[Artist(name="Artist 1")])]
        new_tracks = [Track(title="Song 2", artists=[Artist(name="Artist 2")])]

        track_list = TrackList(tracks=original_tracks)
        updated_list = track_list.with_tracks(new_tracks)

        assert updated_list.tracks == new_tracks
        assert updated_list != track_list  # Immutability
        assert track_list.tracks == original_tracks  # Original unchanged

    def test_track_list_with_metadata(self):
        """Test adding metadata to track list."""
        track_list = TrackList(tracks=[])

        updated_list = track_list.with_metadata("source", "spotify_playlist")

        assert updated_list.metadata["source"] == "spotify_playlist"
        assert updated_list != track_list  # Immutability
        assert track_list.metadata == {}  # Original unchanged


class TestTrackLikeEntity:
    """Test track like entity behavior."""

    def test_track_like_creation(self):
        """Test creating a track like."""
        timestamp = datetime.now(UTC)

        like = TrackLike(
            track_id=123, service="spotify", is_liked=True, liked_at=timestamp
        )

        assert like.track_id == 123
        assert like.service == "spotify"
        assert like.is_liked is True
        assert like.liked_at == timestamp
        assert like.last_synced is None
        assert like.id is None

    def test_track_like_defaults(self):
        """Test track like default values."""
        like = TrackLike(track_id=123, service="spotify")

        assert like.is_liked is True  # Default to liked
        assert like.liked_at is None
        assert like.last_synced is None


class TestConnectorTrackMappingEntity:
    """Test connector track mapping entity for cross-service resolution."""

    def test_connector_mapping_creation(self):
        """Test creating a connector mapping."""
        mapping = ConnectorTrackMapping(
            connector_name="spotify",
            connector_track_id="4iV5W9uYEdYUVa79Axb7Rh",
            match_method="isrc",
            confidence=95,
            metadata={"algorithm_version": "1.0"},
        )

        assert mapping.connector_name == "spotify"
        assert mapping.connector_track_id == "4iV5W9uYEdYUVa79Axb7Rh"
        assert mapping.match_method == "isrc"
        assert mapping.confidence == 95
        assert mapping.metadata["algorithm_version"] == "1.0"

    def test_connector_mapping_match_method_validation(self):
        """Test that only valid match methods are accepted."""
        valid_methods = ["direct", "isrc", "mbid", "artist_title"]

        for method in valid_methods:
            mapping = ConnectorTrackMapping(
                connector_name="spotify",
                connector_track_id="test_id",
                match_method=method,
                confidence=80,
            )
            assert mapping.match_method == method

        # Invalid method should raise validation error
        with pytest.raises(ValueError):
            ConnectorTrackMapping(
                connector_name="spotify",
                connector_track_id="test_id",
                match_method="invalid_method",
                confidence=80,
            )

    def test_connector_mapping_confidence_validation(self):
        """Test confidence score validation."""
        # Valid confidence scores
        for confidence in [0, 50, 100]:
            mapping = ConnectorTrackMapping(
                connector_name="spotify",
                connector_track_id="test_id",
                match_method="isrc",
                confidence=confidence,
            )
            assert mapping.confidence == confidence

        # Invalid confidence scores
        for invalid_confidence in [-1, 101]:
            with pytest.raises(ValueError):
                ConnectorTrackMapping(
                    connector_name="spotify",
                    connector_track_id="test_id",
                    match_method="isrc",
                    confidence=invalid_confidence,
                )
