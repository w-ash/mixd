"""The artist search and lookup endpoints of the MusicBrainz client.

The query shape is the point of the search test: the census found the fielded
``artist:`` search ranks tribute bands above a renamed primary and returns
nothing for "STRFKR", whose only MusicBrainz entry is an alias — so the query
has to cover ``alias:`` too, and a regression to ``artist:`` alone must fail
here rather than quietly halve the hit rate.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest

from src.infrastructure.connectors._shared import rate_limiting
from src.infrastructure.connectors.musicbrainz.client import MusicBrainzAPIClient

STRFKR_MBID = "d368baa8-21ca-4759-9731-0b2753071ad8"

SEARCH_BODY: dict[str, Any] = {
    "artists": [
        {
            "id": STRFKR_MBID,
            "name": "Starfucker",
            "type": "Group",
            "disambiguation": "US indie band",
            "score": 100,
            "aliases": [{"name": "STRFKR", "sort-name": "STRFKR"}],
        },
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "name": "Starfucker Tribute",
            "score": 58,
        },
    ]
}

LOOKUP_BODY: dict[str, Any] = {
    "id": STRFKR_MBID,
    "name": "Starfucker",
    "type": "Group",
    "aliases": [
        {
            "name": "STRFKR",
            "sort-name": "STRFKR",
            "type": "Artist name",
            "locale": "en",
            "primary": True,
        }
    ],
    "relations": [
        {
            "type": "streaming",
            "url": {
                "resource": "https://open.spotify.com/artist/2Tz1DTzVJ5Gyh8ZwVr6ekU"
            },
        }
    ],
}


def _response(body: dict[str, Any]) -> MagicMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=body)
    return response


def _client_with(body: dict[str, Any]) -> tuple[MusicBrainzAPIClient, AsyncMock]:
    client = MusicBrainzAPIClient()
    get = AsyncMock(return_value=_response(body))
    client._client = MagicMock(get=get)  # pyright: ignore[reportPrivateUsage]
    return client, get


class TestSearchArtist:
    async def test_the_query_covers_both_artist_and_alias_fields(self):
        client, get = _client_with(SEARCH_BODY)

        _ = await client._search_artist_impl("STRFKR", 5)  # pyright: ignore[reportPrivateUsage]

        params = get.await_args.kwargs["params"]
        assert params["query"] == 'artist:"STRFKR" OR alias:"STRFKR"'
        assert params["limit"] == "5"
        assert get.await_args.args[0] == "/artist"

    async def test_hits_are_parsed_with_their_relevance_score(self):
        client, _ = _client_with(SEARCH_BODY)

        results = await client._search_artist_impl("STRFKR", 5)  # pyright: ignore[reportPrivateUsage]

        assert [artist.id for artist in results] == [
            STRFKR_MBID,
            "00000000-0000-0000-0000-000000000001",
        ]
        assert results[0].score == 100
        assert results[0].aliases[0].name == "STRFKR"
        assert results[1].score == 58

    async def test_a_quoted_name_cannot_break_out_of_the_lucene_phrase(self):
        client, get = _client_with(SEARCH_BODY)

        _ = await client._search_artist_impl('The "Band"', 5)  # pyright: ignore[reportPrivateUsage]

        assert get.await_args.kwargs["params"]["query"] == (
            'artist:"The \\"Band\\"" OR alias:"The \\"Band\\""'
        )

    async def test_an_empty_name_never_reaches_the_api(self):
        client, get = _client_with(SEARCH_BODY)

        assert await client._search_artist_impl("", 5) == []  # pyright: ignore[reportPrivateUsage]
        get.assert_not_awaited()

    async def test_a_body_without_artists_yields_no_hits(self):
        client, _ = _client_with({})

        assert await client._search_artist_impl("STRFKR", 5) == []  # pyright: ignore[reportPrivateUsage]

    async def test_a_suppressed_request_failure_reads_as_no_hits(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # A 400 is permanent (no retry) and suppressed to None by the base
        # client; an empty list is the only thing a caller can act on.
        # A fresh limiter cache keeps the 1 req/s bucket full for this call.
        monkeypatch.setattr(rate_limiting, "_LIMITERS", {})
        request = httpx2.Request("GET", "https://musicbrainz.org/ws/2/artist")
        client = MusicBrainzAPIClient()
        get = AsyncMock(return_value=httpx2.Response(400, request=request))
        client._client = MagicMock(get=get)  # pyright: ignore[reportPrivateUsage]

        assert await client.search_artist("STRFKR") == []
        get.assert_awaited_once()


class TestGetArtist:
    async def test_the_lookup_asks_for_aliases_and_url_rels(self):
        client, get = _client_with(LOOKUP_BODY)

        _ = await client._get_artist_impl(STRFKR_MBID)  # pyright: ignore[reportPrivateUsage]

        assert get.await_args.args[0] == f"/artist/{STRFKR_MBID}"
        assert get.await_args.kwargs["params"]["inc"] == "aliases+url-rels"

    async def test_the_artist_carries_its_aliases_and_relations(self):
        client, _ = _client_with(LOOKUP_BODY)

        artist = await client._get_artist_impl(STRFKR_MBID)  # pyright: ignore[reportPrivateUsage]

        assert artist is not None
        assert artist.type == "Group"
        assert artist.aliases[0].primary is True
        assert artist.aliases[0].locale == "en"
        assert artist.relations[0].url is not None

    async def test_a_body_without_an_id_is_no_artist(self):
        client, _ = _client_with({"error": "Not Found"})

        assert await client._get_artist_impl(STRFKR_MBID) is None  # pyright: ignore[reportPrivateUsage]

    async def test_an_empty_mbid_never_reaches_the_api(self):
        client, get = _client_with(LOOKUP_BODY)

        assert await client._get_artist_impl("") is None  # pyright: ignore[reportPrivateUsage]
        get.assert_not_awaited()
