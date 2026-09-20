"""MusicBrainz as an artist-enrichment source.

Adapts :class:`MusicBrainzAPIClient` to
:class:`~src.domain.matching.artist_enrichment.ArtistEnrichmentProviderProtocol`:
aliases for the equivalence cache, and ``url-rels`` for the external ids that
seed connector artist rows. The census found an MBID on 51/51 sampled artists
and Spotify/Discogs ids on 51/51 and 51/51 of them, Apple and Tidal on 50/51 —
one lookup at 1 req/s replaces four name searches.

Pacing and 503 backoff are the client's; nothing here retries.
"""

from collections.abc import Mapping, Sequence
from typing import Final
from urllib.parse import unquote_plus, urlparse

from attrs import define

from src.config import get_logger
from src.domain.entities.artist import ArtistKind
from src.domain.matching.artist_enrichment import (
    ArtistAliasRecord,
    ArtistLookup,
    ArtistUrlRel,
)
from src.infrastructure.connectors.musicbrainz.client import MusicBrainzAPIClient
from src.infrastructure.connectors.musicbrainz.models import (
    MusicBrainzAlias,
    MusicBrainzArtist,
)

logger = get_logger(__name__).bind(service="musicbrainz_artist_enrichment")

# MusicBrainz ``type`` → the three buckets an artist page acts on. An
# orchestra or a choir is a group of people, so both read as "group"; every
# other value MusicBrainz uses ("Character", "Other") reads as "other".
_ARTIST_KINDS: Final[Mapping[str, ArtistKind]] = {
    "person": "person",
    "group": "group",
    "orchestra": "group",
    "choir": "group",
}

# Hosts that publish an artist id in a path segment after "artist".
_SPOTIFY_HOSTS: Final[frozenset[str]] = frozenset({"open.spotify.com"})
_DISCOGS_HOSTS: Final[frozenset[str]] = frozenset({"discogs.com"})
_APPLE_HOSTS: Final[frozenset[str]] = frozenset({"music.apple.com"})
_TIDAL_HOSTS: Final[frozenset[str]] = frozenset({"tidal.com", "listen.tidal.com"})
_LASTFM_HOSTS: Final[frozenset[str]] = frozenset({"last.fm"})


def artist_kind_of(mb_type: str | None) -> ArtistKind | None:
    """Map a MusicBrainz artist ``type`` to the domain vocabulary."""
    if not mb_type:
        return None
    return _ARTIST_KINDS.get(mb_type.strip().lower(), "other")


def parse_url_rel(url: str) -> ArtistUrlRel | None:
    """Extract a connector artist id from a MusicBrainz ``url-rel`` target.

    Returns ``None`` for a service Mixd does not model (Wikidata, Bandcamp,
    SoundCloud, an official homepage) rather than guessing an identifier out
    of an unrecognized URL shape.
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    segments = [segment for segment in parsed.path.split("/") if segment]
    if not host or not segments:
        return None

    if host in _SPOTIFY_HOSTS:
        return _segment_after("artist", "spotify", url, segments)
    if host in _DISCOGS_HOSTS:
        # Discogs slugs the name onto the id ("/artist/1289-Aphex-Twin");
        # the leading numeric run is the id.
        rel = _segment_after("artist", "discogs", url, segments)
        return _with_identifier(rel, _leading_digits(rel.identifier)) if rel else None
    if host in _APPLE_HOSTS:
        # /<cc>/artist/<slug>/<id> — the id is the trailing numeric segment,
        # the slug before it is decoration.
        if "artist" in segments and segments[-1].isdigit():
            return ArtistUrlRel("apple", segments[-1], url)
        return None
    if host in _TIDAL_HOSTS:
        return _segment_after("artist", "tidal", url, segments)
    if host in _LASTFM_HOSTS:
        # Last.fm's only identity is the name string, percent- and
        # plus-encoded in the path.
        rel = _segment_after("music", "lastfm", url, segments)
        return _with_identifier(rel, unquote_plus(rel.identifier)) if rel else None
    return None


def _segment_after(
    marker: str, service: str, url: str, segments: Sequence[str]
) -> ArtistUrlRel | None:
    """The path segment following *marker*, as *service*'s identifier."""
    try:
        index = segments.index(marker)
    except ValueError:
        return None
    if index + 1 >= len(segments):
        return None
    return ArtistUrlRel(service, segments[index + 1], url)


def _with_identifier(rel: ArtistUrlRel, identifier: str) -> ArtistUrlRel | None:
    return ArtistUrlRel(rel.service, identifier, rel.url) if identifier else None


def _leading_digits(value: str) -> str:
    digits = ""
    for char in value:
        if not char.isdigit():
            break
        digits += char
    return digits


def _alias_record(alias: MusicBrainzAlias) -> ArtistAliasRecord:
    return ArtistAliasRecord(
        name=alias.name,
        sort_name=alias.sort_name,
        alias_type=alias.type,
        locale=alias.locale,
        is_primary=bool(alias.primary),
    )


def to_artist_lookup(artist: MusicBrainzArtist) -> ArtistLookup:
    """Convert a validated MusicBrainz artist into the domain record."""
    url_rels = [
        rel
        for relation in artist.relations
        if relation.url is not None
        and (rel := parse_url_rel(relation.url.resource)) is not None
    ]
    return ArtistLookup(
        mbid=artist.id,
        name=artist.name,
        kind=artist_kind_of(artist.type),
        disambiguation=artist.disambiguation or None,
        aliases=tuple(_alias_record(alias) for alias in artist.aliases if alias.name),
        url_rels=tuple(url_rels),
        score=artist.score,
    )


@define(frozen=True, slots=True)
class MusicBrainzArtistEnrichmentProvider:
    """MusicBrainz implementation of the artist enrichment port."""

    client: MusicBrainzAPIClient

    async def search_artist(self, name: str) -> list[ArtistLookup]:
        """Candidate artists for a name, in MusicBrainz relevance order.

        A search result is a candidate, never a decision: same-name artists
        are real, so the caller still needs an id anchor or a disambiguated
        hit before minting anything.
        """
        artists = await self.client.search_artist(name)
        return [to_artist_lookup(artist) for artist in artists]

    async def lookup_artist(self, mbid: str) -> ArtistLookup | None:
        """The full record for one MBID, with aliases and external ids."""
        artist = await self.client.get_artist(mbid)
        if artist is None:
            logger.debug(f"No MusicBrainz artist for MBID {mbid}")
            return None
        return to_artist_lookup(artist)
