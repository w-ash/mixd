"""Is an incoming description an existing canonical, a new one, or a review?

The one place that decision is made. An ingest batch arrives as descriptions
of recordings, each possibly carrying a strong identifier (an ISRC for a
track); the planner ranks the evidence the same way at every site — the
strong id first, then names and lengths, then the model's own price for the
decision — and returns one outcome per description. It never touches a
repository: the callers do the two batch probes (who owns these strong ids,
who already describes these names), hand the answers in, and persist what
comes out.

Entity-generic by construction. ``plan_resolution`` knows nothing about
tracks: it is parameterised by a ``ResolutionRules`` object that describes an
entity, prices a strong-id match, prices a same-entity match and prices a
creation; the caller keys each description (``Described.name_key``).
``TrackResolutionRules`` is the track instantiation; an artist or album
planner is another set of rules, not a second copy of the walk.
"""

from collections.abc import Hashable, Iterable, Mapping, Sequence
from typing import Literal, Protocol
from uuid import UUID

from attrs import Factory, define, field

from src.domain.entities.match_review import MatchReview
from src.domain.entities.track import Track
from src.domain.entities.track_mapping import MatchMethod
from src.domain.matching.algorithms import (
    InternalTrackData,
    ServiceTrackData,
    calculate_confidence,
)
from src.domain.matching.config import MatchingConfig
from src.domain.matching.evaluation_service import TrackMatchEvaluationService
from src.domain.matching.isrc_validation import (
    assess_isrc_match_reliability,
    compute_duration_diff_ms,
)
from src.domain.matching.recording_identity import (
    RecordingDescription,
    describe_track,
    describes_same_recording,
    identity_key,
)
from src.domain.matching.types import ConfidenceEvidence, MatchZone


@define(frozen=True, slots=True)
class ResolutionEvidence:
    """How a decision was priced: the method, the score and the zone it fell in.

    Every persisted mapping and review carries one of these, so the number on
    the row is always the model's, never a constant a call site chose.
    """

    method: MatchMethod
    confidence: int
    zone: MatchZone
    match_weight: float
    evidence: dict[str, object] | None = None


@define(frozen=True, slots=True)
class Described[TKey, TDesc]:
    """One incoming item: its batch key, its description and its strong id.

    ``name_key`` is the bucket a name match requires — the key the caller
    files ``name_owners`` under, so it owns both sides of that contract —
    or ``None`` when names may not settle this item: a description with
    nothing to compare, a relink the provider itself asserted, a connector
    whose contract is strong-id-or-nothing. The strong id still decides it,
    and it neither folds onto a name leader nor leads a name bucket.
    """

    key: TKey
    description: TDesc
    strong_id: str | None = None
    name_key: Hashable | None = None


@define(frozen=True, slots=True)
class Refusal[TEntity]:
    """The best same-entity candidate the planner priced and turned down.

    Carried on the creation it explains, so a consumer that records
    refusals (the inward resolvers' refusal events) never re-runs the
    comparison.
    """

    candidate: TEntity
    evidence: ResolutionEvidence


@define(frozen=True, slots=True)
class Contest[TKey]:
    """An earlier creation in the batch whose strong id this one collided
    with as a suspect, and the price of that collision."""

    leader: TKey
    evidence: ResolutionEvidence


@define(frozen=True, slots=True)
class Reuse[TKey, TEntity]:
    """Map onto an entity that already holds this recording.

    Exactly one of ``canonical`` (a persisted entity) or ``leader`` (the key
    of an earlier ``Create`` in the same batch) is set. Leaders are why a
    batch carrying an original and its remaster, neither yet known, mints
    one canonical and not two.
    """

    evidence: ResolutionEvidence
    canonical: TEntity | None = None
    leader: TKey | None = None
    kind: Literal["reuse"] = field(default="reuse", init=False)

    def __attrs_post_init__(self) -> None:
        if (self.canonical is None) == (self.leader is None):
            raise ValueError("Reuse names exactly one of canonical or leader")

    @property
    def depends_on(self) -> TKey | None:
        """The batch key whose creation must persist before this outcome can."""
        return self.leader


@define(frozen=True, slots=True)
class Create[TKey, TEntity]:
    """Mint a new entity, claiming ``strong_id`` when it is not contested.

    ``refusal`` is the best same-entity candidate the planner priced and
    turned down. ``contest`` names an earlier creation in the same batch
    whose strong id this description collided with as a suspect: the
    creation withholds the id, and the caller — which persists the leader
    before it acts on this outcome — queues the review against it.
    """

    strong_id: str | None
    evidence: ResolutionEvidence
    refusal: Refusal[TEntity] | None = None
    contest: Contest[TKey] | None = None
    kind: Literal["create"] = field(default="create", init=False)

    def __attrs_post_init__(self) -> None:
        if self.contest is not None and self.strong_id is not None:
            raise ValueError("a contested creation must withhold the contested id")

    @property
    def depends_on(self) -> TKey | None:
        """The batch key whose creation must persist before this outcome can."""
        return None if self.contest is None else self.contest.leader


@define(frozen=True, slots=True)
class DeferToReview[TKey, TEntity]:
    """A suspect strong-id collision: create without the id and ask a person.

    ``owner`` holds the contested strong id; ``review`` is the price of the
    collision the review shows; ``create`` is the creation that happens
    meanwhile, always with ``strong_id`` withheld.
    """

    owner: TEntity
    review: ResolutionEvidence
    create: Create[TKey, TEntity]
    kind: Literal["defer_to_review"] = field(default="defer_to_review", init=False)

    def __attrs_post_init__(self) -> None:
        if self.create.strong_id is not None:
            raise ValueError("a deferred creation must withhold the contested id")

    @property
    def depends_on(self) -> TKey | None:
        """The owner is persisted already: a deferral waits on nothing."""
        return None


type Outcome[TKey, TEntity] = (
    Reuse[TKey, TEntity] | Create[TKey, TEntity] | DeferToReview[TKey, TEntity]
)


def creation_of[TKey, TEntity](
    outcome: Outcome[TKey, TEntity],
) -> Create[TKey, TEntity] | None:
    """The creation an outcome persists, if it persists one."""
    if outcome.kind == "create":
        return outcome
    if outcome.kind == "defer_to_review":
        return outcome.create
    return None


class ResolutionRules[TDesc, TEntity](Protocol):
    """What the planner needs to know about one kind of entity."""

    def describe(self, entity: TEntity) -> TDesc:
        """An existing entity, as the same-entity question sees it."""
        ...

    def strong_match(
        self, description: TDesc, owner: TDesc
    ) -> tuple[ResolutionEvidence, bool]:
        """Price a strong-id collision; the flag says whether it is suspect."""
        ...

    def same(self, description: TDesc, candidate: TDesc) -> ResolutionEvidence | None:
        """Price a name-bucket candidate, or None when it cannot be compared.

        A priced candidate whose zone is not ``accept`` is a refusal the
        planner records on the creation; ``None`` leaves no record.
        """
        ...

    def creation(self, description: TDesc) -> ResolutionEvidence:
        """Price a creation — the description vouching for itself."""
        ...


@define(slots=True)
class _Leaders[TKey, TDesc]:
    """The creations earlier in the batch that later items may fold onto.

    A name bucket holds one leader per *recording*: a creation the gate
    refused against every earlier leader in its bucket (a longer master of
    the same name) leads for its own later twins.
    """

    by_strong_id: dict[str, tuple[TKey, TDesc]] = field(factory=dict)
    by_name: dict[Hashable, list[tuple[TKey, TDesc]]] = field(factory=dict)


def plan_resolution[TKey, TDesc, TEntity](
    descriptions: Sequence[Described[TKey, TDesc]],
    *,
    strong_owners: Mapping[str, TEntity],
    name_owners: Mapping[Hashable, Sequence[TEntity]],
    rules: ResolutionRules[TDesc, TEntity],
) -> dict[TKey, Outcome[TKey, TEntity]]:
    """Decide every description in order: reuse, create or defer.

    Per description: a strong id with a persisted owner is decided by the
    strong match alone — reuse, or defer when suspect. A strong id an earlier
    creation in this batch claimed folds onto that leader unless suspect, in
    which case it is created without the id, naming the leader in a
    ``Contest`` so the caller can queue the review once the leader is
    persisted. Only then do names count: the bucket's persisted owners
    and its leaders are priced in that order, and the first accepted one is
    reused. Anything else is a creation, which joins the bucket's leaders
    for the rest of the batch.

    ``name_owners`` is keyed by the descriptions' ``name_key``: under the
    recording gate, by what each *owner* normalizes to
    (``owners_by_identity``) — a candidate the probe reached on a looser
    form then keys differently from the description, which is exactly the
    pairing that gate refuses.
    """
    outcomes: dict[TKey, Outcome[TKey, TEntity]] = {}
    leaders = _Leaders[TKey, TDesc]()
    for item in descriptions:
        outcome = _plan_one(
            item,
            strong_owners=strong_owners,
            name_owners=name_owners,
            rules=rules,
            leaders=leaders,
        )
        outcomes[item.key] = outcome
        if outcome.kind != "create":
            # A deferred creation is not a leader: folding a later twin onto
            # it would settle by the back door what the review exists to ask.
            continue
        if outcome.strong_id is not None:
            leaders.by_strong_id[outcome.strong_id] = (item.key, item.description)
        if item.name_key is not None:
            leaders.by_name.setdefault(item.name_key, []).append((
                item.key,
                item.description,
            ))
    return outcomes


def price_reuse[TKey, TDesc, TEntity](
    description: TDesc,
    candidates: Iterable[TEntity],
    rules: ResolutionRules[TDesc, TEntity],
) -> Reuse[TKey, TEntity] | Refusal[TEntity] | None:
    """Price persisted candidates in order: the first accepted one is reused.

    Otherwise the first candidate priced and turned down is the refusal —
    the record that explains the creation that follows — and ``None``
    means nothing was comparable. The inward resolvers' reuse step calls
    this directly, one description at a time, with no leaders and no
    strong owners in play.
    """
    refusal: Refusal[TEntity] | None = None
    for candidate in candidates:
        evidence = rules.same(description, rules.describe(candidate))
        if evidence is None:
            continue
        if evidence.zone == "accept":
            return Reuse(evidence=evidence, canonical=candidate)
        if refusal is None:
            refusal = Refusal(candidate, evidence)
    return refusal


def _plan_one[TKey, TDesc, TEntity](
    item: Described[TKey, TDesc],
    *,
    strong_owners: Mapping[str, TEntity],
    name_owners: Mapping[Hashable, Sequence[TEntity]],
    rules: ResolutionRules[TDesc, TEntity],
    leaders: _Leaders[TKey, TDesc],
) -> Outcome[TKey, TEntity]:
    description = item.description
    if item.strong_id is not None:
        owner = strong_owners.get(item.strong_id)
        if owner is not None:
            evidence, suspect = rules.strong_match(description, rules.describe(owner))
            if not suspect:
                return Reuse(evidence=evidence, canonical=owner)
            return DeferToReview(
                owner=owner,
                review=evidence,
                create=Create(strong_id=None, evidence=rules.creation(description)),
            )
        leader = leaders.by_strong_id.get(item.strong_id)
        if leader is not None:
            leader_key, leader_description = leader
            evidence, suspect = rules.strong_match(description, leader_description)
            if not suspect:
                return Reuse(evidence=evidence, leader=leader_key)
            return Create(
                strong_id=None,
                evidence=rules.creation(description),
                contest=Contest(leader_key, evidence),
            )

    key = item.name_key
    if key is None:
        return Create(strong_id=item.strong_id, evidence=rules.creation(description))

    priced: Reuse[TKey, TEntity] | Refusal[TEntity] | None = price_reuse(
        description, name_owners.get(key, ()), rules
    )
    if isinstance(priced, Reuse):
        return priced
    for leader_key, leader_description in leaders.by_name.get(key, ()):
        evidence = rules.same(description, leader_description)
        if evidence is not None and evidence.zone == "accept":
            return Reuse(evidence=evidence, leader=leader_key)
    return Create(
        strong_id=item.strong_id,
        evidence=rules.creation(description),
        refusal=priced,
    )


# ---------------------------------------------------------------------------
# Track instantiation
# ---------------------------------------------------------------------------


def _internal(description: RecordingDescription) -> InternalTrackData:
    return {
        "title": description.title,
        "artists": [description.artist] if description.artist else [],
        "duration_ms": description.duration_ms,
    }


def _service(description: RecordingDescription) -> ServiceTrackData:
    return {
        "title": description.title,
        "artist": description.artist,
        "duration_ms": description.duration_ms,
    }


def _evaluator_for(rules: TrackResolutionRules) -> TrackMatchEvaluationService:
    return TrackMatchEvaluationService(rules.config)


@define(frozen=True, slots=True)
class TrackResolutionRules:
    """The track rules: ISRC as the strong id, ``describes_same_recording``
    as the name gate, and the Fellegi-Sunter model pricing every decision.

    ``names_alone`` swaps the name gate for the inward resolvers' identifier
    probe — the evaluator's accept plus a title-similarity floor — because
    a Last.fm identifier carries no duration for the recording gate to rule
    on. A candidate under the floor is priced and refused, not dropped: the
    model can score it 100 on the artist alone, and the refusal is the
    record that explains why that score did not reuse. The strong-id and
    creation pricing are the same either way.
    """

    config: MatchingConfig
    names_alone: bool = False
    # The one zoner: the evaluator's two threshold reads, so a zone recorded
    # here is the zone the matching pipeline would have assigned.
    _evaluator: TrackMatchEvaluationService = field(
        init=False, default=Factory(_evaluator_for, takes_self=True)
    )

    def describe(self, entity: Track) -> RecordingDescription:
        return describe_track(entity)

    def strong_match(
        self, description: RecordingDescription, owner: RecordingDescription
    ) -> tuple[ResolutionEvidence, bool]:
        duration_diff_ms = compute_duration_diff_ms(
            description.duration_ms, owner.duration_ms
        )
        suspect = assess_isrc_match_reliability(duration_diff_ms).suspect
        confidence, evidence = calculate_confidence(
            _internal(owner), _service(description), "isrc", self.config
        )
        # A suspect collision is a review by construction, whatever the model
        # says: a high score must not silently merge what the duration check
        # flagged (v0.8.18 FM2a/FM2c).
        zone: MatchZone = "review" if suspect else self._zone(confidence)
        method: MatchMethod = "isrc_suspect" if suspect else "isrc_match"
        return self._priced(method, confidence, zone, evidence), suspect

    def same(
        self, description: RecordingDescription, candidate: RecordingDescription
    ) -> ResolutionEvidence | None:
        # The recording gate first: a pair it refuses is never priced.
        if not self.names_alone and not describes_same_recording(
            candidate, description
        ):
            return None
        confidence, evidence = calculate_confidence(
            _internal(candidate), _service(description), "canonical_reuse", self.config
        )
        zone = self._zone(confidence)
        if (
            self.names_alone
            and evidence.title_similarity < self.config.high_similarity_threshold
        ):
            zone = "reject"
        return self._priced("canonical_reuse", confidence, zone, evidence)

    def creation(self, description: RecordingDescription) -> ResolutionEvidence:
        confidence, evidence = calculate_confidence(
            _internal(description), _service(description), "direct", self.config
        )
        return self._priced("direct", confidence, self._zone(confidence), evidence)

    def _zone(self, confidence: int) -> MatchZone:
        if self._evaluator.should_accept_match(confidence, "direct"):
            return "accept"
        if self._evaluator.should_review_match(confidence, "direct"):
            return "review"
        return "reject"

    @staticmethod
    def _priced(
        method: MatchMethod,
        confidence: int,
        zone: MatchZone,
        evidence: ConfidenceEvidence,
    ) -> ResolutionEvidence:
        return ResolutionEvidence(
            method=method,
            confidence=confidence,
            zone=zone,
            match_weight=evidence.match_weight,
            evidence=evidence.as_dict(),
        )


def plan_canonical_resolution[TKey](
    descriptions: Sequence[Described[TKey, RecordingDescription]],
    *,
    isrc_owners: Mapping[str, Track],
    name_owners: Mapping[Hashable, Sequence[Track]],
    config: MatchingConfig,
) -> dict[TKey, Outcome[TKey, Track]]:
    """``plan_resolution`` under the track rules."""
    return plan_resolution(
        descriptions,
        strong_owners=isrc_owners,
        name_owners=name_owners,
        rules=TrackResolutionRules(config),
    )


def suspect_review(
    owner: Track,
    priced: ResolutionEvidence,
    *,
    connector: str,
    connector_track_id: UUID,
    user_id: str,
) -> MatchReview:
    """The ``isrc_suspect`` review a collision queues against its owner.

    ``owner`` holds the contested ISRC — a deferral's ``owner`` or the
    persisted leader a ``Contest`` named; ``priced`` is the collision's
    price (``deferral.review`` or ``contest.evidence``).
    """
    return MatchReview(
        user_id=user_id,
        track_id=owner.id,
        connector_name=connector,
        connector_track_id=connector_track_id,
        match_method="isrc_suspect",
        confidence=priced.confidence,
        match_weight=priced.match_weight,
        confidence_evidence=priced.evidence,
    )


def track_name_key(description: RecordingDescription) -> Hashable | None:
    """The bucket a track description's name match requires, or None when
    it has no title or artist to compare."""
    if not (description.title and description.artist):
        return None
    return identity_key(description)


def undecided_name_pairs[TKey](
    described: Sequence[Described[TKey, RecordingDescription]],
    strong_owners: Mapping[str, object],
) -> list[tuple[str, str]]:
    """The (title, artist) pairs the name probe should ask about.

    Only what the ISRC step leaves undecided: an item whose strong id has
    a persisted owner is settled either way by that owner, and an item
    names may not decide has nothing to ask.
    """
    return [
        (item.description.title, item.description.artist)
        for item in described
        if item.name_key is not None
        and not (item.strong_id and item.strong_id in strong_owners)
    ]


def owners_by_identity(tracks: Iterable[Track]) -> dict[Hashable, list[Track]]:
    """Name-probe hits bucketed by what each *found canonical* normalizes to.

    Not by the probe pair that surfaced it: the probe also answers on a
    parenthetical-stripped form, and a candidate reached that way keys
    differently from the description — the pairing the recording gate
    refuses. One entry per canonical, however many pairs reached it.
    """
    owners: dict[Hashable, list[Track]] = {}
    seen: set[UUID] = set()
    for owner in tracks:
        if owner.id in seen:
            continue
        seen.add(owner.id)
        owners.setdefault(identity_key(describe_track(owner)), []).append(owner)
    return owners


__all__ = [
    "Contest",
    "Create",
    "DeferToReview",
    "Described",
    "Outcome",
    "Refusal",
    "ResolutionEvidence",
    "ResolutionRules",
    "Reuse",
    "TrackResolutionRules",
    "creation_of",
    "owners_by_identity",
    "plan_canonical_resolution",
    "plan_resolution",
    "price_reuse",
    "suspect_review",
    "track_name_key",
    "undecided_name_pairs",
]
