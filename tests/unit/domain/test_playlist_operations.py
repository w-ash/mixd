"""Domain layer tests for playlist operations and business logic.

Tests focus on playlist entity behavior, connector operations, and business rules.
Following TDD principles - write tests first, then implement domain services.
"""

from datetime import UTC, datetime

import pytest

from src.domain.entities.playlist import (
    ConnectorPlaylist,
    ConnectorPlaylistItem,
    Playlist,
    PlaylistEntry,
)
from src.domain.entities.track import Artist, Track


@pytest.mark.unit
class TestPlaylistEntity:
    """Test core playlist entity behavior and business rules."""

    def test_playlist_creation_with_valid_data(self):
        """Test creating a playlist with valid data."""
        tracks = [
            Track(title="Song 1", artists=[Artist(name="Artist 1")]),
            Track(title="Song 2", artists=[Artist(name="Artist 2")]),
        ]

        playlist = Playlist.from_tracklist(
            name="My Playlist", tracklist=tracks, description="A great playlist"
        )

        assert playlist.name == "My Playlist"
        assert playlist.tracks == tracks
        assert playlist.description == "A great playlist"
        assert playlist.id is None
        assert playlist.connector_playlist_identifiers == {}

    def test_playlist_creation_with_minimal_data(self):
        """Test creating a playlist with only required fields."""
        playlist = Playlist.from_tracklist(name="Minimal Playlist", tracklist=[])

        assert playlist.name == "Minimal Playlist"
        assert playlist.tracks == []
        assert playlist.description is None
        assert playlist.id is None
        assert playlist.connector_playlist_identifiers == {}

    def test_from_tracklist_with_connector_identifiers(self):
        """Test creating playlist with connector identifiers in one step."""
        tracks = [Track(title="Song 1", artists=[Artist(name="Artist 1")])]

        playlist = Playlist.from_tracklist(
            name="Test Playlist",
            tracklist=tracks,
            description="Description",
            connector_playlist_identifiers={
                "spotify": "spotify_123",
                "apple_music": "am_456",
            },
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
        from datetime import UTC, datetime

        from src.domain.entities.playlist import PlaylistEntry

        original_tracks = [Track(title="Song 1", artists=[Artist(name="Artist 1")])]
        new_tracks = [Track(title="Song 2", artists=[Artist(name="Artist 2")])]

        playlist = Playlist.from_tracklist(
            name="Test Playlist", tracklist=original_tracks
        )
        new_entries = [
            PlaylistEntry(track=t, added_at=datetime.now(UTC)) for t in new_tracks
        ]
        updated_playlist = playlist.with_entries(new_entries)

        assert updated_playlist.tracks == new_tracks
        assert updated_playlist.name == "Test Playlist"  # Other fields preserved
        assert updated_playlist != playlist  # Immutability
        assert playlist.tracks == original_tracks  # Original unchanged

    def test_playlist_with_connector_playlist_id(self):
        """Test adding connector playlist ID."""
        playlist = Playlist(name="Test Playlist")

        updated_playlist = playlist.with_connector_playlist_id(
            "spotify", "37i9dQZF1DXcBWIGoYBM5M"
        )

        assert (
            updated_playlist.connector_playlist_identifiers["spotify"]
            == "37i9dQZF1DXcBWIGoYBM5M"
        )
        assert updated_playlist != playlist  # Immutability
        assert playlist.connector_playlist_identifiers == {}  # Original unchanged

    def test_playlist_with_multiple_connector_ids(self):
        """Test adding multiple connector IDs."""
        playlist = Playlist(name="Test Playlist")

        playlist = playlist.with_connector_playlist_id("spotify", "spotify_id")
        playlist = playlist.with_connector_playlist_id("apple_music", "apple_id")

        assert playlist.connector_playlist_identifiers["spotify"] == "spotify_id"
        assert playlist.connector_playlist_identifiers["apple_music"] == "apple_id"

    def test_playlist_with_connector_id_validation(self):
        """Test that internal connector names are rejected."""
        playlist = Playlist(name="Test Playlist")

        # Should reject internal connector names
        with pytest.raises(ValueError, match="Cannot use 'db' as connector name"):
            playlist.with_connector_playlist_id("db", "123")

        with pytest.raises(ValueError, match="Cannot use 'internal' as connector name"):
            playlist.with_connector_playlist_id("internal", "123")

    def test_playlist_with_id_validation(self):
        """Test database ID validation."""
        playlist = Playlist(name="Test Playlist")

        # Valid ID
        updated_playlist = playlist.with_id(123)
        assert updated_playlist.id == 123

        # Invalid IDs
        with pytest.raises(ValueError, match="Invalid database ID"):
            playlist.with_id(0)

        with pytest.raises(ValueError, match="Invalid database ID"):
            playlist.with_id(-1)


@pytest.mark.unit
class TestConnectorPlaylistEntity:
    """Test connector playlist entity behavior."""

    def test_connector_playlist_creation(self):
        """Test creating a connector playlist."""
        items = [
            ConnectorPlaylistItem(
                connector_track_identifier="track_1",
                position=1,
                added_at="2023-01-01T00:00:00Z",
            ),
            ConnectorPlaylistItem(
                connector_track_identifier="track_2",
                position=2,
                added_at="2023-01-02T00:00:00Z",
            ),
        ]

        playlist = ConnectorPlaylist(
            connector_name="spotify",
            connector_playlist_identifier="37i9dQZF1DXcBWIGoYBM5M",
            name="Discover Weekly",
            description="Your weekly mixtape",
            items=items,
            owner="Spotify",
            owner_id="spotify",
            is_public=True,
            collaborative=False,
            follower_count=1000000,
        )

        assert playlist.connector_name == "spotify"
        assert playlist.connector_playlist_identifier == "37i9dQZF1DXcBWIGoYBM5M"
        assert playlist.name == "Discover Weekly"
        assert playlist.description == "Your weekly mixtape"
        assert playlist.items == items
        assert playlist.owner == "Spotify"
        assert playlist.owner_id == "spotify"
        assert playlist.is_public is True
        assert playlist.collaborative is False
        assert playlist.follower_count == 1000000

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

    def test_connector_playlist_defaults(self):
        """Test connector playlist default values."""
        playlist = ConnectorPlaylist(
            connector_name="spotify",
            connector_playlist_identifier="test_id",
            name="Test Playlist",
        )

        assert playlist.description is None
        assert playlist.items == []
        assert playlist.owner is None
        assert playlist.owner_id is None
        assert playlist.is_public is False
        assert playlist.collaborative is False
        assert playlist.follower_count is None
        assert playlist.raw_metadata == {}
        assert playlist.id is None
        assert isinstance(playlist.last_updated, datetime)


@pytest.mark.unit
class TestConnectorPlaylistItemEntity:
    """Test connector playlist item entity behavior."""

    def test_connector_playlist_item_creation(self):
        """Test creating a connector playlist item."""
        item = ConnectorPlaylistItem(
            connector_track_identifier="4iV5W9uYEdYUVa79Axb7Rh",
            position=1,
            added_at="2023-01-01T00:00:00Z",
            added_by_id="user_123",
            extras={"is_local": False, "is_playable": True},
        )

        assert item.connector_track_identifier == "4iV5W9uYEdYUVa79Axb7Rh"
        assert item.position == 1
        assert item.added_at == "2023-01-01T00:00:00Z"
        assert item.added_by_id == "user_123"
        assert item.extras["is_local"] is False
        assert item.extras["is_playable"] is True

    def test_connector_playlist_item_defaults(self):
        """Test connector playlist item default values."""
        item = ConnectorPlaylistItem(connector_track_identifier="track_id", position=1)

        assert item.added_at is None
        assert item.added_by_id is None
        assert item.extras == {}


@pytest.mark.unit
class TestPlaylistEntryEntity:
    """Test playlist entry entity behavior."""

    def test_playlist_entry_creation(self):
        """Test creating a playlist entry."""
        test_track = Track(id=123, title="Test", artists=[Artist(name="Test Artist")])
        entry = PlaylistEntry(
            track=test_track, added_at=datetime.now(UTC), added_by="user123"
        )

        assert entry.track.id == 123
        assert entry.added_at is not None
        assert entry.added_by == "user123"

    def test_playlist_entry_defaults(self):
        """Test playlist entry default values."""
        test_track = Track(id=123, title="Test", artists=[Artist(name="Test Artist")])
        entry = PlaylistEntry(track=test_track)

        assert entry.added_at is None
        assert entry.added_by is None


# TODO(#123): Add tests for domain services once they're implemented
