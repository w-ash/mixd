"""Domain layer tests for track operations and business logic.

Tests focus on track entity behavior, connector operations, and business rules.
Following TDD principles - write tests first, then implement domain services.
"""

from datetime import UTC, datetime

import pytest

from src.domain.entities import (
    OperationResult,
    PlayRecord,
    SyncCheckpoint,
    TrackContextFields,
    TrackPlay,
    create_lastfm_play_record,
    ensure_utc,
)
from src.domain.entities.track import (
    Artist,
    ConnectorTrackMapping,
    Track,
    TrackLike,
    TrackList,
)


@pytest.mark.unit
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
        assert track.connector_track_identifiers == {}
        assert track.connector_metadata == {}

    def test_track_requires_at_least_one_artist(self):
        """Test that track creation fails without artists."""
        with pytest.raises(ValueError, match="Track must have at least one artist"):
            Track(title="Test Song", artists=[])

    def test_track_with_connector_track_id(self):
        """Test adding connector track ID."""
        track = Track(title="Test Song", artists=[Artist(name="Test Artist")])

        updated_track = track.with_connector_track_id(
            "spotify", "4iV5W9uYEdYUVa79Axb7Rh"
        )

        assert (
            updated_track.connector_track_identifiers["spotify"]
            == "4iV5W9uYEdYUVa79Axb7Rh"
        )
        assert updated_track != track  # Immutability check
        assert track.connector_track_identifiers == {}  # Original unchanged

    def test_track_with_multiple_connector_ids(self):
        """Test adding multiple connector IDs."""
        track = Track(title="Test Song", artists=[Artist(name="Test Artist")])

        track = track.with_connector_track_id("spotify", "spotify_id")
        track = track.with_connector_track_id("lastfm", "lastfm_id")

        assert track.connector_track_identifiers["spotify"] == "spotify_id"
        assert track.connector_track_identifiers["lastfm"] == "lastfm_id"

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
            connector_track_identifier="4iV5W9uYEdYUVa79Axb7Rh",
            match_method="isrc",
            confidence=95,
            metadata={"algorithm_version": "1.0"},
        )

        assert mapping.connector_name == "spotify"
        assert mapping.connector_track_identifier == "4iV5W9uYEdYUVa79Axb7Rh"
        assert mapping.match_method == "isrc"
        assert mapping.confidence == 95
        assert mapping.metadata["algorithm_version"] == "1.0"

    def test_connector_mapping_match_method_validation(self):
        """Test that only valid match methods are accepted."""
        valid_methods = ["direct", "isrc", "mbid", "artist_title"]

        for method in valid_methods:
            mapping = ConnectorTrackMapping(
                connector_name="spotify",
                connector_track_identifier="test_id",
                match_method=method,
                confidence=80,
            )
            assert mapping.match_method == method

        # Invalid method should raise validation error
        with pytest.raises(ValueError):
            ConnectorTrackMapping(
                connector_name="spotify",
                connector_track_identifier="test_id",
                match_method="invalid_method",
                confidence=80,
            )

    def test_connector_mapping_confidence_validation(self):
        """Test confidence score validation."""
        # Valid confidence scores
        for confidence in [0, 50, 100]:
            mapping = ConnectorTrackMapping(
                connector_name="spotify",
                connector_track_identifier="test_id",
                match_method="isrc",
                confidence=confidence,
            )
            assert mapping.confidence == confidence

        # Invalid confidence scores
        for invalid_confidence in [-1, 101]:
            with pytest.raises(ValueError):
                ConnectorTrackMapping(
                    connector_name="spotify",
                    connector_track_identifier="test_id",
                    match_method="isrc",
                    confidence=invalid_confidence,
                )


@pytest.mark.unit
class TestSyncCheckpoint:
    """Test SyncCheckpoint entity behavior."""

    def test_sync_checkpoint_creation_and_update(self):
        """Test SyncCheckpoint creation and immutable updates."""
        checkpoint = SyncCheckpoint(
            user_id="user123", service="spotify", entity_type="likes"
        )

        assert checkpoint.user_id == "user123"
        assert checkpoint.service == "spotify"
        assert checkpoint.entity_type == "likes"

        # Test update returns new instance
        timestamp = datetime.now(UTC)
        updated = checkpoint.with_update(timestamp, "cursor123")
        assert updated.last_timestamp == timestamp
        assert updated.cursor == "cursor123"
        assert checkpoint.last_timestamp is None  # Original unchanged


@pytest.mark.unit
class TestPlayRecord:
    """Test PlayRecord and factory functions."""

    def test_play_record_creation(self):
        """Test PlayRecord creation with all fields."""
        played_at = datetime.now(UTC)
        record = PlayRecord(
            artist_name="Artist",
            track_name="Song",
            played_at=played_at,
            service="spotify",
            album_name="Album",
            ms_played=240000,
        )

        assert record.artist_name == "Artist"
        assert record.track_name == "Song"
        assert record.played_at == played_at
        assert record.service == "spotify"
        assert record.album_name == "Album"
        assert record.ms_played == 240000

    def test_create_lastfm_play_record(self):
        """Test LastFM play record creation factory function."""
        scrobbled_at = datetime.now(UTC)
        record = create_lastfm_play_record(
            artist_name="Artist",
            track_name="Song",
            scrobbled_at=scrobbled_at,
            album_name="Album",
            lastfm_track_url="https://last.fm/track/123",
            mbid="123-456-789",
            loved=True,
        )

        assert record.artist_name == "Artist"
        assert record.track_name == "Song"
        assert record.played_at == scrobbled_at
        assert record.service == "lastfm"
        assert record.album_name == "Album"
        assert (
            record.service_metadata[TrackContextFields.LASTFM_TRACK_URL]
            == "https://last.fm/track/123"
        )
        assert record.service_metadata["mbid"] == "123-456-789"
        assert record.service_metadata["loved"] is True


@pytest.mark.unit
class TestTrackPlayEntity:
    """Test TrackPlay entity behavior."""

    def test_track_play_metadata_extraction(self):
        """Test TrackPlay metadata extraction."""
        context = {
            TrackContextFields.TRACK_NAME: "Song Title",
            TrackContextFields.ARTIST_NAME: "Artist Name",
            TrackContextFields.ALBUM_NAME: "Album Name",
        }

        track_play = TrackPlay(
            track_id=123,
            service="spotify",
            played_at=datetime.now(UTC),
            ms_played=240000,
            context=context,
        )

        metadata = track_play.to_track_metadata()
        assert metadata["title"] == "Song Title"
        assert metadata["artist"] == "Artist Name"
        assert metadata["album"] == "Album Name"
        assert metadata["duration_ms"] == 240000

    def test_track_play_to_track(self):
        """Test converting TrackPlay to Track."""
        context = {
            TrackContextFields.TRACK_NAME: "Song Title",
            TrackContextFields.ARTIST_NAME: "Artist Name",
            TrackContextFields.ALBUM_NAME: "Album Name",
        }

        track_play = TrackPlay(
            track_id=123,
            service="spotify",
            played_at=datetime.now(UTC),
            ms_played=240000,
            context=context,
        )

        track = track_play.to_track()
        assert track.title == "Song Title"
        assert track.artists[0].name == "Artist Name"
        assert track.album == "Album Name"
        assert track.duration_ms == 240000
        assert track.id == 123


@pytest.mark.unit
class TestOperationResultEntity:
    """Test OperationResult behavior."""

    def test_operation_result_per_track_metrics(self):
        """Test OperationResult per-track metric access."""
        artist = Artist(name="Artist")
        tracks = [
            Track(title="Song 1", artists=[artist]).with_id(1),
            Track(title="Song 2", artists=[artist]).with_id(2),
        ]

        result = OperationResult(
            tracks=tracks, operation_name="test_operation", execution_time=1.5
        )

        result.metrics["status"] = {1: "processed", 2: "processed"}
        assert result.get_metric(1, "status") == "processed"
        assert result.get_metric(3, "status", "not_found") == "not_found"


@pytest.mark.unit
class TestEnsureUtc:
    """Test UTC timezone enforcement utility."""

    def test_ensure_utc_none_input(self):
        """Test None passes through."""
        assert ensure_utc(None) is None

    def test_ensure_utc_naive_datetime(self):
        """Test naive datetime is converted to UTC."""
        naive_dt = datetime(2023, 1, 1, 12, 0, 0, tzinfo=None)  # noqa: DTZ001
        utc_dt = ensure_utc(naive_dt)
        assert utc_dt.tzinfo == UTC

    def test_ensure_utc_already_utc(self):
        """Test already-UTC datetime passes through unchanged."""
        already_utc = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = ensure_utc(already_utc)
        assert result == already_utc
