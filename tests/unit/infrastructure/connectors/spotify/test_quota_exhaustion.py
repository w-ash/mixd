"""Quota-exhaustion surfacing through the Spotify client (PDR-003, v0.11.2).

A 429 whose body carries ``reason: "QUOTA_EXCEEDED"`` is the pooled
developer-account quota emptying out. The classifier already marks it
permanent (no retries) — these tests pin the next link: the client must
RAISE ``SpotifyQuotaExhaustedError`` instead of suppressing the
``HTTPStatusError`` to ``None``, and a multi-chunk batch must abort on the
first quota error rather than keep firing doomed calls. Plain 429s keep
today's suppressed-to-None behavior.
"""

import json

import httpx2
import pytest

from src.config.constants import SpotifyConstants
from src.domain.exceptions import SpotifyQuotaExhaustedError
from src.infrastructure.connectors.spotify.client import SpotifyAPIClient


def _make_429(json_body: object = None) -> httpx2.HTTPStatusError:
    request = httpx2.Request("GET", "https://api.spotify.com/v1/tracks")
    content = json.dumps(json_body).encode() if json_body is not None else b""
    response = httpx2.Response(429, content=content, request=request)
    return httpx2.HTTPStatusError("HTTP 429", request=request, response=response)


def _quota_429() -> httpx2.HTTPStatusError:
    return _make_429({"reason": "QUOTA_EXCEEDED"})


class TestClientRaisesOnQuota429:
    async def test_scalar_method_raises_instead_of_returning_none(
        self, spotify_client: SpotifyAPIClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _impl(*_args: object) -> None:
            raise _quota_429()

        monkeypatch.setattr(SpotifyAPIClient, "_search_track_impl", _impl)

        with pytest.raises(SpotifyQuotaExhaustedError):
            _ = await spotify_client.search_track("artist:x track:y")

    async def test_raised_error_names_pdr_003(
        self, spotify_client: SpotifyAPIClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _impl(*_args: object) -> None:
            raise _quota_429()

        monkeypatch.setattr(SpotifyAPIClient, "_search_track_impl", _impl)

        with pytest.raises(SpotifyQuotaExhaustedError, match="PDR-003"):
            _ = await spotify_client.search_track("artist:x track:y")

    async def test_plain_429_stays_suppressed_to_empty_result(
        self, spotify_client: SpotifyAPIClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _impl(*_args: object) -> None:
            raise _make_429()

        monkeypatch.setattr(SpotifyAPIClient, "_search_track_impl", _impl)

        assert await spotify_client.search_track("artist:x track:y") == []

    async def test_non_quota_429_body_stays_suppressed(
        self, spotify_client: SpotifyAPIClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _impl(*_args: object) -> None:
            raise _make_429({"reason": "SOMETHING_ELSE"})

        monkeypatch.setattr(SpotifyAPIClient, "_search_track_impl", _impl)

        assert await spotify_client.search_track("artist:x track:y") == []


class TestBatchAbortsOnQuota:
    """A chunked fetch must stop on the first quota error — the remaining
    chunks are doomed identically, and reporting the ids "unanswered" would
    hide the outage from every caller."""

    async def test_get_tracks_batched_raises_bare_error(
        self, spotify_client: SpotifyAPIClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Two chunks' worth of ids: the failure must surface as the bare
        # typed exception (not an ExceptionGroup, not an unanswered set).
        track_ids = [f"id-{i}" for i in range(SpotifyConstants.TRACKS_BATCH_SIZE * 2)]

        async def _impl(*_args: object) -> None:
            raise _quota_429()

        monkeypatch.setattr(SpotifyAPIClient, "_get_tracks_batch_impl", _impl)

        with pytest.raises(SpotifyQuotaExhaustedError):
            _ = await spotify_client.get_tracks_batched(track_ids)

    async def test_plain_429_batch_still_degrades_to_unanswered(
        self, spotify_client: SpotifyAPIClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _impl(*_args: object) -> None:
            raise _make_429()

        monkeypatch.setattr(SpotifyAPIClient, "_get_tracks_batch_impl", _impl)

        fetch = await spotify_client.get_tracks_batched(["only-id"])

        assert fetch.tracks == {}
        assert fetch.unanswered == frozenset({"only-id"})
