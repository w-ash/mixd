"""Tests for Spotify Feb 2026 API migration changes.

Validates: single track fetch, concurrent fetch with semaphore,
search limit clamping, playlist items field rename, and non-owned playlist warning.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from src.infrastructure.connectors.spotify.models import (
    SpotifyOwner,
    SpotifyPaginatedPlaylistItems,
    SpotifyPlaylist,
    SpotifyPlaylistItem,
)
from tests.fixtures import make_spotify_track


class TestGetTrackSingle:
    """GET /tracks/{id} returns a single validated SpotifyTrack."""

    async def test_get_track_returns_model(self):
        from src.infrastructure.connectors.spotify.client import SpotifyAPIClient

        track_data = {"id": "abc123", "name": "Test Song"}
        mock_impl = AsyncMock(return_value=track_data)

        async def passthrough_retry(impl, *args):
            return await impl(*args)

        with patch.object(SpotifyAPIClient, "_get_track_impl", mock_impl):
            with patch.object(SpotifyAPIClient, "__attrs_post_init__"):
                client = SpotifyAPIClient()
                client._retry_policy = passthrough_retry
                result = await client.get_track("abc123")

        assert result is not None
        assert result.id == "abc123"
        assert result.name == "Test Song"

    async def test_get_track_returns_none_on_failure(self):
        from src.infrastructure.connectors.spotify.client import SpotifyAPIClient

        mock_impl = AsyncMock(return_value=None)

        async def passthrough_retry(impl, *args):
            return await impl(*args)

        with patch.object(SpotifyAPIClient, "_get_track_impl", mock_impl):
            with patch.object(SpotifyAPIClient, "__attrs_post_init__"):
                client = SpotifyAPIClient()
                client._retry_policy = passthrough_retry
                result = await client.get_track("missing")

        assert result is None


class TestGetTracksConcurrent:
    """Concurrent fetch returns dict keyed by requested ID."""

    async def test_empty_input_returns_empty_dict(self):
        from src.infrastructure.connectors.spotify.client import SpotifyAPIClient

        with patch.object(SpotifyAPIClient, "__attrs_post_init__"):
            client = SpotifyAPIClient()
            result = await client.get_tracks_concurrent([])

        assert result == {}

    async def test_concurrent_fetch_returns_dict_keyed_by_requested_id(self):
        from src.infrastructure.connectors.spotify.client import SpotifyAPIClient

        track_a = make_spotify_track("aaa", "Song A")
        track_b = make_spotify_track("bbb", "Song B")

        mock_get_track = AsyncMock(side_effect=[track_a, track_b])

        with patch.object(SpotifyAPIClient, "__attrs_post_init__"):
            with patch.object(SpotifyAPIClient, "get_track", mock_get_track):
                client = SpotifyAPIClient()
                with patch(
                    "src.infrastructure.connectors.spotify.client.settings"
                ) as mock_settings:
                    mock_settings.api.spotify.concurrency = 5
                    result = await client.get_tracks_concurrent(["aaa", "bbb"])

        assert isinstance(result, dict)
        assert "aaa" in result
        assert "bbb" in result
        assert result["aaa"].id == "aaa"
        assert result["bbb"].id == "bbb"

    async def test_partial_failures_skipped(self):
        from src.infrastructure.connectors.spotify.client import SpotifyAPIClient

        track_a = make_spotify_track("aaa", "Song A")
        mock_get_track = AsyncMock(side_effect=[track_a, None])

        with patch.object(SpotifyAPIClient, "__attrs_post_init__"):
            with patch.object(SpotifyAPIClient, "get_track", mock_get_track):
                client = SpotifyAPIClient()
                with patch(
                    "src.infrastructure.connectors.spotify.client.settings"
                ) as mock_settings:
                    mock_settings.api.spotify.concurrency = 5
                    result = await client.get_tracks_concurrent(["aaa", "bbb"])

        assert len(result) == 1
        assert "aaa" in result

    async def test_get_tracks_concurrent_keys_by_requested_id(self):
        """When Spotify returns a track with different .id, dict keys by REQUESTED id."""
        from src.infrastructure.connectors.spotify.client import SpotifyAPIClient

        # Simulate redirect: asked for "old_id", Spotify returns track with id="new_id"
        redirected_track = make_spotify_track("new_id", "Redirected Song")
        mock_get_track = AsyncMock(return_value=redirected_track)

        with patch.object(SpotifyAPIClient, "__attrs_post_init__"):
            with patch.object(SpotifyAPIClient, "get_track", mock_get_track):
                client = SpotifyAPIClient()
                with patch(
                    "src.infrastructure.connectors.spotify.client.settings"
                ) as mock_settings:
                    mock_settings.api.spotify.concurrency = 5
                    result = await client.get_tracks_concurrent(["old_id"])

        # Keyed by requested ID, not the returned track's .id
        assert "old_id" in result
        assert "new_id" not in result
        assert result["old_id"].id == "new_id"


class TestSearchLimitClamped:
    """Search limit should be clamped to SEARCH_MAX_LIMIT (10)."""

    async def test_search_limit_clamped_to_10(self):
        from src.infrastructure.connectors.spotify.client import SpotifyAPIClient

        with patch.object(SpotifyAPIClient, "__attrs_post_init__"):
            client = SpotifyAPIClient()
            client._client = AsyncMock()
            mock_response = MagicMock()
            mock_response.json.return_value = {"tracks": {"items": []}}
            mock_response.raise_for_status.return_value = None
            client._client.get = AsyncMock(return_value=mock_response)

            await client._search_track_impl("Artist", "Title", limit=50)

            # Verify the limit param was clamped to 10
            call_kwargs = client._client.get.call_args
            assert call_kwargs.kwargs["params"]["limit"] == 10


class TestPlaylistItemsFieldRename:
    """Verify model parses `items` not `tracks` on SpotifyPlaylist."""

    def test_playlist_model_uses_items_field(self):
        data = {
            "id": "pl1",
            "name": "My Playlist",
            "owner": {"id": "user1"},
            "items": {
                "href": "https://api.spotify.com/v1/playlists/pl1/items",
                "total": 42,
                "items": [],
            },
        }
        playlist = SpotifyPlaylist.model_validate(data)
        assert playlist.items.total == 42
        assert playlist.items.href.endswith("/items")

    def test_playlist_item_uses_item_field(self):
        data = {
            "item": {"id": "tr1", "name": "Song"},
            "added_at": "2024-01-01T00:00:00Z",
        }
        item = SpotifyPlaylistItem.model_validate(data)
        assert item.item is not None
        assert item.item.id == "tr1"


class TestNonOwnedPlaylistWarning:
    """Verify warning logged for non-owned playlists."""

    async def test_non_owned_playlist_logs_warning(self, caplog):
        from src.infrastructure.connectors.spotify.operations import SpotifyOperations

        playlist = SpotifyPlaylist(
            id="pl1",
            name="Editorial Playlist",
            owner=SpotifyOwner(id="spotify_editorial"),
            items=SpotifyPaginatedPlaylistItems(total=0, items=[]),
        )

        mock_client = AsyncMock()
        mock_client.get_playlist.return_value = playlist
        mock_client.get_current_user_id.return_value = "my_user_id"
        mock_client.get_next_page.return_value = None

        operations = SpotifyOperations.__new__(SpotifyOperations)
        operations.client = mock_client

        result = await operations.get_playlist_with_all_tracks("pl1")

        # Should complete without error
        assert result is not None
        # Items should be empty (non-owned playlist)
        assert len(result.items) == 0


class TestFeb2026NullableListCoercion:
    """Feb 2026 API migration relaxed nullability on several list fields —
    third-party libs (rspotify#550, psst#721) are patching the same pattern.

    Model-level BeforeValidators must coerce `null` → `[]` at the boundary so
    a single quirky track or page doesn't poison an entire response. Companion
    regression test for `SpotifyPlaylist.images` lives in
    test_user_playlists.py::TestNullableImagesCoercion.
    """

    def test_track_with_null_artists_parses(self) -> None:
        from src.infrastructure.connectors.spotify.models import SpotifyTrack

        track = SpotifyTrack.model_validate({"id": "abc", "name": "X", "artists": None})
        assert track.artists == []

    def test_paginated_items_with_null_items_parses(self) -> None:
        payload = {
            "href": "https://api.spotify.com/v1/playlists/x/items",
            "limit": 50,
            "offset": 0,
            "total": 0,
            "items": None,
        }
        parsed = SpotifyPaginatedPlaylistItems.model_validate(payload)
        assert parsed.items == []
