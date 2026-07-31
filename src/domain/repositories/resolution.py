"""Resolution event-log, negative-cache, and write-seam protocols (v0.10.2).

Three interfaces, one purpose: an identity decision and the record of that
decision must land in the same transaction, so the log can never disagree with
the data it explains. The two repository protocols are the storage; the
recorder protocol is the *seam* every mutation site routes through, and the
reason it exists as an interface rather than a concrete import is that the
sites live in three different layers (a use case, an infrastructure service, a
repository) and none of them may reach outward to find it.
"""

from collections.abc import Awaitable, Iterable, Mapping, Sequence
from datetime import datetime
from typing import Literal, NamedTuple, Protocol
from uuid import UUID

from attrs import define, field

from src.domain.entities.resolution_event import ResolutionEvent, ResolutionEventType
from src.domain.entities.shared import JsonDict
from src.domain.entities.track import Track
from src.domain.entities.track_mapping import SupersessionReason
from src.domain.matching.content_digest import DigestSide, service_side
from src.domain.matching.types import MatchResult


@define(frozen=True, slots=True)
class NegativeCacheSize:
    """How much the negative cache is currently suppressing, per mechanism.

    The cache's own size is a monitored metric, not an implementation detail:
    a cannot-link store with no clock-based expiry only ever grows, and the
    documented failure mode (Reltio's) is silent *under*matching as it does.
    A rejected-pair count climbing without a matching climb in review throughput
    is the shape of that failure, visible a release before users report it.
    """

    rejected_pairs_active: int = 0
    no_match_pending: int = 0
    # Ids that have now missed enough times, over a long enough span, to look
    # dead rather than transient. Reported rather than acted on: providers
    # relink and redirect rather than delete (Spotify's track relinking,
    # MusicBrainz's merge-not-delete), so a number that stays near zero is the
    # expected shape and a number that climbs is worth a human looking.
    dead_id_candidates: int = 0


@define(frozen=True, slots=True)
class ResolutionDecision:
    """One identity decision, ready to be recorded, however it was reached.

    Deliberately independent of the machinery that produced it: a match
    evaluation, a Spotify redirect, a human clicking accept, and a debounced
    id death all reduce to this. ``matcher_version``, ``decided_at`` and
    ``run_id`` are *not* here — the seam stamps them, so no call site can
    forget one or record a version it did not actually run.
    """

    event_type: ResolutionEventType
    connector_name: str
    connector_track_id: UUID | None = None
    track_id: UUID | None = None
    resulting_mapping_id: UUID | None = None
    confidence: int | None = None
    score: float | None = None
    zone: str | None = None
    # Probability this candidate was offered for review at all. 1.0 wherever
    # queue admission is deterministic, which is everywhere today; the field
    # exists because calibration cannot reconstruct it later if a sampled
    # stratum is ever added.
    selection_probability: float | None = None
    evidence_as_of: datetime | None = None
    payload: JsonDict = field(factory=dict)


@define(frozen=True, slots=True)
class RejectionCandidate:
    """A candidate pair some decision refused, with everything to key it.

    Carries both sides because the cannot-link entry is keyed by a digest over
    both: change either one's match-relevant metadata and the rejection
    expires, which is the only expiry a sticky constraint gets.
    """

    connector: DigestSide
    candidate_track: Track


def rejection_candidates(matches: Iterable[MatchResult]) -> list[RejectionCandidate]:
    """Build the ``active_rejections`` query payload for a batch of matches.

    Both the accept path and the review-queue path ask the same question of
    the same store — "is this pair still refused?" — and must build the
    identical candidate shape to ask it, or a rejection honoured by one could
    silently be ignored by the other. What differs between the two callers is
    tenancy handling (grouped by owner vs. a single caller-supplied user) and
    what they do with the returned frozenset, not this construction step.
    """
    return [
        RejectionCandidate(
            connector=service_side(match.connector_id, match.service_data),
            candidate_track=match.track,
        )
        for match in matches
    ]


@define(frozen=True, slots=True)
class RejectedPairRow:
    """Storage-shaped cannot-link fact — the negative repository's input."""

    connector_track_id: UUID
    candidate_track_id: UUID
    connector_name: str
    content_digest: str


# Which half of the negative cache a listing is asking about. A *selector*, not
# a predicate: the predicates are SQL and live in the repository, and naming
# them here would drag the storage layer's expression types into the domain and
# out to the CLI. Both kinds answer the same shape of question — "what is this
# table currently holding against me, and about which track" — which is why one
# listing serves both.
type NegativeListingKind = Literal["rejected", "dead_id"]


@define(frozen=True, slots=True)
class NegativeListing:
    """One negative-cache row, read-shaped for a human deciding what to do.

    The counterpart to :class:`RejectedPairRow` on the way out: that is what a
    caller hands the repository to record a rejection, this is what a caller
    gets back to *display* one — connector and track identity joined in, so a
    human (or the ``manage_track_matches`` agent tool) never needs a second
    lookup per row.

    ``track_id``/``track_title`` are "the track this row is about", which
    differs by kind and deliberately reads the same either way: for a rejection
    it is the candidate that was refused, for a dead id it is the track whose
    live mapping still points at the identifier that stopped answering. A
    listing exists so a person can act, and in both cases the track is the
    thing they act on.
    """

    connector_track_id: UUID
    connector_name: str
    connector_track_identifier: str
    track_id: UUID | None = None
    track_title: str | None = None
    # Consecutive failed lookups. Zero for a rejection — nothing was missing.
    consecutive_misses: int = 0


class ResolutionEventRepositoryProtocol(Protocol):
    """Append-only storage for :class:`ResolutionEvent`."""

    def append_events(self, events: Sequence[ResolutionEvent]) -> Awaitable[int]:
        """Insert events, ignoring id collisions; return the number inserted.

        ``recorded_at`` is never supplied — the column's ``now()`` default is
        the only writer, so ordering stays monotone regardless of what clock
        the calling process believes in.
        """
        ...

    def events_for_mapping(
        self, mapping_id: UUID, *, user_id: str, limit: int = 100
    ) -> Awaitable[list[ResolutionEvent]]:
        """Every event naming this mapping, newest first — "why do we believe this"."""
        ...


class ResolutionNegativeRepositoryProtocol(Protocol):
    """Storage for the two negative-cache mechanisms."""

    def upsert_no_match(
        self,
        *,
        user_id: str,
        connector_name: str,
        connector_track_ids: Sequence[UUID],
        matcher_version: str,
    ) -> Awaitable[int]:
        """Record (or extend) the no-match backoff for a batch of connector tracks.

        Returns the number of rows written. One statement per batch, not per
        id: the counter increment and the clock doubling are both SQL, so the
        whole batch is one atomic upsert.
        """
        ...

    def clear_no_match(
        self,
        *,
        user_id: str,
        connector_name: str,
        connector_track_ids: Sequence[UUID],
    ) -> Awaitable[int]:
        """Drop the backoff rows for connector tracks that just resolved."""
        ...

    def pending_no_match(
        self,
        *,
        user_id: str,
        connector_name: str,
        connector_track_ids: Sequence[UUID],
    ) -> Awaitable[set[UUID]]:
        """Which of these connector tracks are still inside their backoff window."""
        ...

    def record_rejected_pairs(
        self, pairs: Sequence[RejectedPairRow], *, user_id: str, matcher_version: str
    ) -> Awaitable[int]:
        """Remember cannot-link facts; re-asserting one refreshes its digest."""
        ...

    def active_rejected_pairs(
        self,
        *,
        user_id: str,
        connector_track_ids: Sequence[UUID],
        matcher_version: str,
        content_digests: Mapping[tuple[UUID, UUID], str] | None = None,
    ) -> Awaitable[set[tuple[UUID, UUID]]]:
        """Pairs still suppressed: same matcher version, same digest, not un-rejected.

        ``content_digests`` supplies the *current* digest per pair; a stored
        digest that no longer matches means the underlying metadata changed, so
        the pair is expired and comes back as a candidate.
        """
        ...

    def unreject(
        self, *, user_id: str, connector_track_id: UUID, candidate_track_id: UUID
    ) -> Awaitable[bool]:
        """Stamp ``unrejected_at``; return whether a live rejection was found."""
        ...

    def list_negatives(
        self,
        *,
        user_id: str,
        kind: NegativeListingKind,
        connector_track_ids: Sequence[UUID] | None = None,
    ) -> Awaitable[list[NegativeListing]]:
        """What the negative cache is holding against this user, identity joined in.

        One listing for both halves because both answer the same question for
        the same reader: *something is being withheld or has stopped working —
        which track, and what would I do about it?* ``rejected`` is the
        discovery step ``unreject`` depends on; ``dead_id`` is the drill-down
        behind ``NegativeCacheSize.dead_id_candidates``, which is a bare number
        and cannot be acted on without it.

        ``connector_track_ids`` narrows to specific connector tracks (the
        per-track view); omitted, returns the user's whole set.

        "Active" here is deliberately broader than
        :meth:`active_rejected_pairs`'s matcher-scoped view: un-withdrawn,
        regardless of matcher version. That view exists for automatic
        re-matching, which must honour the version expiry; this one is for a
        human to browse, and a pair a matcher bump quietly stopped enforcing is
        still something they may want to see and withdraw for good.

        Batch-fetches connector-track and track identity in the query rather
        than one lookup per row — this is a listing, and the codebase is
        batch-first.
        """
        ...

    def count_active_negatives(self, *, user_id: str) -> Awaitable[NegativeCacheSize]:
        """How many pairs are suppressed and how many ids are inside a backoff."""
        ...


class SupersessionEdge(NamedTuple):
    """A live mapping's predecessor→successor edge, with the tenancy to key it.

    Not merge-specific despite the shape's origin there (it moved from
    ``TrackRepositoryProtocol.merge_mappings_to_track``'s return type, where it
    was ``ConflationEdge``): an automated re-match's ``assert_mappings`` batch
    and a track merge's mapping conflation both retire a mapping in favor of
    another, and both need the same four fields to record it as an event.
    Tenancy travels with the edge, rather than being supplied once per caller,
    because a single batch can span users and connectors — the recorder groups
    by (user_id, connector_name) internally, so no caller has to.
    """

    predecessor_id: UUID
    successor_id: UUID
    user_id: str
    connector_name: str


class ResolutionRecorderProtocol(Protocol):
    """The single write seam for identity decisions.

    Every method here rides the caller's open transaction and commits nothing —
    the mutation site owns its transaction boundary, and an event that
    committed on its own could survive a rolled-back mapping write.
    """

    @property
    def matcher_version(self) -> str:
        """The matcher identity stamped on every event and cache row.

        Read-only by design: it is derived from the matching code and config,
        so a writable attribute would let a caller record decisions under a
        version that never ran.
        """
        ...

    def record(
        self, decisions: Sequence[ResolutionDecision], *, user_id: str
    ) -> Awaitable[int]:
        """Build and append in one step, for sites that own no commit.

        ``user_id`` is stamped onto every event unconditionally, not filled in
        only where a decision lacks one. ``ResolutionDecision`` carries no
        owner of its own — the seam is the single place that supplies it — so
        a caller running under the local-dev default could otherwise have its
        events recorded as another tenant's history, past an RLS policy that
        reads ``app.user_id`` and would have rejected them.
        """
        ...

    def record_supersessions(
        self,
        edges: Sequence[SupersessionEdge],
        *,
        reason: SupersessionReason = "rematch",
    ) -> Awaitable[int]:
        """One ``superseded`` event per edge, grouped by (user_id, connector_name).

        Callers hand over whatever they already have — the edges from an
        ``assert_mappings`` batch or a merge's mapping conflation already carry
        their own tenancy — so there is no group-by to hand-roll before
        calling this; the recorder does it once, internally. Each event is
        written against its edge's *successor*: "this mapping is what
        replaced that one" is the question a reader asks, and the successor is
        the row still reachable by a live lookup.
        """
        ...

    def retire_mapping(
        self,
        mapping_id: UUID,
        *,
        user_id: str,
        reason: SupersessionReason,
    ) -> Awaitable[bool]:
        """Retire a live mapping with no successor; return whether a row moved.

        Retirement is how a mapping *ends* without being replaced — an id that
        died, or a user who unlinked. The row stays and becomes history rather
        than being deleted, because "we used to believe X" is the whole point.
        """
        ...

    def active_rejections(
        self,
        candidates: Sequence[RejectionCandidate],
        *,
        user_id: str,
        connector_name: str,
    ) -> Awaitable[frozenset[tuple[str, UUID]]]:
        """Which of these (connector identifier, track id) pairs are still refused."""
        ...

    def remember_rejections(
        self,
        candidates: Sequence[RejectionCandidate],
        *,
        user_id: str,
        connector_name: str,
    ) -> Awaitable[int]:
        """Persist cannot-link entries, materializing connector tracks as needed.

        Every refusal stored here binds every reader of the store, so only a
        decision that should stop *all* future matching belongs in it. A gate
        that refuses on its own stricter bar records a ``rejected`` event and
        nothing more — see :class:`ResolutionEvent`, which is where "who
        decided what, and why" lives.
        """
        ...

    def remember_no_match(
        self,
        connector_sides: Sequence[DigestSide],
        *,
        user_id: str,
        connector_name: str,
    ) -> Awaitable[int]:
        """Start or extend the backoff clock for connector ids that found nothing."""
        ...

    def clear_negatives(
        self,
        connector_identifiers: Sequence[str],
        *,
        user_id: str,
        connector_name: str,
    ) -> Awaitable[int]:
        """Any success clears the backoff — no hysteresis on recovery."""
        ...

    def backoff_suppressed(
        self,
        connector_identifiers: Sequence[str],
        *,
        user_id: str,
        connector_name: str,
    ) -> Awaitable[frozenset[str]]:
        """External ids whose ``check_again`` has not yet come due.

        The reader for the backoff clock. Callers must skip these before
        spending a provider request on them; an id with no stored no-match row,
        or one whose clock has passed, is never in the result.
        """
        ...

    def connector_track_ids(
        self, identifiers: Sequence[str], *, connector_name: str
    ) -> Awaitable[dict[str, UUID]]:
        """The connector-track rows these external ids name (no writes).

        Every event, streak and backoff query keys on ``connector_track_id``,
        so anything recording an event *about* an external id has to trade the
        string for the row first. Unknown ids are absent from the result.
        """
        ...
