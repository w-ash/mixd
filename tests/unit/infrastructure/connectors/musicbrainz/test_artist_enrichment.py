"""MusicBrainz artist records converted to the domain enrichment shape.

The url-rel parsing carries the weight here: those relations are what seed the
first ``connector_artists`` rows for Apple, Tidal and Discogs, and each service
buries its id in a different path shape. Fixture bodies are trimmed copies of
real responses — Caribou's several Spotify relations and STRFKR's alias-only
entry are the two cases the census singled out.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.matching.artist_enrichment import ArtistLookup
from src.infrastructure.connectors.musicbrainz.artist_enrichment import (
    MusicBrainzArtistEnrichmentProvider,
    artist_kind_of,
    parse_url_rel,
    to_artist_lookup,
)
from src.infrastructure.connectors.musicbrainz.models import MusicBrainzArtist

CARIBOU_MBID = "e5c7b94f-e264-473c-bb0f-37c85d4d5c70"

CARIBOU_BODY: dict[str, Any] = {
    "id": CARIBOU_MBID,
    "name": "Caribou",
    "type": "Person",
    "disambiguation": "Canadian electronic musician Dan Snaith",
    "aliases": [
        {"name": "Manitoba", "sort-name": "Manitoba", "type": "Artist name"},
        {"name": "Daphni", "sort-name": "Daphni", "type": "Artist name"},
    ],
    "relations": [
        {
            "type": "streaming",
            "url": {
                "resource": "https://open.spotify.com/artist/4aXXDGiKAJgtBOePcPAtFU"
            },
        },
        {
            "type": "free streaming",
            "url": {
                "resource": "https://open.spotify.com/artist/2vmiuLJTtBiNFsdGJViMiP"
            },
        },
        {
            "type": "discogs",
            "url": {"resource": "https://www.discogs.com/artist/50043-Caribou"},
        },
        {
            "type": "streaming",
            "url": {"resource": "https://music.apple.com/us/artist/caribou/1258529"},
        },
        {
            "type": "streaming",
            "url": {"resource": "https://tidal.com/browse/artist/3528325"},
        },
        {
            "type": "last.fm",
            "url": {"resource": "https://www.last.fm/music/Caribou"},
        },
        {
            "type": "wikidata",
            "url": {"resource": "https://www.wikidata.org/wiki/Q1035463"},
        },
    ],
}

# STRFKR is only reachable through its alias — the search that found it
# returned nothing for the fielded artist query.
STRFKR_BODY: dict[str, Any] = {
    "id": "d368baa8-21ca-4759-9731-0b2753071ad8",
    "name": "Starfucker",
    "type": "Group",
    "score": 100,
    "aliases": [
        {"name": "STRFKR", "sort-name": "STRFKR", "primary": True, "locale": "en"}
    ],
}


def _artist(body: dict[str, Any]) -> MusicBrainzArtist:
    return MusicBrainzArtist.model_validate(body)


class TestParseUrlRel:
    @pytest.mark.parametrize(
        ("url", "service", "identifier"),
        [
            (
                "https://open.spotify.com/artist/4aXXDGiKAJgtBOePcPAtFU",
                "spotify",
                "4aXXDGiKAJgtBOePcPAtFU",
            ),
            ("https://www.discogs.com/artist/50043-Caribou", "discogs", "50043"),
            ("https://www.discogs.com/artist/50043", "discogs", "50043"),
            ("https://music.apple.com/us/artist/caribou/1258529", "apple", "1258529"),
            ("https://music.apple.com/gb/artist/teed/419758287", "apple", "419758287"),
            ("https://tidal.com/browse/artist/3528325", "tidal", "3528325"),
            ("https://listen.tidal.com/artist/3528325", "tidal", "3528325"),
            ("https://www.last.fm/music/Caribou", "lastfm", "Caribou"),
            (
                "https://www.last.fm/music/Boards+of+Canada",
                "lastfm",
                "Boards of Canada",
            ),
            (
                "https://www.last.fm/music/Sigur%20R%C3%B3s",
                "lastfm",
                "Sigur Rós",
            ),
        ],
    )
    def test_known_services_yield_their_identifier(
        self, url: str, service: str, identifier: str
    ):
        rel = parse_url_rel(url)
        assert rel is not None
        assert (rel.service, rel.identifier) == (service, identifier)
        assert rel.url == url

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.wikidata.org/wiki/Q1035463",
            "https://caribou.fm/",
            "https://soundcloud.com/caribou",
            "https://open.spotify.com/album/4aXXDGiKAJgtBOePcPAtFU",
            "https://music.apple.com/us/artist/caribou",
            "not a url at all",
            "",
        ],
    )
    def test_unmodelled_or_malformed_targets_yield_nothing(self, url: str):
        assert parse_url_rel(url) is None


class TestArtistKind:
    @pytest.mark.parametrize(
        ("mb_type", "expected"),
        [
            ("Person", "person"),
            ("Group", "group"),
            ("Orchestra", "group"),
            ("Choir", "group"),
            ("Character", "other"),
            ("Other", "other"),
            (None, None),
            ("", None),
        ],
    )
    def test_musicbrainz_types_map_to_the_domain_vocabulary(
        self, mb_type: str | None, expected: str | None
    ):
        assert artist_kind_of(mb_type) == expected


class TestToArtistLookup:
    def test_every_modelled_service_survives_the_conversion(self):
        lookup = to_artist_lookup(_artist(CARIBOU_BODY))

        assert lookup.mbid == CARIBOU_MBID
        assert lookup.kind == "person"
        assert lookup.disambiguation is not None
        assert [rel.service for rel in lookup.url_rels] == [
            "spotify",
            "spotify",
            "discogs",
            "apple",
            "tidal",
            "lastfm",
        ]

    def test_several_spotify_relations_are_all_kept(self):
        # One artist really does carry more than one Spotify id; collapsing
        # them here would silently drop a mapping the seeding step needs.
        lookup = to_artist_lookup(_artist(CARIBOU_BODY))
        spotify = [
            rel.identifier for rel in lookup.url_rels if rel.service == "spotify"
        ]
        assert spotify == ["4aXXDGiKAJgtBOePcPAtFU", "2vmiuLJTtBiNFsdGJViMiP"]

    def test_aliases_carry_their_type_and_primary_flag(self):
        lookup = to_artist_lookup(_artist(STRFKR_BODY))

        assert lookup.kind == "group"
        assert len(lookup.aliases) == 1
        alias = lookup.aliases[0]
        assert (alias.name, alias.locale, alias.is_primary) == ("STRFKR", "en", True)
        assert lookup.score == 100

    def test_alias_names_lead_with_the_primary_name(self):
        lookup = to_artist_lookup(_artist(CARIBOU_BODY))
        assert lookup.alias_names() == ("Caribou", "Manitoba", "Daphni")

    def test_an_artist_with_no_relations_converts_cleanly(self):
        lookup = to_artist_lookup(_artist({"id": CARIBOU_MBID, "name": "Caribou"}))
        assert lookup.url_rels == ()
        assert lookup.kind is None
        assert lookup.disambiguation is None


class TestProvider:
    async def test_search_returns_domain_records(self):
        client = MagicMock()
        client.search_artist = AsyncMock(return_value=[_artist(STRFKR_BODY)])
        provider = MusicBrainzArtistEnrichmentProvider(client=client)

        results = await provider.search_artist("STRFKR")

        assert [type(result) for result in results] == [ArtistLookup]
        assert results[0].alias_names() == ("Starfucker", "STRFKR")

    async def test_lookup_converts_the_client_record(self):
        client = MagicMock()
        client.get_artist = AsyncMock(return_value=_artist(CARIBOU_BODY))
        provider = MusicBrainzArtistEnrichmentProvider(client=client)

        lookup = await provider.lookup_artist(CARIBOU_MBID)

        assert lookup is not None
        assert lookup.mbid == CARIBOU_MBID

    async def test_an_unresolved_mbid_reads_as_nothing(self):
        client = MagicMock()
        client.get_artist = AsyncMock(return_value=None)
        provider = MusicBrainzArtistEnrichmentProvider(client=client)

        assert await provider.lookup_artist("missing") is None
