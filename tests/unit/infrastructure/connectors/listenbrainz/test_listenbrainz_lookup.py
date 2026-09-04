"""Tests for ListenBrainz Labs Spotify-id resolution.

Validates the lookup's contract over the API client: triple-keyed results
read from the plural ``spotify_track_ids`` rows, echoed-field (not
positional) keying with case-insensitive matching, chunking at the
configured batch size, and degradation to misses when a chunk's call fails.
"""

from unittest.mock import AsyncMock

from src.config import settings
from src.infrastructure.connectors.listenbrainz.client import ListenBrainzAPIClient
from src.infrastructure.connectors.listenbrainz.lookup import ListenBrainzLookup
from src.infrastructure.connectors.listenbrainz.models import (
    SpotifyIdLookupQuery,
    SpotifyIdLookupResult,
)

_CREEP = ("Radiohead", "Pablo Honey", "Creep")
_BLISS = ("Muse", "Origin of Symmetry", "Bliss")


def _row(
    triple: tuple[str, str, str], spotify_track_ids: list[str]
) -> SpotifyIdLookupResult:
    artist, release, track = triple
    return SpotifyIdLookupResult(
        artist_name=artist,
        release_name=release,
        track_name=track,
        spotify_track_ids=spotify_track_ids,
    )


def _lookup_returning(
    *chunk_results: list[SpotifyIdLookupResult] | None,
) -> tuple[ListenBrainzLookup, AsyncMock]:
    """A lookup over a client double answering one result set per chunk."""
    client = AsyncMock(spec=ListenBrainzAPIClient)
    client.lookup_spotify_ids.side_effect = list(chunk_results)
    return ListenBrainzLookup(client=client), client


class TestSpotifyIdsFromMetadata:
    """Batch resolution from (artist, release, track) triples."""

    async def test_resolves_first_id_keyed_by_original_triple(self):
        lookup, _ = _lookup_returning([_row(_CREEP, ["abc123", "alt456"])])

        result = await lookup.spotify_ids_from_metadata([_CREEP])

        assert result == {_CREEP: "abc123"}

    async def test_empty_id_list_is_a_miss(self):
        lookup, _ = _lookup_returning([
            _row(_CREEP, ["abc123"]),
            _row(_BLISS, []),
        ])

        result = await lookup.spotify_ids_from_metadata([_CREEP, _BLISS])

        assert result == {_CREEP: "abc123"}

    async def test_rows_pair_with_queries_by_position(self):
        """The endpoint echoes one row per query in request order; the echo
        text is not the join key, so a server-normalized echo cannot strand
        a hit."""
        lookup, _ = _lookup_returning([
            _row(("radiohead", "pablo honey", "creep"), ["abc123"]),
            _row(("MUSE", "Origin of Symmetry", "Bliss"), ["muse1"]),
        ])

        result = await lookup.spotify_ids_from_metadata([_CREEP, _BLISS])

        assert result == {_CREEP: "abc123", _BLISS: "muse1"}

    async def test_row_count_mismatch_degrades_the_chunk_to_misses(self):
        """A broken one-row-per-query contract is not silently misattributed:
        the short chunk yields nothing and the next chunk still resolves."""
        batch_size = settings.api.listenbrainz.batch_size
        filler = [
            (f"Artist {n}", f"Album {n}", f"Track {n}") for n in range(batch_size)
        ]
        lookup, _ = _lookup_returning(
            [_row(filler[0], ["stray1"])],  # one row for a full chunk of queries
            [_row(_BLISS, ["muse1"])],
        )

        result = await lookup.spotify_ids_from_metadata([*filler, _BLISS])

        assert result == {_BLISS: "muse1"}

    async def test_chunks_at_the_configured_batch_size(self):
        batch_size = settings.api.listenbrainz.batch_size
        assert batch_size == 50
        triples = [(f"Artist {n}", f"Album {n}", f"Track {n}") for n in range(60)]

        async def _echo_all(
            queries: list[SpotifyIdLookupQuery],
        ) -> list[SpotifyIdLookupResult]:
            return [
                SpotifyIdLookupResult(
                    artist_name=query.artist_name,
                    release_name=query.release_name,
                    track_name=query.track_name,
                    spotify_track_ids=[f"id-{query.track_name}"],
                )
                for query in queries
            ]

        client = AsyncMock(spec=ListenBrainzAPIClient)
        client.lookup_spotify_ids.side_effect = _echo_all
        lookup = ListenBrainzLookup(client=client)

        result = await lookup.spotify_ids_from_metadata(triples)

        assert client.lookup_spotify_ids.await_count == 2
        chunk_sizes = [
            len(call.args[0]) for call in client.lookup_spotify_ids.await_args_list
        ]
        assert chunk_sizes == [50, 10]
        assert len(result) == 60
        assert result["Artist 59", "Album 59", "Track 59"] == "id-Track 59"

    async def test_failed_call_resolves_nothing(self):
        """The client suppresses transport/HTTP/shape failures to None — the
        lookup reads that as all-miss, never raises."""
        lookup, _ = _lookup_returning(None)

        assert await lookup.spotify_ids_from_metadata([_CREEP]) == {}

    async def test_one_failed_chunk_degrades_only_its_own_triples(self):
        triples = [(f"Artist {n}", f"Album {n}", f"Track {n}") for n in range(51)]
        lookup, _ = _lookup_returning(
            None, [_row(("Artist 50", "Album 50", "Track 50"), ["tail1"])]
        )

        result = await lookup.spotify_ids_from_metadata(triples)

        assert result == {("Artist 50", "Album 50", "Track 50"): "tail1"}

    async def test_empty_triples_skip_the_request(self):
        lookup, client = _lookup_returning()

        assert await lookup.spotify_ids_from_metadata([]) == {}
        client.lookup_spotify_ids.assert_not_awaited()
