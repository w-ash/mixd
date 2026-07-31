"""Tests for SpotifyAPIClient.get_recently_played (v0.10.1).

Covers response-model validation, cursor/limit parameter passing, and the
50-item clamp — the endpoint's hard ceiling, which is also why this reader is
deliberately a single request rather than a paginate-to-completion loop.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from src.infrastructure.connectors.spotify.client import SpotifyAPIClient

_PLAYED_AT = "2026-07-20T12:00:00.123Z"


def _payload(*, after: str | None = "1753012800000") -> dict:
    return {
        "items": [
            {
                "track": {
                    "id": "4iV5W9uYEdYUVa79Axb7Rh",
                    "name": "Creep",
                    "artists": [{"id": "a1", "name": "Radiohead"}],
                    "album": {"id": "al1", "name": "Pablo Honey"},
                    "duration_ms": 238640,
                },
                "played_at": _PLAYED_AT,
                "context": {"type": "playlist", "uri": "spotify:playlist:p1"},
            }
        ],
        "cursors": {"after": after, "before": None},
        "next": None,
        "limit": 50,
    }


class TestGetRecentlyPlayedParsing:
    async def test_payload_parses_into_typed_history_items(self, spotify_client):
        mock_impl = AsyncMock(return_value=_payload())

        with patch.object(SpotifyAPIClient, "_get_recently_played_impl", mock_impl):
            response = await spotify_client.get_recently_played()

        assert response is not None
        item = response.items[0]
        assert item.track.name == "Creep"
        assert item.track.artists[0].name == "Radiohead"
        assert item.played_at == datetime(2026, 7, 20, 12, 0, 0, 123000, tzinfo=UTC)
        assert item.context is not None
        assert item.context.type == "playlist"

    async def test_null_items_coerce_to_empty_list(self, spotify_client):
        """Spotify's Feb 2026 nullability relaxation reaches this endpoint too."""
        mock_impl = AsyncMock(return_value={"items": None, "cursors": {}})

        with patch.object(SpotifyAPIClient, "_get_recently_played_impl", mock_impl):
            response = await spotify_client.get_recently_played()

        assert response is not None
        assert response.items == []

    async def test_missing_context_is_tolerated(self, spotify_client):
        payload = _payload()
        del payload["items"][0]["context"]
        mock_impl = AsyncMock(return_value=payload)

        with patch.object(SpotifyAPIClient, "_get_recently_played_impl", mock_impl):
            response = await spotify_client.get_recently_played()

        assert response is not None
        assert response.items[0].context is None

    async def test_one_unusable_item_does_not_lose_the_whole_page(self, spotify_client):
        """A local file (null track.id) must cost one play, not the window.

        Spotify retains only the trailing ~50 plays, so whole-page validation
        failure would lose every other play in it unrecoverably. An entry with
        no track id could never resolve anyway (no spotify:track: URI).
        """
        payload = _payload()
        local_file = {
            "track": {"id": None, "name": "Home Recording", "artists": []},
            "played_at": "2026-07-20T11:00:00.000Z",
        }
        payload["items"] = [local_file, *payload["items"]]
        mock_impl = AsyncMock(return_value=payload)

        with patch.object(SpotifyAPIClient, "_get_recently_played_impl", mock_impl):
            response = await spotify_client.get_recently_played()

        assert response is not None
        assert [i.track.name for i in response.items] == ["Creep"]

    async def test_suppressed_failure_returns_none(self, spotify_client):
        """_SUPPRESS_ERRORS turns transport failures into None, not an exception."""
        mock_impl = AsyncMock(return_value=None)

        with patch.object(SpotifyAPIClient, "_get_recently_played_impl", mock_impl):
            assert await spotify_client.get_recently_played() is None


class TestGetRecentlyPlayedParams:
    async def test_cursor_and_limit_forwarded_to_impl(self, spotify_client):
        mock_impl = AsyncMock(return_value=_payload())

        with patch.object(SpotifyAPIClient, "_get_recently_played_impl", mock_impl):
            await spotify_client.get_recently_played(after_ms=1753012800000, limit=20)

        mock_impl.assert_awaited_once_with(1753012800000, 20)

    async def test_after_omitted_from_query_on_a_first_poll(self, spotify_client):
        """No cursor means "give me the whole retained window"."""
        http = AsyncMock()
        http.get = AsyncMock(return_value=_FakeResponse(_payload()))
        spotify_client._client = http

        await spotify_client._get_recently_played_impl(None, 50)

        assert http.get.await_args.kwargs["params"] == {"limit": 50}

    async def test_limit_clamped_to_endpoint_maximum(self, spotify_client):
        http = AsyncMock()
        http.get = AsyncMock(return_value=_FakeResponse(_payload()))
        spotify_client._client = http

        await spotify_client._get_recently_played_impl(None, 500)

        assert http.get.await_args.kwargs["params"]["limit"] == 50


class _FakeResponse:
    """Minimal httpx2.Response stand-in for the _impl-level query assertions."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status_code = 200
        self.content = b"{}"
        self.headers = {"content-type": "application/json"}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload
