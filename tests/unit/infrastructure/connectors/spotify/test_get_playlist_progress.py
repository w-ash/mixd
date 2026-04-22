"""Tests for ``SpotifyOperations.get_playlist_with_all_tracks`` progress callback.

Pins three properties:
- ``on_page`` fires once per page (initial + each paginated fetch).
- The reported ``total`` matches Spotify's ``tracks.total`` field, stable across
  pages (denominator doesn't drift).
- A raising callback does not break the import (progress must be best-effort).
"""

from __future__ import annotations

from unittest.mock import AsyncMock


def _track(idx: int) -> dict:
    """Minimal Spotify track JSON for playlist item pagination."""
    return {
        "id": f"t{idx}",
        "name": f"Track {idx}",
        "artists": [{"id": f"a{idx}", "name": f"Artist {idx}"}],
        "album": {
            "id": f"al{idx}",
            "name": f"Album {idx}",
            "album_type": "album",
            "release_date": "2020-01-01",
            "total_tracks": 10,
            "images": [],
        },
        "duration_ms": 200_000,
        "external_ids": {"isrc": f"ISRC{idx:06d}"},
        "external_urls": {"spotify": f"https://open.spotify.com/track/t{idx}"},
        "popularity": 50,
        "explicit": False,
        "is_local": False,
        "disc_number": 1,
        "track_number": 1,
    }


def _playlist_item(idx: int) -> dict:
    return {
        "added_at": "2020-01-01T00:00:00Z",
        "added_by": {"id": "me"},
        "is_local": False,
        # /playlists/{id}/items (Feb 2026 API) uses `item`, not `track`.
        "item": _track(idx),
    }


def _items_page(
    items: list[dict],
    next_url: str | None,
    total: int,
    offset: int = 0,
) -> dict:
    return {
        "href": f"https://api.spotify.com/v1/playlists/pid/items?offset={offset}",
        "limit": 100,
        "next": next_url,
        "offset": offset,
        "previous": None,
        "total": total,
        "items": items,
    }


def _playlist_response(first_page_items: list[dict], total: int) -> dict:
    """Minimal SpotifyPlaylist JSON shape the operations layer consumes.

    Note: our SpotifyPlaylist model exposes the items page under the
    ``items`` field (not ``tracks``) — the /playlists/{id} response is
    normalized that way at parse time.
    """
    return {
        "id": "pid",
        "name": "Drive Home",
        "description": None,
        "snapshot_id": "snap-1",
        "owner": {"id": "me", "display_name": "Me"},
        "public": True,
        "collaborative": False,
        "images": [],
        "external_urls": {"spotify": "https://open.spotify.com/playlist/pid"},
        "href": "https://api.spotify.com/v1/playlists/pid",
        "items": _items_page(first_page_items, next_url=None, total=total),
    }


class TestGetPlaylistOnPage:
    async def test_fires_once_per_page(self) -> None:
        """A 250-track playlist fetched in three pages (100 + 100 + 50) must
        invoke ``on_page`` four times: once after the initial page (100/250),
        and once after each ``get_next_page`` result (200/250, 250/250)."""
        from src.infrastructure.connectors.spotify.models import (
            SpotifyPaginatedPlaylistItems,
            SpotifyPlaylist,
        )
        from src.infrastructure.connectors.spotify.operations import SpotifyOperations

        page1_items = [_playlist_item(i) for i in range(100)]
        page2_items = [_playlist_item(i) for i in range(100, 200)]
        page3_items = [_playlist_item(i) for i in range(200, 250)]

        initial_playlist_json = _playlist_response(page1_items, total=250)
        initial_playlist_json["items"]["next"] = "cursor-page2"
        playlist = SpotifyPlaylist.model_validate(initial_playlist_json)

        page2 = SpotifyPaginatedPlaylistItems.model_validate(
            _items_page(page2_items, next_url="cursor-page3", total=250, offset=100)
        )
        page3 = SpotifyPaginatedPlaylistItems.model_validate(
            _items_page(page3_items, next_url=None, total=250, offset=200)
        )

        client = AsyncMock()
        client.get_playlist.return_value = playlist
        client.get_current_user_id.return_value = "me"
        client.get_next_page.side_effect = [page2, page3]

        ops = SpotifyOperations(client=client)

        reported: list[tuple[int, int]] = []

        async def on_page(fetched: int, total: int) -> None:
            reported.append((fetched, total))

        _ = await ops.get_playlist_with_all_tracks("pid", on_page=on_page)

        # Initial page emits 100/250; then one emit per get_next_page result.
        assert reported == [(100, 250), (200, 250), (250, 250)]

    async def test_denominator_stable_across_pages(self) -> None:
        """The reported total comes from ``tracks.total`` on the initial page
        and must not shift as pagination advances."""
        from src.infrastructure.connectors.spotify.models import (
            SpotifyPaginatedPlaylistItems,
            SpotifyPlaylist,
        )
        from src.infrastructure.connectors.spotify.operations import SpotifyOperations

        page1 = [_playlist_item(i) for i in range(100)]
        page2 = [_playlist_item(i) for i in range(100, 150)]
        initial = _playlist_response(page1, total=150)
        initial["items"]["next"] = "cursor-page2"

        client = AsyncMock()
        client.get_playlist.return_value = SpotifyPlaylist.model_validate(initial)
        client.get_current_user_id.return_value = "me"
        client.get_next_page.return_value = (
            SpotifyPaginatedPlaylistItems.model_validate(
                _items_page(page2, next_url=None, total=150, offset=100)
            )
        )

        ops = SpotifyOperations(client=client)

        reported: list[int] = []

        async def on_page(fetched: int, total: int) -> None:
            reported.append(total)

        _ = await ops.get_playlist_with_all_tracks("pid", on_page=on_page)

        assert reported == [150, 150]

    async def test_callback_exception_does_not_break_fetch(self) -> None:
        """Progress emission is best-effort — a raising callback is swallowed
        so a broken SSE subscriber can't abort the import."""
        from src.infrastructure.connectors.spotify.models import SpotifyPlaylist
        from src.infrastructure.connectors.spotify.operations import SpotifyOperations

        page1 = [_playlist_item(i) for i in range(5)]
        initial = _playlist_response(page1, total=5)  # next=None — single page

        client = AsyncMock()
        client.get_playlist.return_value = SpotifyPlaylist.model_validate(initial)
        client.get_current_user_id.return_value = "me"

        ops = SpotifyOperations(client=client)

        async def raising_on_page(_fetched: int, _total: int) -> None:
            raise RuntimeError("consumer broke")

        # Should not raise; the fetch completes despite the callback failure.
        result = await ops.get_playlist_with_all_tracks("pid", on_page=raising_on_page)

        assert result is not None
        assert result.name == "Drive Home"
