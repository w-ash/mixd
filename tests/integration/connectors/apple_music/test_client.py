"""Apple Music API client behavior at the transport boundary (MockTransport).

Covers: developer-token bearer on every request, Music User Token only on
/v1/me/* calls, actionable auth failures (401 = developer token, no reauth
marker; /v1/me 403 = rejected user token with the stored reauth marker;
catalog 403 = instance problem, no marker), retry policy behavior on 5xx,
filter[isrc] chunking with merged results and failed-chunk surfacing, and
recently-played offset passthrough.
"""

import httpx2
import pytest

from src.config import settings
from src.domain.exceptions import AppleMusicAuthRequiredError
from tests.integration.connectors.apple_music.conftest import (
    DEV_TOKEN,
    MUSIC_USER_TOKEN,
    FakeTokenStorage,
    invalid_mut_error_body,
    song_payload,
    songs_response,
    storefront_response,
)


def recording(handler):
    """Wrap a handler so every request is captured for assertions."""
    requests: list[httpx2.Request] = []

    def _handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return handler(request)

    return _handler, requests


def routed_handler(request: httpx2.Request) -> httpx2.Response:
    """Canned happy-path responses for the endpoints under test."""
    path = request.url.path
    if path == "/v1/me/storefront":
        return httpx2.Response(200, json=storefront_response("us"))
    if path == "/v1/me/recent/played/tracks":
        return httpx2.Response(
            200,
            json={
                "data": [song_payload("recent-1")],
                "next": "/v1/me/recent/played/tracks?offset=30",
            },
        )
    if path.startswith("/v1/catalog/"):
        return httpx2.Response(200, json=songs_response("catalog-1"))
    return httpx2.Response(404, json={"errors": []})


class TestAuthHeaders:
    async def test_every_request_carries_developer_bearer_token(self, make_client):
        handler, requests = recording(routed_handler)
        client = make_client(handler)

        _ = await client.get_songs_by_ids("us", ["catalog-1"])
        _ = await client.get_storefront()
        _ = await client.get_recently_played()

        assert len(requests) == 3
        for request in requests:
            assert request.headers["Authorization"] == f"Bearer {DEV_TOKEN}"

    async def test_me_requests_carry_music_user_token(self, make_client):
        handler, requests = recording(routed_handler)
        client = make_client(handler)

        storefront = await client.get_storefront()
        _ = await client.get_recently_played()

        assert storefront is not None
        assert storefront.id == "us"
        for request in requests:
            assert request.headers["Music-User-Token"] == MUSIC_USER_TOKEN

    async def test_catalog_requests_do_not_require_music_user_token(
        self, make_client, storage: FakeTokenStorage
    ):
        # No stored MUT at all: catalog calls must still work.
        storage.token = None
        handler, requests = recording(routed_handler)
        client = make_client(handler)

        result = await client.get_songs_by_ids("us", ["catalog-1"])

        assert [song.id for song in result.songs] == ["catalog-1"]
        assert "Music-User-Token" not in requests[0].headers
        assert storage.loads == []

    async def test_me_call_without_stored_token_raises_before_any_request(
        self, make_client, storage: FakeTokenStorage
    ):
        storage.token = None
        handler, requests = recording(routed_handler)
        client = make_client(handler)

        with pytest.raises(AppleMusicAuthRequiredError):
            _ = await client.get_storefront()

        assert requests == []


class TestAuthFailures:
    async def test_401_raises_developer_token_error_without_reauth_marker(
        self, make_client, storage: FakeTokenStorage
    ):
        # 401 rejects the developer token — an instance problem the user
        # cannot fix, so no retry and NO reauth_required marker is written.
        handler, requests = recording(
            lambda _request: httpx2.Response(
                401, json={"errors": [{"title": "Unauthorized", "status": "401"}]}
            )
        )
        client = make_client(handler)

        with pytest.raises(AppleMusicAuthRequiredError, match="developer token"):
            _ = await client.get_songs_by_ids("us", ["catalog-1"])

        assert len(requests) == 1
        assert storage.saved == []

    async def test_403_with_real_invalid_mut_body_raises_and_marks_reauth(
        self, make_client, storage: FakeTokenStorage
    ):
        # The verbatim live rejection body: code 40300 / "Invalid
        # authentication" — no token-marker string to pattern-match.
        handler, requests = recording(
            lambda _request: httpx2.Response(403, json=invalid_mut_error_body())
        )
        client = make_client(handler)

        with pytest.raises(AppleMusicAuthRequiredError):
            _ = await client.get_storefront()

        assert len(requests) == 1
        assert storage.token is not None
        extra_data = storage.token.get("extra_data")
        assert extra_data is not None
        assert extra_data["reauth_required"] is True

    async def test_403_without_parseable_body_still_marks_reauth(
        self, make_client, storage: FakeTokenStorage
    ):
        # ANY 403 counts as a user-token rejection — Apple uses 401 for
        # developer-token problems, so a bare 403 is not innocent.
        handler, _requests = recording(
            lambda _request: httpx2.Response(403, text="Forbidden")
        )
        client = make_client(handler)

        with pytest.raises(AppleMusicAuthRequiredError):
            _ = await client.get_storefront()

        assert storage.token is not None
        extra_data = storage.token.get("extra_data")
        assert extra_data is not None
        assert extra_data["reauth_required"] is True

    async def test_catalog_403_is_not_reauth_and_writes_no_marker(
        self, make_client, storage: FakeTokenStorage
    ):
        # The catalog surface carries no Music User Token, so a 403 there
        # cannot mean "re-authorize": no AppleMusicAuthRequiredError, no
        # reauth marker — the chunk fails and is reported as failed values.
        handler, requests = recording(
            lambda _request: httpx2.Response(403, json=invalid_mut_error_body())
        )
        client = make_client(handler)

        result = await client.get_songs_by_ids("us", ["catalog-1"])

        assert result.songs == []
        assert result.failed_values == ["catalog-1"]
        assert storage.saved == []
        # Permanent classification: no retry churn on a catalog 403.
        assert len(requests) == 1

    async def test_reauth_marker_write_failure_does_not_mask_auth_error(
        self, make_client, storage: FakeTokenStorage
    ):
        storage.fail_saves = True
        handler, _requests = recording(
            lambda _request: httpx2.Response(403, json=invalid_mut_error_body())
        )
        client = make_client(handler)

        with pytest.raises(AppleMusicAuthRequiredError):
            _ = await client.get_storefront()

        assert storage.saved == []


class TestRetryBehavior:
    async def test_5xx_retried_per_policy_then_suppressed(self, make_client):
        handler, requests = recording(
            lambda _request: httpx2.Response(500, json={"errors": []})
        )
        client = make_client(handler)

        result = await client.get_songs_by_ids("us", ["catalog-1"])

        assert result.songs == []
        assert result.failed_values == ["catalog-1"]
        assert len(requests) == settings.api.apple_music.retry_count

    async def test_5xx_surfaces_classified_when_not_suppressed(self, make_client):
        handler, requests = recording(
            lambda _request: httpx2.Response(500, json={"errors": []})
        )
        client = make_client(handler)

        with pytest.raises(httpx2.HTTPStatusError):
            _ = await client._api_call(
                "probe_apple_music_5xx",
                client._get_catalog_songs_impl,
                "us",
                "ids",
                ["catalog-1"],
                suppress=(),
            )

        assert len(requests) == settings.api.apple_music.retry_count


class TestChunking:
    async def test_isrc_lookup_chunks_at_25_and_merges(self, make_client):
        def echo_isrcs(request: httpx2.Request) -> httpx2.Response:
            codes = request.url.params["filter[isrc]"].split(",")
            return httpx2.Response(200, json=songs_response(*codes))

        handler, requests = recording(echo_isrcs)
        client = make_client(handler)
        isrcs = [f"ISRC{i:04d}" for i in range(30)]

        result = await client.get_songs_by_isrc("us", isrcs)

        assert len(requests) == 2
        first_codes = requests[0].url.params["filter[isrc]"].split(",")
        second_codes = requests[1].url.params["filter[isrc]"].split(",")
        assert len(first_codes) == 25
        assert len(second_codes) == 5
        assert [song.id for song in result.songs] == isrcs
        assert result.failed_values == []
        assert requests[0].url.path == "/v1/catalog/us/songs"

    async def test_equivalents_lookup_chunks_at_300(self, make_client):
        def echo_ids(request: httpx2.Request) -> httpx2.Response:
            ids = request.url.params["filter[equivalents]"].split(",")
            return httpx2.Response(200, json=songs_response(*ids))

        handler, requests = recording(echo_ids)
        client = make_client(handler)
        ids = [str(i) for i in range(301)]

        result = await client.get_song_equivalents("us", ids)

        assert len(requests) == 2
        assert len(requests[0].url.params["filter[equivalents]"].split(",")) == 300
        assert len(result.songs) == 301

    async def test_failed_chunk_surfaces_its_values_and_spares_the_rest(
        self, make_client
    ):
        """One chunk failing after retries must not read as 'those ISRCs do
        not exist' — the failed chunk's input values are reported."""

        def first_chunk_fails(request: httpx2.Request) -> httpx2.Response:
            codes = request.url.params["filter[isrc]"].split(",")
            if "ISRC0000" in codes:
                return httpx2.Response(500, json={"errors": []})
            return httpx2.Response(200, json=songs_response(*codes))

        handler, _requests = recording(first_chunk_fails)
        client = make_client(handler)
        isrcs = [f"ISRC{i:04d}" for i in range(30)]

        result = await client.get_songs_by_isrc("us", isrcs)

        assert result.failed_values == isrcs[:25]
        assert [song.id for song in result.songs] == isrcs[25:]


class TestRecentlyPlayed:
    async def test_offset_and_fixed_params_are_passed(self, make_client):
        handler, requests = recording(routed_handler)
        client = make_client(handler)

        page = await client.get_recently_played(offset=60)

        assert page is not None
        assert [song.id for song in page.data] == ["recent-1"]
        params = requests[0].url.params
        assert requests[0].url.path == "/v1/me/recent/played/tracks"
        assert params["types"] == "songs"
        assert params["limit"] == "30"
        assert params["offset"] == "60"
