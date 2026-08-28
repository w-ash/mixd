"""Tests for ListenBrainzAPIClient.

Validates the real Labs contract at the HTTP boundary: three-field request
bodies (artist_name, release_name, track_name — the endpoint 400s without
all three), the plural ``spotify_track_ids`` response rows, and suppression
of transport/shape failures to ``None``.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest

from src.infrastructure.connectors.listenbrainz.client import ListenBrainzAPIClient
from src.infrastructure.connectors.listenbrainz.models import (
    SpotifyIdLookupQuery,
    SpotifyIdLookupResult,
)

_QUERY = SpotifyIdLookupQuery(
    artist_name="Radiohead", release_name="Pablo Honey", track_name="Creep"
)


def _http_returning(payload: object) -> AsyncMock:
    """An httpx2 double whose POST answers with the given JSON payload."""
    http = AsyncMock(spec=httpx2.AsyncClient)
    response = MagicMock(spec=httpx2.Response)
    response.headers = httpx2.Headers()
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    http.post.return_value = response
    return http


def _make_client(
    monkeypatch: pytest.MonkeyPatch, http: AsyncMock
) -> ListenBrainzAPIClient:
    """Build a client whose pooled httpx2 client is the given double."""
    monkeypatch.setattr(
        "src.infrastructure.connectors.listenbrainz.client.make_listenbrainz_client",
        lambda base_url: http,
    )
    return ListenBrainzAPIClient()


class TestLookupSpotifyIds:
    async def test_posts_all_three_required_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The endpoint 400s unless every query carries artist, release, AND
        track — the exact body shape is the contract under test."""
        http = _http_returning([])
        client = _make_client(monkeypatch, http)

        _ = await client.lookup_spotify_ids([
            _QUERY,
            SpotifyIdLookupQuery(
                artist_name="Muse",
                release_name="Origin of Symmetry",
                track_name="Bliss",
            ),
        ])

        http.post.assert_awaited_once_with(
            "/spotify-id-from-metadata/json",
            json=[
                {
                    "artist_name": "Radiohead",
                    "release_name": "Pablo Honey",
                    "track_name": "Creep",
                },
                {
                    "artist_name": "Muse",
                    "release_name": "Origin of Symmetry",
                    "track_name": "Bliss",
                },
            ],
        )

    async def test_parses_plural_id_rows_and_empty_list_misses(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        rows = [
            {
                "artist_name": "Radiohead",
                "release_name": "Pablo Honey",
                "track_name": "Creep",
                "spotify_track_ids": ["abc123", "alt456"],
            },
            {
                "artist_name": "Muse",
                "release_name": "Origin of Symmetry",
                "track_name": "Bliss",
                "spotify_track_ids": [],
            },
        ]
        client = _make_client(monkeypatch, _http_returning(rows))

        result = await client.lookup_spotify_ids([_QUERY])

        assert result == [
            SpotifyIdLookupResult(
                artist_name="Radiohead",
                release_name="Pablo Honey",
                track_name="Creep",
                spotify_track_ids=["abc123", "alt456"],
            ),
            SpotifyIdLookupResult(
                artist_name="Muse",
                release_name="Origin of Symmetry",
                track_name="Bliss",
                spotify_track_ids=[],
            ),
        ]

    async def test_empty_queries_skip_the_request(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        http = _http_returning([])
        client = _make_client(monkeypatch, http)

        assert await client.lookup_spotify_ids([]) == []
        http.post.assert_not_awaited()

    async def test_http_error_suppresses_to_none(self, monkeypatch: pytest.MonkeyPatch):
        """A 400 (what the old two-field bodies earned on every call) is
        permanent — no retry — and suppresses to None."""
        http = AsyncMock(spec=httpx2.AsyncClient)
        response = MagicMock(spec=httpx2.Response)
        response.headers = httpx2.Headers()
        response.status_code = 400
        response.raise_for_status.side_effect = httpx2.HTTPStatusError(
            "Bad Request", request=MagicMock(), response=response
        )
        http.post.return_value = response
        client = _make_client(monkeypatch, http)

        assert await client.lookup_spotify_ids([_QUERY]) is None
        http.post.assert_awaited_once()

    async def test_non_list_body_suppresses_to_none(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        client = _make_client(monkeypatch, _http_returning({"error": "nope"}))

        assert await client.lookup_spotify_ids([_QUERY]) is None

    async def test_malformed_row_suppresses_to_none(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A row without the echoed query fields fails boundary validation."""
        client = _make_client(
            monkeypatch, _http_returning([{"spotify_track_ids": "not-a-list"}])
        )

        assert await client.lookup_spotify_ids([_QUERY]) is None
