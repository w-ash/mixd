"""Track-to-connector mapping domain entity.

Typed frozen entity for track mapping data flowing between the persistence
layer and domain/application layers.
"""

from datetime import datetime
from typing import Literal
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
#   platform_superseded   - the connector itself asserts permanent succession —
#                           MusicBrainz 301 merge redirects only. Never Spotify
#                           relinking, Apple equivalents, or Tidal/Deezer
#                           substitution, which are contextual and never retire
#                           the incumbent (see supersession_scope below).
#   manual                - a human (review UI, admin action) overrode the
#                           mapping directly.
type SupersessionReason = Literal[
    "id_dead", "rematch", "conflation", "platform_superseded", "manual"
]


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
    ``"storefront:jp"``) — contextual substitution never supersedes the
    incumbent (see ``SupersessionReason.platform_superseded``), so a scoped
    value here only ever comes from a secondary mapping recorded alongside a
    ``substituted`` resolution event, not from a live mapping's own retirement.
    """

    user_id: str = "default"
    track_id: UUID = field(factory=uuid7)
    connector_track_id: UUID = field(factory=uuid7)
    connector_name: str = ""
    match_method: str = ""
    confidence: int = 0
    confidence_evidence: dict[str, object] | None = None
    origin: str = "automatic"
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
    # scopes the supersession to one market/storefront/context.
    supersession_scope: str | None = None
    # Next scheduled re-verification for this (still-live) mapping — the FM4a
    # substrate (GLEIF "lapsed" analog). No worker reads this yet; the column
    # exists so a future re-validation pass has somewhere to write due dates.
    next_verify_at: datetime | None = None
    id: UUID = field(factory=uuid7)
