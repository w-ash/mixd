"""Tests for ListenBrainz Labs API lookup client.

Validates Spotify ID resolution via the ListenBrainz metadata lookup
endpoint, including response parsing, URI prefix stripping, and error
handling for HTTP and request failures.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx

from src.infrastructure.connectors.listenbrainz.lookup import ListenBrainzLookup


class TestSpotifyIdFromMetadata:
    """Spotify ID resolution from artist + recording name."""

    async def test_returns_spotify_id_on_success(self):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.json.return_value = [{"spotify_track_id": "abc123"}]
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        lookup = ListenBrainzLookup(client=mock_client)
        result = await lookup.spotify_id_from_metadata("Radiohead", "Creep")

        assert result == "abc123"

    async def test_strips_spotify_uri_prefix(self):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.json.return_value = [{"spotify_track_id": "spotify:track:abc123"}]
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        lookup = ListenBrainzLookup(client=mock_client)
        result = await lookup.spotify_id_from_metadata("Radiohead", "Creep")

        assert result == "abc123"

    async def test_returns_none_on_empty_response(self):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        lookup = ListenBrainzLookup(client=mock_client)
        result = await lookup.spotify_id_from_metadata("Radiohead", "Creep")

        assert result is None

    async def test_returns_none_on_missing_field(self):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.json.return_value = [{}]
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        lookup = ListenBrainzLookup(client=mock_client)
        result = await lookup.spotify_id_from_metadata("Radiohead", "Creep")

        assert result is None

    async def test_returns_none_on_http_error(self):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_error_response = MagicMock(spec=httpx.Response)
        mock_error_response.status_code = 500
        mock_client.post.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=mock_error_response
        )

        lookup = ListenBrainzLookup(client=mock_client)
        result = await lookup.spotify_id_from_metadata("Radiohead", "Creep")

        assert result is None

    async def test_returns_none_on_request_error(self):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.side_effect = httpx.RequestError(
            "connection failed", request=MagicMock()
        )

        lookup = ListenBrainzLookup(client=mock_client)
        result = await lookup.spotify_id_from_metadata("Radiohead", "Creep")

        assert result is None

    async def test_sends_correct_payload(self):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.json.return_value = [{"spotify_track_id": "abc123"}]
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        lookup = ListenBrainzLookup(client=mock_client)
        await lookup.spotify_id_from_metadata("Radiohead", "Creep")

        mock_client.post.assert_called_once_with(
            "/spotify-id-from-metadata/json",
            json=[{"artist_name": "Radiohead", "recording_name": "Creep"}],
        )
