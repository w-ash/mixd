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
entity, keys a description, prices a strong-id match, prices a same-entity
match and prices a creation. ``TrackResolutionRules`` is the track
instantiation; an artist or album planner is another set of rules, not a
second copy of the walk.
"""

from collections.abc import Hashable, Mapping, Sequence
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

    ``names_decide`` is False when names may not settle this item — a
    relink the provider itself asserted, or a connector whose contract is
    strong-id-or-nothing. The strong id still decides it, and it neither
    folds onto a name leader nor leads a name bucket.
    """

    key: TKey
    description: TDesc
    strong_id: str | None = None
    names_decide: bool = True


@define(frozen=True, slots=True)
class Reuse[TKey, TEntity]:
    """Map onto an entity that already holds this recording.

    Exactly one of ``canonical`` (a persisted entity) or ``leader`` (the key
    of an earlier ``Create`` in the same batch) is set. Leaders are why a
    batch carrying an original and its remaster, neither yet known, mints
    one canonical and not two.

    ``refused``/``refusal`` carry the best persisted candidate the planner
    priced and turned down before the description folded onto a leader —
    the same record a ``Create`` carries, so a consumer that records
    refusals sees one per description whether or not a leader absorbed it.
    A reuse of a persisted canonical never carries one: the accepted
    candidate is the answer to the question the refusal would explain.
    """

    evidence: ResolutionEvidence
    canonical: TEntity | None = None
    leader: TKey | None = None
    refused: TEntity | None = None
    refusal: ResolutionEvidence | None = None
    kind: Literal["reuse"] = field(default="reuse", init=False)

    def __attrs_post_init__(self) -> None:
        if (self.canonical is None) == (self.leader is None):
            raise ValueError("Reuse names exactly one of canonical or leader")
        if (self.refused is None) != (self.refusal is None):
            raise ValueError("a refusal names both the candidate and the price")
        if self.canonical is not None and self.refused is not None:
            raise ValueError("a reuse of a persisted canonical carries no refusal")


@define(frozen=True, slots=True)
class Create[TKey, TEntity]:
    """Mint a new entity, claiming ``strong_id`` when it is not contested.

    ``refused``/``refusal`` carry the best same-entity candidate the planner
    priced and turned down, priced once here so a consumer that records
    refusals (the inward resolvers' refusal events) can explain the creation
    without re-running the comparison. A ``Reuse`` of a batch leader carries
    the same pair. The ingest service does not record them.

    ``contested_leader``/``contest`` name an earlier creation in the same
    batch whose strong id this description collided with as a suspect: the
    creation withholds the id, and the caller — which persists the leader
    before it acts on this outcome — queues the review against it.
    """

    strong_id: str | None
    evidence: ResolutionEvidence
    refused: TEntity | None = None
    refusal: ResolutionEvidence | None = None
    contested_leader: TKey | None = None
    contest: ResolutionEvidence | None = None
    kind: Literal["create"] = field(default="create", init=False)

    def __attrs_post_init__(self) -> None:
        if (self.contested_leader is None) != (self.contest is None):
            raise ValueError("a contested creation names both its leader and the price")
        if self.contested_leader is not None and self.strong_id is not None:
            raise ValueError("a contested creation must withhold the contested id")


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


type Outcome[TKey, TEntity] = (
    Reuse[TKey, TEntity] | Create[TKey, TEntity] | DeferToReview[TKey, TEntity]
)


class ResolutionRules[TDesc, TEntity](Protocol):
    """What the planner needs to know about one kind of entity."""

    def describe(self, entity: TEntity) -> TDesc:
        """An existing entity, as the same-entity question sees it."""
        ...

    def name_key(self, description: TDesc) -> Hashable | None:
        """The bucket key a name match requires, or None when uncomparable."""
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
    which case it is created without the id, naming the leader as
    ``contested_leader`` so the caller can queue the review once the leader
    is persisted. Only then do names count: the bucket's persisted owners
    and its leaders are priced in that order, and the first accepted one is
    reused. Anything else is a creation, which joins the bucket's leaders
    for the rest of the batch.

    ``name_owners`` is keyed by whatever ``rules.name_key`` answers for the
    descriptions: under a recording gate, by what each *owner* normalizes
    to — a candidate the probe reached on a looser form then keys
    differently from the description, which is exactly the pairing that
    gate refuses; under a names-alone gate, by the description that
    proposed it, so every proposed candidate is priced and a refusal is
    recorded.
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
        key = rules.name_key(item.description) if item.names_decide else None
        if key is not None:
            leaders.by_name.setdefault(key, []).append((item.key, item.description))
    return outcomes


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
                contested_leader=leader_key,
                contest=evidence,
            )

    key = rules.name_key(description) if item.names_decide else None
    if key is None:
        return Create(strong_id=item.strong_id, evidence=rules.creation(description))

    refused: TEntity | None = None
    refusal: ResolutionEvidence | None = None
    for candidate in name_owners.get(key, ()):
        evidence = rules.same(description, rules.describe(candidate))
        if evidence is None:
            continue
        if evidence.zone == "accept":
            return Reuse(evidence=evidence, canonical=candidate)
        if refusal is None:
            refused, refusal = candidate, evidence
    for leader_key, leader_description in leaders.by_name.get(key, ()):
        evidence = rules.same(description, leader_description)
        if evidence is not None and evidence.zone == "accept":
            return Reuse(
                evidence=evidence,
                leader=leader_key,
                refused=refused,
                refusal=refusal,
            )
    return Create(
        strong_id=item.strong_id,
        evidence=rules.creation(description),
        refused=refused,
        refusal=refusal,
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

    def name_key(self, description: RecordingDescription) -> Hashable | None:
        if not (description.title and description.artist):
            return None
        return identity_key(description)

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
        confidence, evidence = calculate_confidence(
            _internal(candidate), _service(description), "canonical_reuse", self.config
        )
        zone = self._zone(confidence)
        if self.names_alone:
            if evidence.title_similarity < self.config.high_similarity_threshold:
                zone = "reject"
        elif not describes_same_recording(candidate, description):
            return None
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


def review_for[TKey](
    deferral: DeferToReview[TKey, Track],
    *,
    connector: str,
    connector_track_id: UUID,
    user_id: str,
) -> MatchReview:
    """The review a deferred ISRC collision queues against the owner."""
    return _suspect_review(
        deferral.owner,
        deferral.review,
        connector=connector,
        connector_track_id=connector_track_id,
        user_id=user_id,
    )


def contest_review_for[TKey](
    create: Create[TKey, Track],
    leader: Track,
    *,
    connector: str,
    connector_track_id: UUID,
    user_id: str,
) -> MatchReview:
    """The review a suspect in-batch ISRC collision queues against its leader.

    ``leader`` is the persisted entity ``create.contested_leader`` named —
    the caller resolves the key, since only it knows what the key persisted
    as.
    """
    if create.contest is None:
        raise ValueError("only a contested creation has a review to queue")
    return _suspect_review(
        leader,
        create.contest,
        connector=connector,
        connector_track_id=connector_track_id,
        user_id=user_id,
    )


def _suspect_review(
    owner: Track,
    priced: ResolutionEvidence,
    *,
    connector: str,
    connector_track_id: UUID,
    user_id: str,
) -> MatchReview:
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


__all__ = [
    "Create",
    "DeferToReview",
    "Described",
    "Outcome",
    "ResolutionEvidence",
    "ResolutionRules",
    "Reuse",
    "TrackResolutionRules",
    "contest_review_for",
    "plan_canonical_resolution",
    "plan_resolution",
    "review_for",
]
