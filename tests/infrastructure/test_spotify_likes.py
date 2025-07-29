"""Tests for Spotify likes functionality."""

from unittest.mock import MagicMock, patch

import pytest

from src.infrastructure.connectors.spotify import SpotifyConnector


@pytest.mark.asyncio
async def test_get_liked_tracks():
    """Test fetching liked tracks from Spotify."""
    # Mock Spotify API response
    mock_response = {
        "items": [
            {
                "added_at": "2023-09-21T15:48:56Z",
                "track": {
                    "id": "1234",
                    "name": "Test Track",
                    "artists": [{"name": "Test Artist"}],
                    "album": {
                        "name": "Test Album",
                        "release_date": "2023-01-01",
                        "release_date_precision": "day",
                    },
                    "duration_ms": 300000,
                    "external_ids": {"isrc": "USABC1234567"},
                    "popularity": 80,
                },
            }
        ],
        "next": None,
    }

    # Create a mock Spotify client
    with patch("spotipy.Spotify"):
        mock_client = MagicMock()
        mock_client.current_user_saved_tracks.return_value = mock_response

        # Configure the connector with the mock client
        connector = SpotifyConnector()
        connector.client = mock_client

        # Call the method being tested
        tracks, next_cursor = await connector.get_liked_tracks(limit=50)

        # Verify the API was called correctly
        mock_client.current_user_saved_tracks.assert_called_once_with(
            limit=50, offset=0, market="US"
        )

        # Verify the results
        assert len(tracks) == 1
        assert next_cursor is None

        track = tracks[0]
        assert track.title == "Test Track"
        assert track.artists[0].name == "Test Artist"
        assert track.album == "Test Album"
        assert track.duration_ms == 300000
        assert track.isrc == "USABC1234567"
        assert track.connector_track_id == "1234"
        assert track.raw_metadata["popularity"] == 80

        # Verify liked_at was parsed from the response
        assert "liked_at" in track.raw_metadata


@pytest.mark.asyncio
async def test_get_liked_tracks_pagination():
    """Test fetching liked tracks from Spotify with pagination."""
    # Mock Spotify API response with pagination
    mock_response_page1 = {
        "items": [
            {
                "added_at": "2023-09-21T15:48:56Z",
                "track": {
                    "id": "1234",
                    "name": "Track 1",
                    "artists": [{"name": "Artist 1"}],
                    "album": {
                        "name": "Album 1",
                        "release_date": "2023-01-01",
                        "release_date_precision": "day",
                    },
                    "duration_ms": 300000,
                    "external_ids": {"isrc": "USABC1234567"},
                    "popularity": 80,
                },
            }
        ],
        "next": "next_page_url",
    }

    mock_response_page2 = {
        "items": [
            {
                "added_at": "2023-09-21T15:48:56Z",
                "track": {
                    "id": "5678",
                    "name": "Track 2",
                    "artists": [{"name": "Artist 2"}],
                    "album": {
                        "name": "Album 2",
                        "release_date": "2023-01-01",
                        "release_date_precision": "day",
                    },
                    "duration_ms": 300000,
                    "external_ids": {"isrc": "USABC7654321"},
                    "popularity": 70,
                },
            }
        ],
        "next": None,
    }

    # Create a mock Spotify client
    with patch("spotipy.Spotify"):
        mock_client = MagicMock()
        mock_client.current_user_saved_tracks.side_effect = [
            mock_response_page1,
            mock_response_page2,
        ]

        # Configure the connector with the mock client
        connector = SpotifyConnector()
        connector.client = mock_client

        # Call the method being tested - first page
        tracks_page1, next_cursor = await connector.get_liked_tracks(limit=1)

        # Verify the first call was made correctly
        mock_client.current_user_saved_tracks.assert_called_with(
            limit=1, offset=0, market="US"
        )

        # Check first page results
        assert len(tracks_page1) == 1
        assert tracks_page1[0].title == "Track 1"
        assert next_cursor == "1"  # Offset for the next page

        # Call again with the cursor for the second page
        tracks_page2, next_cursor = await connector.get_liked_tracks(
            limit=1, cursor=next_cursor
        )

        # Verify the second call with proper offset
        mock_client.current_user_saved_tracks.assert_called_with(
            limit=1, offset=1, market="US"
        )

        # Check second page results
        assert len(tracks_page2) == 1
        assert tracks_page2[0].title == "Track 2"
        assert next_cursor is None  # No more pages
