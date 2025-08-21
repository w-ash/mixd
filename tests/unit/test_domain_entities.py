"""Tests for domain layer entities.

These tests verify that the domain entities work correctly and have zero external dependencies.
"""

from datetime import UTC, datetime

import pytest

from src.domain.entities import (
    Artist,
    ConnectorPlaylistItem,
    OperationResult,
    Playlist,
    PlayRecord,
    SyncCheckpoint,
    Track,
    TrackContextFields,
    TrackList,
    TrackPlay,
    create_lastfm_play_record,
    ensure_utc,
)


@pytest.mark.unit
class TestTrackEntities:
    """Test track-related domain entities."""

    def test_artist_creation(self):
        """Test creating an Artist."""
        artist = Artist(name="The Beatles")
        assert artist.name == "The Beatles"

    def test_track_creation(self):
        """Test creating a Track."""
        artist = Artist(name="The Beatles")
        track = Track(
            title="Hey Jude",
            artists=[artist],
            album="Hey Jude",
            duration_ms=420000,
            isrc="GBUM71505078",
        )

        assert track.title == "Hey Jude"
        assert len(track.artists) == 1
        assert track.artists[0].name == "The Beatles"
        assert track.album == "Hey Jude"
        assert track.duration_ms == 420000
        assert track.isrc == "GBUM71505078"

    def test_track_with_id(self):
        """Test setting track ID."""
        artist = Artist(name="Artist")
        track = Track(title="Song", artists=[artist])

        track_with_id = track.with_id(123)
        assert track_with_id.id == 123
        assert track.id is None  # Original unchanged

    def test_track_with_connector_id(self):
        """Test adding connector ID to track."""
        artist = Artist(name="Artist")
        track = Track(title="Song", artists=[artist])

        track_with_spotify = track.with_connector_track_id(
            "spotify", "4uLU6hMCjMI75M1A2tKUQC"
        )
        assert (
            track_with_spotify.connector_track_identifiers["spotify"]
            == "4uLU6hMCjMI75M1A2tKUQC"
        )
        assert track.connector_track_identifiers == {}  # Original unchanged

    def test_track_like_status(self):
        """Test track like status management."""
        artist = Artist(name="Artist")
        track = Track(title="Song", artists=[artist])

        # Initially not liked
        assert not track.is_liked_on("spotify")

        # Set liked
        timestamp = datetime.now(UTC)
        liked_track = track.with_like_status("spotify", True, timestamp)
        assert liked_track.is_liked_on("spotify")
        assert liked_track.get_liked_timestamp("spotify") == timestamp

    def test_track_list_creation(self):
        """Test creating a TrackList."""
        artist = Artist(name="Artist")
        tracks = [Track(title=f"Song {i}", artists=[artist]) for i in range(3)]

        track_list = TrackList(tracks=tracks, metadata={"source": "test"})
        assert len(track_list.tracks) == 3
        assert track_list.metadata["source"] == "test"

    def test_track_list_immutable_operations(self):
        """Test TrackList immutable operations."""
        artist = Artist(name="Artist")
        original_tracks = [Track(title="Song 1", artists=[artist])]
        new_tracks = [Track(title="Song 2", artists=[artist])]

        track_list = TrackList(tracks=original_tracks)
        new_track_list = track_list.with_tracks(new_tracks)

        assert len(track_list.tracks) == 1  # Original unchanged
        assert len(new_track_list.tracks) == 1
        assert new_track_list.tracks[0].title == "Song 2"


class TestPlaylistEntities:
    """Test playlist-related domain entities."""

    def test_playlist_creation(self):
        """Test creating a Playlist."""
        artist = Artist(name="Artist")
        tracks = [Track(title="Song", artists=[artist])]

        playlist = Playlist(
            name="My Playlist", tracks=tracks, description="Test playlist"
        )

        assert playlist.name == "My Playlist"
        assert len(playlist.tracks) == 1
        assert playlist.description == "Test playlist"

    def test_playlist_with_connector_id(self):
        """Test adding connector ID to playlist."""
        playlist = Playlist(name="Test Playlist")

        playlist_with_spotify = playlist.with_connector_playlist_id(
            "spotify", "37i9dQZF1DX0XUsuxWHRQd"
        )
        assert (
            playlist_with_spotify.connector_playlist_identifiers["spotify"]
            == "37i9dQZF1DX0XUsuxWHRQd"
        )
        assert playlist.connector_playlist_identifiers == {}  # Original unchanged

    def test_playlist_connector_id_validation(self):
        """Test that internal connector names are rejected."""
        playlist = Playlist(name="Test Playlist")

        with pytest.raises(ValueError):
            playlist.with_connector_playlist_id("db", "123")

        with pytest.raises(ValueError):
            playlist.with_connector_playlist_id("internal", "123")

    def test_connector_playlist_item(self):
        """Test ConnectorPlaylistItem creation."""
        item = ConnectorPlaylistItem(
            connector_track_identifier="4uLU6hMCjMI75M1A2tKUQC",
            position=1,
            added_at="2023-01-01T00:00:00Z",
        )

        assert item.connector_track_identifier == "4uLU6hMCjMI75M1A2tKUQC"
        assert item.position == 1
        assert item.added_at == "2023-01-01T00:00:00Z"


class TestOperationEntities:
    """Test operation-related domain entities."""

    def test_sync_checkpoint(self):
        """Test SyncCheckpoint creation and updates."""
        checkpoint = SyncCheckpoint(
            user_id="user123", service="spotify", entity_type="likes"
        )

        assert checkpoint.user_id == "user123"
        assert checkpoint.service == "spotify"
        assert checkpoint.entity_type == "likes"

        # Test update
        timestamp = datetime.now(UTC)
        updated = checkpoint.with_update(timestamp, "cursor123")
        assert updated.last_timestamp == timestamp
        assert updated.cursor == "cursor123"
        assert checkpoint.last_timestamp is None  # Original unchanged

    def test_play_record(self):
        """Test PlayRecord creation."""
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

    def test_operation_result(self):
        """Test OperationResult creation and methods."""
        artist = Artist(name="Artist")
        tracks = [
            Track(title="Song 1", artists=[artist]).with_id(1),
            Track(title="Song 2", artists=[artist]).with_id(2),
        ]

        result = OperationResult(
            tracks=tracks, operation_name="test_operation", execution_time=1.5
        )

        assert len(result.tracks) == 2
        assert result.operation_name == "test_operation"
        assert result.execution_time == 1.5

        # Test get_metric with direct metrics setting
        result.metrics["status"] = {1: "processed", 2: "processed"}
        assert result.get_metric(1, "status") == "processed"
        assert result.get_metric(3, "status", "not_found") == "not_found"

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


class TestSharedUtilities:
    """Test shared utility functions."""

    def test_ensure_utc(self):
        """Test UTC timezone enforcement."""
        # Test None input
        assert ensure_utc(None) is None

        # Test naive datetime - intentionally naive to test ensure_utc conversion
        naive_dt = datetime(2023, 1, 1, 12, 0, 0, tzinfo=None)  # noqa: DTZ001
        utc_dt = ensure_utc(naive_dt)
        assert utc_dt.tzinfo == UTC

        # Test already UTC datetime
        already_utc = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = ensure_utc(already_utc)
        assert result == already_utc


class TestEntityImmutability:
    """Test that entities are properly immutable."""

    def test_track_immutable(self):
        """Test Track immutability."""
        artist = Artist(name="Artist")
        track = Track(title="Song", artists=[artist])

        with pytest.raises(AttributeError):
            track.title = "New Title"

    def test_artist_immutable(self):
        """Test Artist immutability."""
        artist = Artist(name="Artist")

        with pytest.raises(AttributeError):
            artist.name = "New Name"

    def test_playlist_immutable(self):
        """Test Playlist immutability."""
        playlist = Playlist(name="Playlist")

        with pytest.raises(AttributeError):
            playlist.name = "New Name"
