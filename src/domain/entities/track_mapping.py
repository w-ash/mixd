"""Track-to-connector mapping domain entity.

Typed frozen entity for track mapping data flowing between the persistence
layer and domain/application layers.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Final, Literal, TypeIs
from uuid import UUID, uuid7

from attrs import define, field

# Why a mapping was superseded rather than overwritten in place (v0.10.2). Each
# value is a distinct write pattern, not a severity gradient:
#   id_dead              - the connector-side identifier stopped resolving (a
#                           404/omission debounced across repeated checks, not a
#                           single transient miss). Renamed from the earlier
#                           draft's "withdrawn": ROR uses that word for the
#                           opposite meaning (record created in error).
#   rematch               - automated re-resolution found a better candidate for
#                           the same track; the old mapping's evidence is stale,
#                           not wrong.
#   conflation            - the mapping pointed at a duplicate/non-canonical
#                           track; retargeted to the surviving canonical sibling
#                           (the FM3b/FM2c class).
#   manual                - a human (review UI, admin action) overrode the
#                           mapping directly.
type SupersessionReason = Literal["id_dead", "rematch", "conflation", "manual"]


# Domain business rule (per domain-purity): the vocabulary of ``match_method``
# — how a connector mapping was established. Every value that reaches
# ``track_mappings.match_method`` is a member; a persisted method that is not
# would be a new decision type nobody designed.
#
# The first four are the raw provider methods the matching engine scores
# (``ISRC_GRADE_METHODS`` in ``domain/matching/types.py``) and the ingest path
# writes as-is; the rest are the resolvers' persisted decisions. The
# ``*_stale_id`` suffix marks the secondary mapping a substitution keeps for a
# dead id — ``STALE_ID_FOR`` is the authoritative pairing.
type MatchMethod = Literal[
    "direct",
    "isrc",
    "mbid",
    "artist_title",
    "direct_import",
    "search_fallback",
    "spotify_redirect",
    "spotify_connector_play_resolver",
    "lastfm_discovery",
    "lastfm_import",
    # Secondary mapping on the raw (pre-autocorrect) artist::title composite,
    # written beside a corrected-name primary so a future import carrying the
    # same raw spelling still hits the fast connector-mapping lookup.
    "lastfm_import_raw_alias",
    "canonical_reuse",
    "isrc_match",
    # ISRC collision with a suspect duration delta — routed to review, not merged.
    "isrc_suspect",
    "mbid_match",
    "direct_import_stale_id",
    "search_fallback_stale_id",
    "isrc_match_stale_id",
]

MATCH_METHODS: Final[frozenset[MatchMethod]] = frozenset({
    "direct",
    "isrc",
    "mbid",
    "artist_title",
    "direct_import",
    "search_fallback",
    "spotify_redirect",
    "spotify_connector_play_resolver",
    "lastfm_discovery",
    "lastfm_import",
    "lastfm_import_raw_alias",
    "canonical_reuse",
    "isrc_match",
    "isrc_suspect",
    "mbid_match",
    "direct_import_stale_id",
    "search_fallback_stale_id",
    "isrc_match_stale_id",
})
"""Runtime membership test for :data:`MatchMethod`.

Kept adjacent to the type so the two are edited together — a ``Literal``'s
members are not reachable at runtime through a supported API.
"""


def is_match_method(value: str) -> TypeIs[MatchMethod]:
    """Narrow a persisted string to the domain vocabulary."""
    return value in MATCH_METHODS


# The stale-id variant a substitution writes for each primary method.
# Authoritative: ``stale_id_mapping_spec`` reads this map directly.
STALE_ID_FOR: Final[Mapping[MatchMethod, MatchMethod]] = {
    "direct_import": "direct_import_stale_id",
    "search_fallback": "search_fallback_stale_id",
    "isrc_match": "isrc_match_stale_id",
}

# How a mapping was established. A manual override is never replaced by a
# subsequent ingestion or matching run.
type MappingOrigin = Literal["automatic", "manual_override"]

MAPPING_ORIGINS: Final[frozenset[MappingOrigin]] = frozenset({
    "automatic",
    "manual_override",
})


def is_mapping_origin(value: str) -> TypeIs[MappingOrigin]:
    """Narrow a persisted string to the domain vocabulary."""
    return value in MAPPING_ORIGINS


# Presentation grouping for the match-method health report. Keyed by the
# vocabulary so a member without a category (or a category for a non-member)
# fails the type checker instead of rendering as "Unknown".
MATCH_METHOD_CATEGORY_ORDER: Final[tuple[str, ...]] = (
    "Primary Import",
    "Identity Resolution",
    "Cross-Service Discovery",
    "Error Recovery",
    "Secondary Cache",
)

MATCH_METHOD_CATEGORIES: Final[Mapping[MatchMethod, str]] = {
    "direct": "Primary Import",
    "direct_import": "Primary Import",
    "artist_title": "Primary Import",
    "lastfm_import": "Primary Import",
    "isrc": "Identity Resolution",
    "mbid": "Identity Resolution",
    "canonical_reuse": "Identity Resolution",
    "isrc_match": "Identity Resolution",
    "isrc_suspect": "Identity Resolution",
    "mbid_match": "Identity Resolution",
    "lastfm_discovery": "Cross-Service Discovery",
    "spotify_connector_play_resolver": "Cross-Service Discovery",
    "search_fallback": "Error Recovery",
    "spotify_redirect": "Error Recovery",
    "direct_import_stale_id": "Secondary Cache",
    "search_fallback_stale_id": "Secondary Cache",
    "isrc_match_stale_id": "Secondary Cache",
    "lastfm_import_raw_alias": "Secondary Cache",
}

MATCH_METHOD_DESCRIPTIONS: Final[Mapping[MatchMethod, str]] = {
    "direct": "Direct import — the connector's own id",
    "direct_import": "Standard Spotify import",
    "artist_title": "Standard Last.fm import",
    "lastfm_import": "Standard Last.fm import (with confidence)",
    "isrc": "ISRC match (matching pipeline)",
    "mbid": "MusicBrainz ID match (matching pipeline)",
    "canonical_reuse": "Canonical reuse — existing track matched",
    "isrc_match": "ISRC dedup across services",
    "isrc_suspect": "ISRC reuse suspected — queued for review",
    "mbid_match": "MusicBrainz ID bridging",
    "lastfm_discovery": "Spotify found via Last.fm enrichment",
    "spotify_connector_play_resolver": "Spotify play context resolution",
    "search_fallback": "Dead Spotify ID → search fallback",
    "spotify_redirect": "Spotify ID relinking detected",
    "direct_import_stale_id": "Stale ID cache (redirect)",
    "search_fallback_stale_id": "Stale ID cache (fallback)",
    "isrc_match_stale_id": "Stale ID cache (ISRC reuse)",
    "lastfm_import_raw_alias": "Last.fm raw-spelling alias (pre-autocorrect) cache",
}


@define(frozen=True, slots=True)
class TrackMapping:
    """Maps a canonical track to an external connector track with confidence scoring.

    Represents the relationship between an internal track and its corresponding
    external service track (Spotify, Last.fm, etc.) with metadata about how
    the match was determined and its reliability.

    ``confidence_evidence`` uses ``dict[str, object]`` rather than ``JsonDict``
    because application-layer producers (matching pipeline, manual overrides)
    construct evidence dicts with mixed types (UUID, datetime) that the
    persistence layer serialises at the JSONB boundary.

    The ``superseded_*`` fields (v0.10.2) make mappings append-only: a mapping
    is never overwritten in place, only retired in favor of a successor row
    referenced by ``superseded_by_id``. ``supersession_scope`` distinguishes a
    global retirement (``None``) from a context-scoped one (e.g. ``"market:GB"``,
    ``"storefront:jp"``) — contextual substitution (Spotify relinking, Apple
    equivalents, Tidal/Deezer substitution) never supersedes the incumbent, so
    a scoped value would only ever come from a secondary mapping recorded
    alongside a ``substituted`` resolution event, not from a live mapping's own
    retirement. No writer produces that event today, so the column stays
    ``None`` in practice — see the field below.
    """

    user_id: str = "default"
    track_id: UUID = field(factory=uuid7)
    connector_track_id: UUID = field(factory=uuid7)
    connector_name: str = ""
    match_method: MatchMethod = field(kw_only=True)
    confidence: int = 0
    confidence_evidence: dict[str, object] | None = None
    origin: MappingOrigin = "automatic"
    is_primary: bool = False
    # Freshness signal (not evidence): last import re-encounter of this mapping.
    last_seen_at: datetime | None = None
    # Successor mapping id once this row is retired. None while live, and also
    # None for a bare retirement with no replacement (e.g. id_dead with nothing
    # to relink to).
    superseded_by_id: UUID | None = None
    superseded_at: datetime | None = None
    supersession_reason: SupersessionReason | None = None
    # None = global (the mapping is superseded everywhere). A non-None value
    # would scope the supersession to one market/storefront/context, but
    # nothing writes one yet — ``retire_mapping`` dropped its ``scope``
    # parameter (v0.10.2) once grep showed neither call site ever passed it.
    # The column awaits a future context-scoped-retirement writer.
    supersession_scope: str | None = None
    # Next scheduled re-verification for this (still-live) mapping — the FM4a
    # substrate (GLEIF "lapsed" analog). No worker reads this yet; the column
    # exists so a future re-validation pass has somewhere to write due dates.
    next_verify_at: datetime | None = None
    id: UUID = field(factory=uuid7)
