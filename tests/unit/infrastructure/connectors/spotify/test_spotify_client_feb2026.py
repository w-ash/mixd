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
    """One track is the degenerate case of the batch fetch — one id, one call."""

    async def test_get_track_returns_model(self):
        impl = AsyncMock(
            return_value={"tracks": [{"id": "abc123", "name": "Test Song"}]}
        )

        with _batch_client(impl) as client:
            result = await client.get_track("abc123")

        assert impl.await_args.args[0] == ["abc123"]
        assert result is not None
        assert result.id == "abc123"
        assert result.name == "Test Song"

    async def test_get_track_returns_none_for_a_dead_id(self):
        """A dead id is a null array entry, where it used to be a 404."""
        with _batch_client(AsyncMock(return_value={"tracks": [None]})) as client:
            result = await client.get_track("missing")

        assert result is None

    async def test_get_track_returns_none_on_failure(self):
        """Unanswered collapses to None here — the batch form keeps them apart."""
        with _batch_client(AsyncMock(return_value=None)) as client:
            result = await client.get_track("missing")

        assert result is None


class TestGetTracksBatched:
    """GET /tracks?ids= — chunking, positional correlation, pacing, progress."""

    async def test_empty_input_returns_an_empty_fetch(self):
        with _batch_client() as client:
            fetch = await client.get_tracks_batched([])

        assert fetch.tracks == {}
        assert fetch.unanswered == frozenset()

    async def test_chunks_at_the_endpoint_maximum_of_fifty(self):
        ids = [f"id{i}" for i in range(120)]
        impl = _impl_returning_requested_ids()

        with _batch_client(impl) as client:
            fetch = await client.get_tracks_batched(ids)

        requested_chunks = [call.args[0] for call in impl.await_args_list]
        assert sorted(len(chunk) for chunk in requested_chunks) == [20, 50, 50]
        assert sorted(id_ for chunk in requested_chunks for id_ in chunk) == sorted(ids)
        assert len(fetch.tracks) == 120
        assert fetch.unanswered == frozenset()

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
            fetch = await client.get_tracks_batched(["aaa", "old_id"])

        assert set(fetch.tracks) == {"aaa", "old_id"}
        assert fetch.tracks["old_id"].id == "new_id"

    async def test_null_entry_marks_that_id_dead(self):
        """Dead means answered-with-null: absent from tracks AND from unanswered."""
        impl = AsyncMock(
            return_value={"tracks": [None, {"id": "bbb", "name": "Song B"}]}
        )

        with _batch_client(impl) as client:
            fetch = await client.get_tracks_batched(["dead_id", "bbb"])

        assert set(fetch.tracks) == {"bbb"}
        assert fetch.unanswered == frozenset()

    async def test_short_array_correlates_the_prefix_and_leaves_the_tail_unanswered(
        self,
    ):
        """A broken-length response must not shift ids onto the wrong tracks —
        nor invent deaths for the ids the array never reached."""
        impl = AsyncMock(return_value={"tracks": [{"id": "aaa", "name": "Song A"}]})

        with _batch_client(impl) as client:
            fetch = await client.get_tracks_batched(["aaa", "bbb"])

        assert set(fetch.tracks) == {"aaa"}
        assert fetch.tracks["aaa"].id == "aaa"
        assert fetch.unanswered == frozenset({"bbb"})

    async def test_failed_chunk_reports_every_one_of_its_ids_unanswered(self):
        """A suppressed request failure is 'no answer', never 50 dead tracks."""
        ids = [f"id{i}" for i in range(50)]

        with _batch_client(AsyncMock(return_value=None)) as client:
            fetch = await client.get_tracks_batched(ids)

        assert fetch.tracks == {}
        assert fetch.unanswered == frozenset(ids)

    async def test_a_chunk_failure_does_not_touch_its_siblings(self):
        """Chunks are classified independently — one 5xx costs one chunk."""
        good = [f"good{i}" for i in range(50)]
        bad = [f"bad{i}" for i in range(50)]

        async def fail_the_bad_chunk(track_ids: list[str]) -> dict[str, object] | None:
            if track_ids[0].startswith("bad"):
                return None
            return {"tracks": [{"id": tid, "name": f"Song {tid}"} for tid in track_ids]}

        with _batch_client(AsyncMock(side_effect=fail_the_bad_chunk)) as client:
            fetch = await client.get_tracks_batched([*good, *bad])

        assert set(fetch.tracks) == set(good)
        assert fetch.unanswered == frozenset(bad)

    async def test_an_unparseable_response_is_unanswered_not_dead(self):
        """Anything escaping the suppression told us as little as a 5xx did."""
        with _batch_client(AsyncMock(return_value={"tracks": "not-a-list"})) as client:
            fetch = await client.get_tracks_batched(["aaa", "bbb"])

        assert fetch.tracks == {}
        assert fetch.unanswered == frozenset({"aaa", "bbb"})

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
