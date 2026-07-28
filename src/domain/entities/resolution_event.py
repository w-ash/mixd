"""Resolution event domain entity — the append-only identity-resolution log.

Every accept, reject, supersession, and override becomes one immutable
``ResolutionEvent`` row, written in the same transaction as the mapping
mutation it explains (v0.10.2). The log is bitemporal-lite: three separate
instants answer three separate questions, and collapsing them loses history
that cannot be reconstructed later.

``recorded_at`` is the database's own insertion clock — monotone, assigned by
the DB default, and never set by application code. It answers "when did mixd
learn this". ``decided_at`` is the matcher's clock: for an online decision made
in real time it equals ``recorded_at``, but the two diverge whenever the same
decision is produced later than it logically happened — a backfill run or an
offline re-resolution replaying old inputs — so "what did we believe on date X"
survives even though the row itself landed today. ``evidence_as_of`` is a third,
independent instant: the freshness of the provider snapshot (a Spotify or
MusicBrainz response) the decision was computed from, which is what makes a
past decision reproducible from its recorded inputs rather than merely dated.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid7

from attrs import define, field

# Starting vocabulary (v0.10.2 spec). `substituted` is the contextual-swap
# shape (Spotify relinking, Apple equivalents, Tidal/Deezer alternatives) —
# it carries a scope + both ids but never writes a supersession, because the
# incumbent mapping stays valid outside that context.
#
# `queued` is the one addition to the spec's list: a review-zone candidate is
# neither accepted nor rejected, and recording it as either would make the log
# say something untrue about a decision nobody has taken yet. It exists because
# score, zone, and selection probability have to be captured *at queue time* or
# calibration can never recover them (memo §10.4) — and the review row itself
# stores none of the three.
type ResolutionEventType = Literal[
    "accepted",
    "rejected",
    "queued",
    "no_match",
    "superseded",
    "substituted",
    "suspect",
    "verified",
    "manual_override",
    "unrejected",
]


@define(frozen=True, slots=True)
class ResolutionEvent:
    """One immutable record of an identity-resolution decision.

    ``confidence``, ``score``, ``zone``, and ``selection_probability`` are
    recorded at queue time (not derived later) so the log stays usable for
    future calibration work even though calibration itself is out of scope
    for this milestone — label-based estimation needs the score and the
    probability the candidate was even offered for review, and neither is
    recoverable after the fact.

    ``payload`` carries the variable, provider-specific evidence (e.g. the
    Spotify requested/returned ids + market, the MusicBrainz source/target
    entity, the Apple equivalents array) that doesn't warrant its own typed
    column.
    """

    id: UUID = field(factory=uuid7)
    user_id: str = "default"
    event_type: ResolutionEventType = "accepted"
    # Content hash of the full matcher config (src.domain.matching.version) —
    # a provenance/validity key, never an auto-re-resolution trigger.
    matcher_version: str = ""
    # DB-assigned; never set by application code (see module docstring).
    recorded_at: datetime | None = None
    decided_at: datetime | None = None
    evidence_as_of: datetime | None = None
    run_id: UUID | None = None
    connector_name: str | None = None
    connector_track_id: UUID | None = None
    track_id: UUID | None = None
    resulting_mapping_id: UUID | None = None
    confidence: int | None = None
    score: float | None = None
    zone: str | None = None
    selection_probability: float | None = None
    payload: dict[str, object] = field(factory=dict)
