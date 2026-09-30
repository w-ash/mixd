"""Tests for Playlist construction helpers and ConnectorPlaylist derived views."""

from datetime import UTC, datetime

from src.domain.entities.playlist import (
    ConnectorPlaylist,
    ConnectorPlaylistItem,
    Playlist,
    PlaylistEntry,
)
from src.domain.entities.track import ArtistCredit, Track
from tests.fixtures import TEST_USER_ID


class TestPlaylistEntity:
    """Test core playlist entity behavior and business rules."""

    def test_from_tracklist_with_connector_identifiers(self):
        """Test creating playlist with connector identifiers in one step."""
        tracks = [
            Track(
                title="Song 1",
                artists=[ArtistCredit(credited_name="Artist 1")],
                user_id=TEST_USER_ID,
            )
        ]

        playlist = Playlist.from_tracklist(
            name="Test Playlist",
            tracklist=tracks,
            description="Description",
            connector_playlist_identifiers={
                "spotify": "spotify_123",
                "apple_music": "am_456",
            },
            user_id=TEST_USER_ID,
        )

        assert playlist.name == "Test Playlist"
        assert len(playlist.tracks) == 1
        assert playlist.description == "Description"
        assert playlist.connector_playlist_identifiers == {
            "spotify": "spotify_123",
            "apple_music": "am_456",
        }

    def test_playlist_with_entries(self):
        """Test creating new playlist with different entries."""

        original_tracks = [
            Track(
                title="Song 1",
                artists=[ArtistCredit(credited_name="Artist 1")],
                user_id=TEST_USER_ID,
            )
        ]
        new_tracks = [
            Track(
                title="Song 2",
                artists=[ArtistCredit(credited_name="Artist 2")],
                user_id=TEST_USER_ID,
            )
        ]

        playlist = Playlist.from_tracklist(
            name="Test Playlist", tracklist=original_tracks, user_id=TEST_USER_ID
        )
        new_entries = [
            PlaylistEntry(track=t, added_at=datetime.now(UTC)) for t in new_tracks
        ]
        updated_playlist = playlist.with_entries(new_entries)

        assert updated_playlist.tracks == new_tracks
        assert updated_playlist.name == "Test Playlist"  # Other fields preserved
        assert updated_playlist != playlist  # Immutability
        assert playlist.tracks == original_tracks  # Original unchanged


class TestConnectorPlaylistEntity:
    """Test connector playlist entity behavior."""

    def test_connector_playlist_track_identifiers_property(self):
        """Test track_ids property extraction."""
        items = [
            ConnectorPlaylistItem(connector_track_identifier="track_1", position=1),
            ConnectorPlaylistItem(connector_track_identifier="track_2", position=2),
            ConnectorPlaylistItem(connector_track_identifier="track_3", position=3),
        ]

        playlist = ConnectorPlaylist(
            connector_name="spotify",
            connector_playlist_identifier="test_id",
            name="Test Playlist",
            items=items,
        )

        assert playlist.track_ids == ["track_1", "track_2", "track_3"]
