"""Tests for SpotifyAPIClient.check_library_contains.

Validates batching logic, error suppression (None fallback), and empty input handling
for the /me/library/contains endpoint wrapper. The pooled HTTP transport is the
stubbed boundary: each GET answers with a real ``httpx2.Response`` carrying the
endpoint's JSON array of booleans.
"""

from unittest.mock import AsyncMock

import httpx2

from src.infrastructure.connectors.spotify.client import SpotifyAPIClient


def _contains_response(status_code: int, body: object = None) -> httpx2.Response:
    """A real /me/library/contains response (a JSON array on 200)."""
    request = httpx2.Request("GET", "https://api.spotify.com/v1/me/library/contains")
    return httpx2.Response(status_code, json=body, request=request)


def _stub_transport(client: SpotifyAPIClient, *responses: httpx2.Response) -> AsyncMock:
    """Replace the pooled HTTP client; each GET returns the next response."""
    transport = AsyncMock()
    transport.get = AsyncMock(side_effect=list(responses))
    client._client = transport
    return transport


def _requested_uris(transport: AsyncMock) -> list[list[str]]:
    """The ``uris`` query of every GET, split back into lists."""
    return [
        call.kwargs["params"]["uris"].split(",")
        for call in transport.get.await_args_list
    ]


class TestCheckLibraryContainsHappyPath:
    """Successful API calls return correct URI→bool mappings."""

    async def test_single_batch_maps_uris_to_booleans(self, spotify_client):
        uris = ["spotify:track:aaa", "spotify:track:bbb", "spotify:track:ccc"]
        transport = _stub_transport(
            spotify_client, _contains_response(200, [True, False, True])
        )

        result = await spotify_client.check_library_contains(uris)

        assert result == {
            "spotify:track:aaa": True,
            "spotify:track:bbb": False,
            "spotify:track:ccc": True,
        }
        transport.get.assert_awaited_once_with(
            "/me/library/contains",
            params={"uris": "spotify:track:aaa,spotify:track:bbb,spotify:track:ccc"},
        )

    async def test_multiple_batches_when_exceeding_batch_size(self, spotify_client):
        """45 URIs split into GETs of 40 and 5, answers aligned per batch."""
        uris = [f"spotify:track:{i:03d}" for i in range(45)]
        transport = _stub_transport(
            spotify_client,
            _contains_response(200, [i % 2 == 0 for i in range(40)]),
            _contains_response(200, [True, False, True, False, True]),
        )

        result = await spotify_client.check_library_contains(uris)

        assert _requested_uris(transport) == [uris[:40], uris[40:]]
        assert result == {
            **{uris[i]: i % 2 == 0 for i in range(40)},
            **dict(zip(uris[40:], [True, False, True, False, True], strict=True)),
        }

    async def test_empty_input_returns_empty_dict(self, spotify_client):
        result = await spotify_client.check_library_contains([])

        assert result == {}


class TestCheckLibraryContainsErrorHandling:
    """API failures default to False (conservative pass-through)."""

    async def test_partial_batch_failure(self, spotify_client):
        """First batch succeeds, second fails and is suppressed — its URIs read False."""
        uris = [f"spotify:track:{i:03d}" for i in range(45)]
        _stub_transport(
            spotify_client,
            _contains_response(200, [True] * 40),
            _contains_response(503),
        )

        result = await spotify_client.check_library_contains(uris)

        assert result == {
            **dict.fromkeys(uris[:40], True),
            **dict.fromkeys(uris[40:], False),
        }
