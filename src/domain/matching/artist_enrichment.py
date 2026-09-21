"""The shape artist enrichment hands the domain, and the port that supplies it.

MusicBrainz is the only source this cycle, but the records here are deliberately
service-neutral: Discogs ``namevariations`` are the obvious second alias surface
and disagree with MusicBrainz about which name is primary, so nothing in this
module ranks one source above another.

It lives under ``matching/`` rather than ``repositories/`` because it is a
provider port, not a persistence port — the caller is the enrichment operation,
and what comes back is scored, not stored verbatim.
"""

from typing import Protocol

from attrs import define, field

from src.domain.entities.artist import ArtistKind


@define(frozen=True, slots=True)
class ArtistAliasRecord:
    """One alternative name for an artist, as a source states it.

    ``alias_type`` carries the source's own typing (MusicBrainz: "Artist name",
    "Legal name", "Search hint") unnormalized — the cache stores what the
    source said, and the equivalence compiled from these rows treats every
    spelling as equal rank.
    """

    name: str
    sort_name: str | None = None
    alias_type: str | None = None
    locale: str | None = None
    is_primary: bool = False


@define(frozen=True, slots=True)
class ArtistUrlRel:
    """An external identifier a source publishes for an artist.

    ``service`` is a Mixd connector name ("spotify", "discogs", "apple",
    "tidal", "lastfm") and ``identifier`` is that service's own artist id, so
    the pair drops straight into a ``connector_artists`` row. ``url`` is kept
    because the detail page links out rather than fetching.
    """

    service: str
    identifier: str
    url: str


@define(frozen=True, slots=True)
class ArtistLookup:
    """One source's full statement about an artist.

    ``mbid`` is an anchor, not a key: a MusicBrainz merge can leave two
    canonical artists on one MBID, so a caller treats it as strong evidence
    rather than a primary key.
    """

    mbid: str
    name: str
    kind: ArtistKind | None = None
    disambiguation: str | None = None
    aliases: tuple[ArtistAliasRecord, ...] = field(factory=tuple)
    url_rels: tuple[ArtistUrlRel, ...] = field(factory=tuple)

    def alias_names(self) -> tuple[str, ...]:
        """Every name this artist is known by, primary name first."""
        return (self.name, *(alias.name for alias in self.aliases if alias.name))


class ArtistEnrichmentProviderProtocol(Protocol):
    """Port for a source of artist aliases and external identifiers."""

    async def search_artist(self, name: str) -> list[ArtistLookup]:
        """Candidate artists for a name, best first. Empty when none match."""
        ...

    async def lookup_artist(self, mbid: str) -> ArtistLookup | None:
        """The full record for one artist id, or None when it does not resolve."""
        ...
