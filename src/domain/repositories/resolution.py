"""Resolution event-log, negative-cache, and write-seam protocols (v0.10.2).

Three interfaces, one purpose: an identity decision and the record of that
decision must land in the same transaction, so the log can never disagree with
the data it explains. The two repository protocols are the storage; the
recorder protocol is the *seam* every mutation site routes through, and the
reason it exists as an interface rather than a concrete import is that the
sites live in three different layers (a use case, an infrastructure service, a
repository) and none of them may reach outward to find it.
"""

from collections.abc import Awaitable, Mapping, Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from attrs import define, field

from src.domain.entities.resolution_event import ResolutionEvent, ResolutionEventType
from src.domain.entities.resolution_negative import ResolutionNegative
from src.domain.entities.shared import JsonDict
from src.domain.entities.track import Track
from src.domain.entities.track_mapping import SupersessionReason
from src.domain.matching.content_digest import DigestSide


@define(frozen=True, slots=True)
class SuspectWindow:
    """How many recent ``suspect`` events there are, and how long they span.

    The two numbers together are the death debounce: a count alone would
    declare a flapping id dead within one import run, and a span alone would
    accept a single failure that happened to be old. Both must clear their
    thresholds (``DEATH_DEBOUNCE_FAILURES`` over
    ``DEATH_DEBOUNCE_MIN_SPAN_SECONDS``) before an id is retired.
    """

    count: int = 0
    span_seconds: float = 0.0


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
    confidence: int = 0
    score: float | None = None
    payload: JsonDict = field(factory=dict)


@define(frozen=True, slots=True)
class RejectedPairRow:
    """Storage-shaped cannot-link fact — the negative repository's input."""

    connector_track_id: UUID
    candidate_track_id: UUID
    connector_name: str
    content_digest: str


class ResolutionEventRepositoryProtocol(Protocol):
    """Append-only storage for :class:`ResolutionEvent`."""

    def append_events(self, events: Sequence[ResolutionEvent]) -> Awaitable[int]:
        """Insert events, ignoring id collisions; return the number inserted.

        ``recorded_at`` is never supplied — the column's ``now()`` default is
        the only writer, so ordering stays monotone regardless of what clock
        the calling process believes in.
        """
        ...

    def recent_suspect_window(
        self, *, user_id: str, connector_name: str, connector_track_id: UUID
    ) -> Awaitable[SuspectWindow]:
        """Count and time-span of the ``suspect`` streak since the last success.

        "Since the last success" is what makes the streak reset free: a
        ``verified``/``accepted`` event later than the oldest suspect truncates
        the window, so recovery needs no counter to clear.
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
        connector_track_id: UUID,
        matcher_version: str,
    ) -> Awaitable[ResolutionNegative]:
        """Record (or extend) a no-match backoff for one connector track."""
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

    def count_active_negatives(self, *, user_id: str) -> Awaitable[NegativeCacheSize]:
        """How many pairs are suppressed and how many ids are inside a backoff."""
        ...


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

    def write_events(
        self, events: Sequence[ResolutionEvent], *, user_id: str
    ) -> Awaitable[Sequence[ResolutionEvent]]:
        """Append pre-built events, stamping ``user_id`` on every one.

        The stamp is unconditional — an event built with a different owner is
        overwritten, because the argument is the authority on whose decision
        this is.
        """
        ...

    def build_events(
        self, decisions: Sequence[ResolutionDecision], *, user_id: str
    ) -> list[ResolutionEvent]:
        """Stamp decisions with matcher version, decision clock, and run id.

        Pure — separated from :meth:`record` so a use case can hand the events
        to ``apply_with_event_log`` and keep "rows changed ⇒ event" in one
        place instead of re-implementing the guard per site.
        """
        ...

    def record(
        self, decisions: Sequence[ResolutionDecision], *, user_id: str
    ) -> Awaitable[int]:
        """Build and append in one step, for sites that own no commit."""
        ...

    def record_supersessions(
        self,
        edges: Mapping[UUID, UUID],
        *,
        user_id: str,
        connector_name: str,
        reason: SupersessionReason = "rematch",
    ) -> Awaitable[int]:
        """One ``superseded`` event per predecessor→successor edge."""
        ...

    def retire_mapping(
        self,
        mapping_id: UUID,
        *,
        user_id: str,
        reason: SupersessionReason,
        scope: str | None = None,
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
        """Persist cannot-link entries, materializing connector tracks as needed."""
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

    def note_suspect(
        self,
        connector_side: DigestSide,
        *,
        user_id: str,
        connector_name: str,
        payload: JsonDict | None = None,
    ) -> Awaitable[bool]:
        """Record one failed lookup and report whether it proves the id dead.

        Returns True only when the debounce thresholds are both met, at which
        point the live mapping (if any) has been retired as ``id_dead`` and the
        matching ``superseded`` event written. A single 404 never gets there:
        the measured Spotify transient band is seconds to minutes, and one
        spurious miss retiring a good id would be worse than the staleness it
        was trying to fix.
        """
        ...
