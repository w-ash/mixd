"""Integration tests for Last.fm play import functionality."""

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from src.domain.entities import PlayRecord
from src.infrastructure.connectors.lastfm import LastFMConnector


class TestLastFMConnectorPlayImport:
    """Test Last.fm connector play import methods."""

    @pytest.fixture
    def mock_lastfm_connector(self):
        """Mock Last.fm connector with test credentials."""
        # Create connector without triggering API initialization
        connector = LastFMConnector()

        # Manually set attributes without triggering __attrs_post_init__
        object.__setattr__(connector, "api_key", "test_key")
        object.__setattr__(connector, "api_secret", "test_secret")
        object.__setattr__(connector, "lastfm_username", "test_user")

        # Mock the client
        object.__setattr__(connector, "client", Mock())
        return connector

    @pytest.fixture
    def mock_lastfm_user(self):
        """Mock pylast User object."""
        user = Mock()
        user.get_recent_tracks = Mock()
        return user

    @pytest.fixture
    def mock_track_data(self):
        """Mock pylast track data structure."""
        # Mock track object
        track = Mock()
        track.get_title.return_value = "Bohemian Rhapsody"
        track.get_url.return_value = (
            "https://www.last.fm/music/Queen/_/Bohemian+Rhapsody"
        )
        track.get_mbid.return_value = "12345-67890-abcdef"

        # Mock artist
        artist = Mock()
        artist.get_name.return_value = "Queen"
        artist.get_url.return_value = "https://www.last.fm/music/Queen"
        artist.get_mbid.return_value = "artist-mbid-123"
        track.get_artist.return_value = artist

        # Mock album
        album = Mock()
        album.get_name.return_value = "A Night at the Opera"
        album.get_url.return_value = (
            "https://www.last.fm/music/Queen/A+Night+at+the+Opera"
        )
        album.get_mbid.return_value = "album-mbid-456"
        track.get_album.return_value = album

        # Return tuple format: (Track, PlayedTime)
        played_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        return (track, played_time)

    @pytest.mark.asyncio
    async def test_get_recent_tracks_success(
        self, mock_lastfm_connector, mock_lastfm_user, mock_track_data
    ):
        """Test successful retrieval of recent tracks."""
        # Setup mocks
        mock_lastfm_connector.client.get_user = Mock(return_value=mock_lastfm_user)
        mock_lastfm_user.get_recent_tracks.return_value = [mock_track_data]

        # Execute
        result = await mock_lastfm_connector.get_recent_tracks(
            username="test_user", limit=50, page=1
        )

        # Verify
        assert len(result) == 1
        play_record = result[0]
        assert isinstance(play_record, PlayRecord)
        assert play_record.track_name == "Bohemian Rhapsody"
        assert play_record.artist_name == "Queen"
        assert play_record.album_name == "A Night at the Opera"
        assert play_record.played_at == datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        assert play_record.service_metadata.get("mbid") == "12345-67890-abcdef"
        assert play_record.service_metadata.get("artist_mbid") == "artist-mbid-123"
        assert play_record.service_metadata.get("album_mbid") == "album-mbid-456"

    @pytest.mark.asyncio
    async def test_get_recent_tracks_no_client(self):
        """Test behavior when client is not initialized."""
        connector = LastFMConnector()
        connector.client = None

        result = await connector.get_recent_tracks()

        assert result == []

    @pytest.mark.asyncio
    async def test_get_recent_tracks_no_username(self, mock_lastfm_connector):
        """Test behavior when no username is provided."""
        mock_lastfm_connector.lastfm_username = None

        result = await mock_lastfm_connector.get_recent_tracks(username=None)

        assert result == []

    @pytest.mark.asyncio
    async def test_get_recent_tracks_with_time_range(
        self, mock_lastfm_connector, mock_lastfm_user, mock_track_data
    ):
        """Test retrieval with time range parameters."""
        # Setup mocks
        mock_lastfm_connector.client.get_user = Mock(return_value=mock_lastfm_user)
        mock_lastfm_user.get_recent_tracks.return_value = [mock_track_data]

        from_time = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
        to_time = datetime(2024, 1, 1, 23, 59, 59, tzinfo=UTC)

        # Execute
        await mock_lastfm_connector.get_recent_tracks(
            username="test_user", from_time=from_time, to_time=to_time
        )

        # Verify API was called with correct parameters
        mock_lastfm_user.get_recent_tracks.assert_called_once()
        call_kwargs = mock_lastfm_user.get_recent_tracks.call_args[1]
        assert "time_from" in call_kwargs
        assert "time_to" in call_kwargs
        assert call_kwargs["time_from"] == int(from_time.timestamp())
        assert call_kwargs["time_to"] == int(to_time.timestamp())

    @pytest.mark.asyncio
    async def test_get_recent_tracks_skips_now_playing(
        self, mock_lastfm_connector, mock_lastfm_user
    ):
        """Test that currently playing tracks (no timestamp) are skipped."""
        # Mock track object
        track = Mock()
        track.get_title.return_value = "Currently Playing"
        track.get_artist.return_value = Mock()
        track.get_artist().get_name.return_value = "Artist"
        track.get_album.return_value = None

        # Setup mocks - one with timestamp, one without (now playing)
        valid_track_data = (track, datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC))
        now_playing_data = (track, None)  # No timestamp = now playing

        mock_lastfm_connector.client.get_user = Mock(return_value=mock_lastfm_user)
        mock_lastfm_user.get_recent_tracks.return_value = [
            valid_track_data,
            now_playing_data,
        ]

        # Execute
        result = await mock_lastfm_connector.get_recent_tracks()

        # Should only return the track with timestamp
        assert len(result) == 1
        assert result[0].track_name == "Currently Playing"

    @pytest.mark.asyncio
    async def test_get_recent_tracks_limit_validation(
        self, mock_lastfm_connector, mock_lastfm_user, mock_track_data
    ):
        """Test limit parameter validation."""
        mock_lastfm_connector.client.get_user = Mock(return_value=mock_lastfm_user)
        mock_lastfm_user.get_recent_tracks.return_value = [mock_track_data]

        # Test limit too high
        await mock_lastfm_connector.get_recent_tracks(limit=500)
        call_kwargs = mock_lastfm_user.get_recent_tracks.call_args[1]
        assert call_kwargs["limit"] == 200  # Should be capped at 200

        # Test limit too low
        await mock_lastfm_connector.get_recent_tracks(limit=0)
        call_kwargs = mock_lastfm_user.get_recent_tracks.call_args[1]
        assert call_kwargs["limit"] == 1  # Should be minimum 1
