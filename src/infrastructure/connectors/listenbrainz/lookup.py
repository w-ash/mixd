"""ListenBrainz Labs Spotify-id resolution built on the shared client.

Lightweight utility (not a full connector) that resolves
(artist, release, track) metadata triples → Spotify track ids via the Labs
``spotify-id-from-metadata`` endpoint. Complements the Spotify search API
with an independent matching source based on MusicBrainz's linked data
graph. The endpoint requires all three fields, so callers without a release
name must skip the lookup entirely.
"""

from collections.abc import Sequence

from src.config import get_logger, settings
from src.infrastructure.connectors.listenbrainz.client import ListenBrainzAPIClient
from src.infrastructure.connectors.listenbrainz.models import SpotifyIdLookupQuery

logger = get_logger(__name__)

# (artist_name, release_name, track_name) — the endpoint's required fields.
type MetadataTriple = tuple[str, str, str]


class ListenBrainzLookup:
    """Targeted ListenBrainz Labs lookup for Spotify ID resolution."""

    _client: ListenBrainzAPIClient

    def __init__(self, client: ListenBrainzAPIClient | None = None) -> None:
        self._client = client if client is not None else ListenBrainzAPIClient()

    async def spotify_ids_from_metadata(
        self, triples: Sequence[MetadataTriple]
    ) -> dict[MetadataTriple, str]:
        """Resolve (artist, release, track) triples to Spotify track ids.

        Chunks at ``settings.api.listenbrainz.batch_size`` — one POST per
        chunk. The endpoint answers one row per query in request order, so
        rows pair with the chunk positionally and the result keys by the
        caller's original triple; the first entry of a row's
        ``spotify_track_ids`` wins.

        Returns:
            triple → bare Spotify track id for the triples that resolved.
            Misses are absent; a chunk whose call failed, or whose row count
            broke the one-row-per-query contract, contributes only misses —
            the lookup degrades, it never raises.
        """
        resolved: dict[MetadataTriple, str] = {}
        batch_size = settings.api.listenbrainz.batch_size
        for start in range(0, len(triples), batch_size):
            chunk = triples[start : start + batch_size]
            rows = await self._client.lookup_spotify_ids([
                SpotifyIdLookupQuery(
                    artist_name=artist, release_name=release, track_name=track
                )
                for artist, release, track in chunk
            ])
            if rows is None:
                logger.debug(
                    f"ListenBrainz lookup failed for a chunk of {len(chunk)} "
                    "triple(s); treating as misses"
                )
                continue
            if len(rows) != len(chunk):
                logger.warning(
                    f"ListenBrainz lookup answered {len(rows)} row(s) for "
                    f"{len(chunk)} query(ies); treating the chunk as misses"
                )
                continue
            for triple, row in zip(chunk, rows, strict=True):
                if row.spotify_track_ids:
                    resolved[triple] = row.spotify_track_ids[0]
        return resolved

    async def aclose(self) -> None:
        """Close the underlying API client."""
        await self._client.aclose()
