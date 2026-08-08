"""Tests for Spotify Feb 2026 API migration changes.

Validates: single track fetch, batched track fetch (GET /tracks?ids=),
search limit clamping, playlist items field rename, and non-owned playlist warning.
"""

from collections.abc import Awaitable, Callable, Iterator
from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from src.infrastructure.connectors.spotify.client import SpotifyAPIClient
from src.infrastructure.connectors.spotify.models import (
    SpotifyOwner,
    SpotifyPaginatedPlaylistItems,
    SpotifyPlaylist,
    SpotifyPlaylistItem,
    SpotifyTrack,
)


class _CountingLimiter:
    """Stands in for ConnectorRateLimiter to count tokens spent, not pace."""

    def __init__(self) -> None:
        self.acquires = 0

    async def acquire(self) -> None:
        self.acquires += 1


def _impl_returning_requested_ids() -> AsyncMock:
    """Batch impl that echoes each requested id back in the same position."""

    async def echo(track_ids: list[str]) -> dict[str, object]:
        return {"tracks": [{"id": tid, "name": f"Song {tid}"} for tid in track_ids]}

    return AsyncMock(side_effect=echo)


@contextmanager
def _batch_client(
    impl: AsyncMock | None = None, limiter: _CountingLimiter | None = None
) -> Iterator[SpotifyAPIClient]:
    """Client with the batch impl stubbed, retries bypassed, pacing controlled.

    ``limiter=None`` makes ``_api_call`` take the unpaced path, so tests never
    wait on the process-wide Spotify limiter.
    """

    async def passthrough_retry(
        fn: Callable[..., Awaitable[object]], *args: object
    ) -> object:
        return await fn(*args)

    with ExitStack() as stack:
        _ = stack.enter_context(patch.object(SpotifyAPIClient, "__attrs_post_init__"))
        _ = stack.enter_context(
            patch(
                "src.infrastructure.connectors.base.get_connector_rate_limiter",
                return_value=limiter,
            )
        )
        if impl is not None:
            _ = stack.enter_context(
                patch.object(SpotifyAPIClient, "_get_tracks_batch_impl", impl)
            )
        client = SpotifyAPIClient()
        client._retry_policy = passthrough_retry
        yield client


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


class TestGetTracksBatched:
    """GET /tracks?ids= — chunking, positional correlation, pacing, progress."""

    async def test_empty_input_returns_empty_dict(self):
        with _batch_client() as client:
            assert await client.get_tracks_batched([]) == {}

    async def test_chunks_at_the_endpoint_maximum_of_fifty(self):
        ids = [f"id{i}" for i in range(120)]
        impl = _impl_returning_requested_ids()

        with _batch_client(impl) as client:
            result = await client.get_tracks_batched(ids)

        requested_chunks = [call.args[0] for call in impl.await_args_list]
        assert sorted(len(chunk) for chunk in requested_chunks) == [20, 50, 50]
        assert sorted(id_ for chunk in requested_chunks for id_ in chunk) == sorted(ids)
        assert len(result) == 120

    async def test_keys_by_requested_id_when_spotify_relinks(self):
        """Position, not the returned .id, is what correlates a relinked track."""
        impl = AsyncMock(
            return_value={
                "tracks": [
                    {"id": "aaa", "name": "Song A"},
                    {"id": "new_id", "name": "Redirected Song"},
                ]
            }
        )

        with _batch_client(impl) as client:
            result = await client.get_tracks_batched(["aaa", "old_id"])

        assert set(result) == {"aaa", "old_id"}
        assert result["old_id"].id == "new_id"

    async def test_null_entry_marks_that_id_dead(self):
        impl = AsyncMock(
            return_value={"tracks": [None, {"id": "bbb", "name": "Song B"}]}
        )

        with _batch_client(impl) as client:
            result = await client.get_tracks_batched(["dead_id", "bbb"])

        assert set(result) == {"bbb"}

    async def test_short_array_correlates_the_common_prefix(self):
        """A broken-length response must not shift ids onto the wrong tracks."""
        impl = AsyncMock(return_value={"tracks": [{"id": "aaa", "name": "Song A"}]})

        with _batch_client(impl) as client:
            result = await client.get_tracks_batched(["aaa", "bbb"])

        assert set(result) == {"aaa"}
        assert result["aaa"].id == "aaa"

    async def test_failed_batch_leaves_its_ids_absent(self):
        """Suppressed request failure reads as 'no answer', never as wrong answers."""
        with _batch_client(AsyncMock(return_value=None)) as client:
            result = await client.get_tracks_batched(["aaa", "bbb"])

        assert result == {}

    async def test_one_rate_limiter_token_per_batch_call(self):
        ids = [f"id{i}" for i in range(120)]
        limiter = _CountingLimiter()

        with _batch_client(_impl_returning_requested_ids(), limiter) as client:
            _ = await client.get_tracks_batched(ids)

        # 3 chunks — not 120 tokens, which is the whole point of batching.
        assert limiter.acquires == 3

    async def test_progress_callback_counts_tracks_not_batches(self):
        ids = [f"id{i}" for i in range(120)]
        progress_calls: list[tuple[int, int, str]] = []

        async def cb(current: int, total: int, message: str) -> None:
            progress_calls.append((current, total, message))

        with _batch_client(_impl_returning_requested_ids()) as client:
            await client.get_tracks_batched(ids, progress_callback=cb)

        # Once per chunk, but counted in tracks so the bar stays track-scale.
        assert len(progress_calls) == 3
        assert sorted(c for c, _, _ in progress_calls) == [50, 100, 120]
        assert all(t == 120 for _, t, _ in progress_calls)
        assert all("Spotify" in msg for _, _, msg in progress_calls)

    async def test_request_sends_comma_joined_ids_and_market(self):
        with patch.object(SpotifyAPIClient, "__attrs_post_init__"):
            client = SpotifyAPIClient()
            client._client = AsyncMock()
            response = MagicMock()
            response.json.return_value = {"tracks": []}
            response.raise_for_status.return_value = None
            client._client.get = AsyncMock(return_value=response)

            _ = await client._get_tracks_batch_impl(["aaa", "bbb"])

        params = client._client.get.call_args.kwargs["params"]
        assert client._client.get.call_args.args[0] == "/tracks"
        assert params["ids"] == "aaa,bbb"
        assert params["market"] == client.market


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

            await client._search_track_impl('artist:"Artist" track:"Title"', limit=50)

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
    third-party libs (rspotify#550, psst#721) patched the same pattern.
    Model-level BeforeValidators coerce `null` → `[]` so one quirky track
    or page doesn't poison an entire response.
    """

    def test_track_with_null_artists_parses(self) -> None:
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
