"""Artist aggregate and its connector-side records.

The canonical :class:`Artist` is per-user, like the canonical track: a
cross-user identity would smuggle in merge rights and shared edits this cycle
has not earned. :class:`ConnectorArtist` is global — a service record is a fact
about the service, not about the user — and :class:`ArtistMapping` is the
per-user statement that one connector artist *is* one canonical artist.

Mappings here are live rows: a changed decision rewrites the row instead of
retiring it, because artist ids are treated as permanent while track ids die
and relink. The five supersession fields on :class:`~src.domain.entities.
track_mapping.TrackMapping` are therefore deliberately absent, and the generic
mapping repository reads that difference off its ``MappingShape``.
"""

from datetime import datetime
from typing import Final, Literal, TypeIs, cast, get_args
from uuid import UUID, uuid7

from attrs import define, field, validators

from .shared import JsonDict
from .track_mapping import MappingOrigin, MatchMethod

# What kind of entity an artist is, from MusicBrainz ``type``. Kept to the
# three buckets a detail page acts on: an orchestra or choir is a group of
# people and reads as "group"; character and the rest read as "other".
type ArtistKind = Literal["person", "group", "other"]

# ``cast("object", ...)`` keeps the PEP 695 ``__value__`` (typed Any) out of
# strict type checking — the idiom ``track_mapping.py`` uses.
ARTIST_KINDS: Final[frozenset[ArtistKind]] = frozenset(
    get_args(cast("object", ArtistKind.__value__))
)
"""Runtime membership test for :data:`ArtistKind`, derived from the alias."""


def is_artist_kind(value: str) -> TypeIs[ArtistKind]:
    """Narrow a persisted string to the domain vocabulary."""
    return value in ARTIST_KINDS


# Credit names that mean "this release has no single artist". Never an artist
# row: the census (entity-representation-findings §4) found six unrelated
# service sentinels, one of them (Discogs artist 194) not even fetchable, so
# there is nothing stable to map and a canonical row would be a favouritable
# phantom. "Various Artists" (Spotify, Last.fm, Apple, MusicBrainz) and
# "Various" (Discogs) are the two the census observed; the rest are the
# conventional spellings tagging tools write for the same thing. A credit
# matching any of them keeps its ``track_artists`` row with a null artist id.
VARIOUS_ARTISTS_SENTINELS: Final[frozenset[str]] = frozenset({
    "various artists",
    "various",
    "va",
    "v/a",
    "v.a.",
    "verschiedene interpreten",
})
"""Lowercased credit names that are a compilation flag, not an artist."""


def is_various_artists(name: str) -> bool:
    """Report whether a credited name is a Various Artists sentinel."""
    return name.strip().casefold() in VARIOUS_ARTISTS_SENTINELS


@define(frozen=True, slots=True)
class Artist:
    """A canonical artist in one user's library.

    ``mbid`` is the identity anchor when present but a *reference, not a key*:
    a MusicBrainz merge can leave two canonical artists on one MBID, so the
    column is indexed and non-unique. ``name`` is non-unique too — same-name
    artists are real. Identity uniqueness lives in the mappings.
    """

    name: str = field(validator=validators.instance_of(str))
    # Tenant is always explicit: no default at any layer (v0.12.0.2).
    user_id: str = field(kw_only=True)
    mbid: str | None = field(default=None)
    kind: ArtistKind | None = field(default=None)
    created_at: datetime | None = field(default=None)
    updated_at: datetime | None = field(default=None)
    id: UUID = field(factory=uuid7)


@define(frozen=True, slots=True)
class ArtistFavorite:
    """An artist the user favorited.

    Presence only: the row exists while the artist is favorited and is deleted
    when it is not. Favorites are Mixd-only curation and never sync to a
    service, so there is no ``service`` column and nothing for a tombstone to
    push.
    """

    artist_id: UUID
    # Tenant is always explicit: no default at any layer (v0.12.0.2).
    user_id: str = field(kw_only=True)
    favorited_at: datetime | None = field(default=None)


@define(frozen=True, slots=True)
class ConnectorArtist:
    """An artist record as one service states it.

    Global, like :class:`~src.domain.entities.track.ConnectorTrack`: no
    ``user_id``. ``name`` carries the service's spelling with Discogs' numeric
    disambiguation suffix ("Tycho (3)") stripped into ``raw_metadata`` rather
    than left in the name.
    """

    connector_name: str
    connector_artist_identifier: str
    name: str
    raw_metadata: JsonDict = field(factory=dict)
    last_updated: datetime | None = field(default=None)
    id: UUID = field(factory=uuid7)


@define(frozen=True, slots=True)
class ArtistMapping:
    """Maps a canonical artist to a connector artist with confidence scoring.

    Several rows per service per one artist are normal, not an anomaly: alias
    projects (Caribou / Daphni / Manitoba) carry separate ids on every service
    and are linked, never merged. ``is_primary`` picks the one the UI shows.
    """

    # Tenant is always explicit: no default at any layer (v0.12.0.2).
    user_id: str = field(kw_only=True)
    artist_id: UUID = field(factory=uuid7)
    connector_artist_id: UUID = field(factory=uuid7)
    connector_name: str = ""
    match_method: MatchMethod = field(kw_only=True)
    confidence: int = 0
    confidence_evidence: dict[str, object] | None = None
    origin: MappingOrigin = "automatic"
    is_primary: bool = False
    # Freshness signal (not evidence): last import re-encounter of this mapping.
    last_seen_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    id: UUID = field(factory=uuid7)


@define(frozen=True, slots=True)
class ArtistAlias:
    """One alternative name a service states for a connector artist.

    Attached to the connector record, not the canonical artist: an alias is the
    service's claim about its own entity. MusicBrainz supplies ``alias_type``
    and ``locale``; Discogs' ``namevariations`` supply neither.
    """

    connector_artist_id: UUID
    name: str
    sort_name: str | None = None
    alias_type: str | None = None
    locale: str | None = None
    is_primary: bool = False
    fetched_at: datetime | None = None
    id: UUID = field(factory=uuid7)
