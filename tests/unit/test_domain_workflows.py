"""Unit tests for pure domain workflow operations.

These tests focus on business logic with no external dependencies,
making them fast and reliable without mocking complexity.
"""

import pytest

from src.domain.entities import Artist, Playlist, Track, TrackList
from src.domain.workflows.playlist_operations import (
    calculate_track_persistence_stats,
    create_playlist_operation,
    create_spotify_playlist_operation,
    format_destination_result,
    format_spotify_destination_result,
    format_update_destination_result,
    update_playlist_tracks_operation,
)


@pytest.fixture
def sample_config():
    """Sample configuration for testing."""
    return {"name": "Test Playlist", "description": "Test Description"}


@pytest.mark.unit
class TestCreatePlaylistOperation:
    """Test pure business logic for playlist creation."""

    def test_create_playlist_with_config(self, tracklist, tracks, sample_config):
        """Test playlist creation with provided configuration."""
        playlist = create_playlist_operation(tracklist, sample_config, tracks)

        assert playlist.name == "Test Playlist"
        assert playlist.description == "Test Description"
        assert playlist.tracks == tracks
        assert playlist.id is None  # Not yet persisted

    def test_create_playlist_with_defaults(self, tracklist, tracks):
        """Test playlist creation with default values."""
        playlist = create_playlist_operation(tracklist, {}, tracks)

        assert playlist.name == "Narada Playlist"
        assert playlist.description == "Created by Narada"
        assert playlist.tracks == tracks


class TestCreateSpotifyPlaylistOperation:
    """Test pure business logic for Spotify playlist creation."""

    def test_create_spotify_playlist(self, tracklist, tracks, sample_config):
        """Test Spotify playlist creation with connector ID."""
        spotify_id = "spotify_123"

        playlist = create_spotify_playlist_operation(
            tracklist, sample_config, tracks, spotify_id
        )

        assert playlist.name == "Test Playlist"
        assert playlist.description == "Test Description"
        assert playlist.tracks == tracks
        assert playlist.connector_playlist_identifiers == {"spotify": spotify_id}


class TestCalculateTrackPersistenceStats:
    """Test pure business logic for persistence statistics."""

    def test_all_new_tracks(self):
        """Test statistics calculation for all new tracks."""
        original_tracks = [
            Track(id=None, title="Track 1", artists=[Artist(name="Artist 1")]),
            Track(id=None, title="Track 2", artists=[Artist(name="Artist 2")]),
        ]
        persisted_tracks = [
            Track(id=1, title="Track 1", artists=[Artist(name="Artist 1")]),
            Track(id=2, title="Track 2", artists=[Artist(name="Artist 2")]),
        ]

        stats = calculate_track_persistence_stats(original_tracks, persisted_tracks)

        assert stats["new_tracks"] == 2
        assert stats["updated_tracks"] == 0

    def test_mixed_tracks(self):
        """Test statistics calculation for mixed new and existing tracks."""
        original_tracks = [
            Track(id=None, title="Track 1", artists=[Artist(name="Artist 1")]),  # New
            Track(id=5, title="Track 2", artists=[Artist(name="Artist 2")]),  # Updated
        ]
        persisted_tracks = [
            Track(
                id=1, title="Track 1", artists=[Artist(name="Artist 1")]
            ),  # Got new ID
            Track(
                id=6, title="Track 2", artists=[Artist(name="Artist 2")]
            ),  # ID changed
        ]

        stats = calculate_track_persistence_stats(original_tracks, persisted_tracks)

        assert stats["new_tracks"] == 1
        assert stats["updated_tracks"] == 1

    def test_no_changes(self):
        """Test statistics calculation when no changes occurred."""
        tracks = [
            Track(id=1, title="Track 1", artists=[Artist(name="Artist 1")]),
            Track(id=2, title="Track 2", artists=[Artist(name="Artist 2")]),
        ]

        stats = calculate_track_persistence_stats(tracks, tracks)

        assert stats["new_tracks"] == 0
        assert stats["updated_tracks"] == 0


class TestFormatDestinationResult:
    """Test pure business logic for result formatting."""

    def test_format_basic_result(self, tracklist, tracks):
        """Test basic result formatting."""
        temp = Playlist.from_tracklist(
            name="Test Playlist", tracklist=tracks, description="Test Description"
        )
        playlist = Playlist(id=1, name=temp.name, entries=temp.entries, description=temp.description)
        stats = {"new_tracks": 2, "updated_tracks": 0}

        result = format_destination_result(
            operation_type="create_internal_playlist",
            playlist=playlist,
            tracklist=tracklist,
            persisted_tracks=tracks,
            stats=stats,
        )

        assert result["playlist_id"] == 1
        assert result["playlist_name"] == "Test Playlist"
        assert result["track_count"] == 3  # Domain tracks fixture has 3 tracks
        assert result["operation"] == "create_internal_playlist"
        assert result["new_tracks"] == 2
        assert result["updated_tracks"] == 0
        assert isinstance(result["tracklist"], TrackList)

    def test_format_with_additional_fields(self, tracklist, tracks):
        """Test result formatting with additional fields."""
        temp = Playlist.from_tracklist(name="Test", tracklist=tracks)
        playlist = Playlist(id=1, name=temp.name, entries=temp.entries)
        stats = {"new_tracks": 1, "updated_tracks": 1}

        result = format_destination_result(
            operation_type="test_operation",
            playlist=playlist,
            tracklist=tracklist,
            persisted_tracks=tracks,
            stats=stats,
            custom_field="custom_value",
            another_field=42,
        )

        assert result["custom_field"] == "custom_value"
        assert result["another_field"] == 42


class TestUpdatePlaylistTracksOperation:
    """Test pure business logic for playlist track updates."""

    def test_replace_mode(self, tracks):
        """Test playlist update in replace mode."""
        existing_tracks = [
            Track(id=99, title="Old Track", artists=[Artist(name="Old Artist")])
        ]
        temp = Playlist.from_tracklist(name="Existing Playlist", tracklist=existing_tracks)
        existing_playlist = Playlist(id=1, name=temp.name, entries=temp.entries)

        updated = update_playlist_tracks_operation(
            existing_playlist, tracks, append_mode=False
        )

        assert updated.tracks == tracks  # Replaced
        assert len(updated.tracks) == 3  # Domain tracks fixture has 3 tracks

    def test_append_mode(self, tracks):
        """Test playlist update in append mode."""
        existing_tracks = [
            Track(id=99, title="Old Track", artists=[Artist(name="Old Artist")])
        ]
        temp = Playlist.from_tracklist(name="Existing Playlist", tracklist=existing_tracks)
        existing_playlist = Playlist(id=1, name=temp.name, entries=temp.entries)

        updated = update_playlist_tracks_operation(
            existing_playlist, tracks, append_mode=True
        )

        assert len(updated.tracks) == 4  # Original + 3 new from domain fixture
        assert updated.tracks[0].title == "Old Track"
        assert updated.tracks[1].title == "Track 1"  # Domain fixture track names
        assert updated.tracks[2].title == "Track 2"
        assert updated.tracks[3].title == "Track 3"


class TestSpotifyFormatting:
    """Test Spotify-specific formatting functions."""

    def test_format_spotify_destination_result(self, tracklist, tracks):
        """Test Spotify destination result formatting."""
        temp = Playlist.from_tracklist(name="Spotify Playlist", tracklist=tracks)
        playlist = Playlist(
            id=1,
            name=temp.name,
            entries=temp.entries,
            connector_playlist_identifiers={"spotify": "spotify_123"},
        )
        stats = {"new_tracks": 2, "updated_tracks": 0}

        result = format_spotify_destination_result(
            playlist=playlist,
            tracklist=tracklist,
            persisted_tracks=tracks,
            stats=stats,
            spotify_id="spotify_123",
        )

        assert result["operation"] == "create_spotify_playlist"
        assert result["spotify_playlist_id"] == "spotify_123"
        assert result["playlist_id"] == 1

    def test_format_update_destination_result(self, tracklist, tracks):
        """Test Spotify update destination result formatting."""
        temp = Playlist.from_tracklist(name="Updated Playlist", tracklist=tracks)
        playlist = Playlist(id=1, name=temp.name, entries=temp.entries)
        stats = {"new_tracks": 1, "updated_tracks": 1}

        result = format_update_destination_result(
            playlist=playlist,
            tracklist=tracklist,
            persisted_tracks=tracks,
            stats=stats,
            spotify_id="spotify_456",
            append_mode=True,
            original_track_count=1,
        )

        assert result["operation"] == "update_spotify_playlist"
        assert result["spotify_playlist_id"] == "spotify_456"
        assert result["append_mode"] is True
        assert result["original_count"] == 1
