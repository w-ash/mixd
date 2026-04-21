"""Tests for the /me/playlists client method + auto-pagination helper.

Pins three properties:
- Single-page response parses into SpotifyUserPlaylistsResponse.
- Pagination helper stops exactly when `next` is None, one call per page.
- The limit clamp (Spotify max 50) is enforced in the client.
"""

from unittest.mock import AsyncMock, patch

import pytest


def _page(items, next_url=None, offset=0, total=0):
    """Build a /me/playlists JSON shape."""
    return {
        "href": f"https://api.spotify.com/v1/me/playlists?offset={offset}",
        "limit": 50,
        "next": next_url,
        "offset": offset,
        "previous": None,
        "total": total or len(items),
        "items": items,
    }


def _playlist_item(spot_id: str, name: str, snapshot: str = "snap") -> dict:
    return {
        "id": spot_id,
        "name": name,
        "snapshot_id": snapshot,
        "owner": {"id": "me", "display_name": "Me"},
        "public": True,
        "collaborative": False,
        "images": [],
        "items": {"href": "https://.../items", "total": 10},
    }


class TestGetCurrentUserPlaylistsClient:
    async def test_single_page_parses(self) -> None:
        from src.infrastructure.connectors.spotify.client import SpotifyAPIClient

        payload = _page([_playlist_item("a", "A"), _playlist_item("b", "B")], total=2)
        mock_impl = AsyncMock(return_value=payload)

        async def passthrough_retry(impl, *args):
            return await impl(*args)

        with patch.object(
            SpotifyAPIClient, "_get_current_user_playlists_impl", mock_impl
        ):
            with patch.object(SpotifyAPIClient, "__attrs_post_init__"):
                client = SpotifyAPIClient()
                client._retry_policy = passthrough_retry
                result = await client.get_current_user_playlists(limit=50, offset=0)

        assert result is not None
        assert result.total == 2
        assert [p.id for p in result.items] == ["a", "b"]
        # Snapshot_id threads through the model.
        assert result.items[0].snapshot_id == "snap"

    async def test_limit_clamped_to_50(self) -> None:
        """Caller passing 1000 gets clamped to Spotify's hard max of 50."""
        from src.infrastructure.connectors.spotify.client import SpotifyAPIClient

        get_mock = AsyncMock()
        get_mock.return_value.raise_for_status = lambda: None
        get_mock.return_value.json = lambda: _page([])

        with patch.object(SpotifyAPIClient, "__attrs_post_init__"):
            client = SpotifyAPIClient()
            client._client = AsyncMock()
            client._client.get = get_mock

            _ = await client._get_current_user_playlists_impl(limit=1000, offset=0)

        call_kwargs = get_mock.call_args
        assert call_kwargs.kwargs["params"]["limit"] == 50


class TestFetchAllUserPlaylists:
    """Operations-layer pagination loop."""

    async def test_stops_when_next_is_none(self) -> None:
        """Three pages of 50 + one partial ⇒ exactly 4 client calls."""
        from src.infrastructure.connectors.spotify.operations import SpotifyOperations

        page1_items = [_playlist_item(f"a{i}", f"A{i}") for i in range(50)]
        page2_items = [_playlist_item(f"b{i}", f"B{i}") for i in range(50)]
        page3_items = [_playlist_item(f"c{i}", f"C{i}") for i in range(50)]
        page4_items = [_playlist_item(f"d{i}", f"D{i}") for i in range(10)]  # tail

        from src.infrastructure.connectors.spotify.models import (
            SpotifyUserPlaylistsResponse,
        )

        responses = [
            SpotifyUserPlaylistsResponse.model_validate(
                _page(page1_items, next_url="page2", offset=0, total=160)
            ),
            SpotifyUserPlaylistsResponse.model_validate(
                _page(page2_items, next_url="page3", offset=50, total=160)
            ),
            SpotifyUserPlaylistsResponse.model_validate(
                _page(page3_items, next_url="page4", offset=100, total=160)
            ),
            SpotifyUserPlaylistsResponse.model_validate(
                _page(page4_items, next_url=None, offset=150, total=160)
            ),
        ]

        client = AsyncMock()
        client.get_current_user_playlists.side_effect = responses

        ops = SpotifyOperations(client=client)
        playlists = await ops.fetch_all_user_playlists(page_size=50)

        assert client.get_current_user_playlists.await_count == 4
        assert len(playlists) == 160
        # Offsets advance 0 → 50 → 100 → 150 exactly.
        offsets = [
            c.kwargs["offset"]
            for c in client.get_current_user_playlists.await_args_list
        ]
        assert offsets == [0, 50, 100, 150]

    async def test_single_short_page(self) -> None:
        """Under one page of data ⇒ exactly one call."""
        from src.infrastructure.connectors.spotify.models import (
            SpotifyUserPlaylistsResponse,
        )
        from src.infrastructure.connectors.spotify.operations import SpotifyOperations

        client = AsyncMock()
        client.get_current_user_playlists.return_value = (
            SpotifyUserPlaylistsResponse.model_validate(
                _page([_playlist_item("only", "Only")], next_url=None)
            )
        )

        ops = SpotifyOperations(client=client)
        playlists = await ops.fetch_all_user_playlists(page_size=50)

        assert client.get_current_user_playlists.await_count == 1
        assert len(playlists) == 1

    async def test_raises_on_none_response(self) -> None:
        """Suppressed retry failure must not silently return empty."""
        from src.infrastructure.connectors.spotify.operations import (
            SpotifyOperations,
            SpotifyPaginationError,
        )

        client = AsyncMock()
        client.get_current_user_playlists.return_value = None

        ops = SpotifyOperations(client=client)
        with pytest.raises(SpotifyPaginationError):
            _ = await ops.fetch_all_user_playlists()


class TestNullableImagesCoercion:
    """Spotify's Feb 2026 API migration relaxed nullability — `images` can
    arrive as `null` on SimplifiedPlaylistObject (notably collaborative or
    no-art playlists). The response model must coerce this to `[]` rather
    than fail validation. Regression test for a user-reported import crash
    with `2 validation errors for SpotifyUserPlaylistsResponse`.
    """

    def test_playlist_with_null_images_parses(self) -> None:
        from src.infrastructure.connectors.spotify.models import (
            SpotifyUserPlaylistsResponse,
        )

        good = _playlist_item("a", "A")
        null_images: dict = _playlist_item("b", "B")
        null_images["images"] = None

        response = SpotifyUserPlaylistsResponse.model_validate(
            _page([good, null_images], total=2)
        )

        assert [p.id for p in response.items] == ["a", "b"]
        assert response.items[0].images == []
        assert response.items[1].images == []

    def test_mixed_null_and_populated_images_parses(self) -> None:
        """The failure mode from the bug report: a response mixing valid
        image lists with `null` images. All items must parse."""
        from src.infrastructure.connectors.spotify.models import (
            SpotifyUserPlaylistsResponse,
        )

        with_art: dict = _playlist_item("a", "A")
        with_art["images"] = [
            {"url": "https://i.scdn.co/image/abc", "width": 300, "height": 300}
        ]
        no_art: dict = _playlist_item("b", "B")
        no_art["images"] = None

        response = SpotifyUserPlaylistsResponse.model_validate(
            _page([with_art, no_art], total=2)
        )

        assert len(response.items) == 2
        assert response.items[0].images[0]["url"] == "https://i.scdn.co/image/abc"
        assert response.items[1].images == []
