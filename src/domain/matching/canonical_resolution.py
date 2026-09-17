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
    """One incoming item: its batch key, its description and its strong id."""

    key: TKey
    description: TDesc
    strong_id: str | None = None


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


@define(frozen=True, slots=True)
class Create[TEntity]:
    """Mint a new entity, claiming ``strong_id`` when it is not contested.

    ``refused``/``refusal`` record the best same-entity candidate the planner
    priced and turned down, so the creation can be explained (and the
    refusal recorded as a negative) without re-running the comparison.
    """

    strong_id: str | None
    evidence: ResolutionEvidence
    refused: TEntity | None = None
    refusal: ResolutionEvidence | None = None
    kind: Literal["create"] = field(default="create", init=False)


@define(frozen=True, slots=True)
class DeferToReview[TEntity]:
    """A suspect strong-id collision: create without the id and ask a person.

    ``owner`` holds the contested strong id; ``review`` is the price of the
    collision the review shows; ``create`` is the creation that happens
    meanwhile, always with ``strong_id`` withheld.
    """

    owner: TEntity
    review: ResolutionEvidence
    create: Create[TEntity]
    kind: Literal["defer_to_review"] = field(default="defer_to_review", init=False)

    def __attrs_post_init__(self) -> None:
        if self.create.strong_id is not None:
            raise ValueError("a deferred creation must withhold the contested id")


type Outcome[TKey, TEntity] = (
    Reuse[TKey, TEntity] | Create[TEntity] | DeferToReview[TEntity]
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
        """Price a name-bucket candidate, or None when the gate refuses it."""
        ...

    def creation(self, description: TDesc) -> ResolutionEvidence:
        """Price a creation — the description vouching for itself."""
        ...


@define(slots=True)
class _Leaders[TKey, TDesc]:
    """The creations earlier in the batch that later items may fold onto."""

    by_strong_id: dict[str, tuple[TKey, TDesc]] = field(factory=dict)
    by_name: dict[Hashable, tuple[TKey, TDesc]] = field(factory=dict)


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
    which case it is created without the id and no review is queued (the
    owner is not persisted yet, so there is nothing to review against). Only
    then do names count: the bucket's persisted owners and its leader are
    priced in that order, and the first accepted one is reused. Anything
    else is a creation, which registers as the bucket leader for the rest of
    the batch.

    ``name_owners`` is keyed by what each *owner* normalizes to — the probe
    that proposed it may have answered on a looser form, and a candidate
    reached that way keys differently from the description, which is
    exactly the pairing the same-entity gate refuses.
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
        key = rules.name_key(item.description)
        if key is not None:
            _ = leaders.by_name.setdefault(key, (item.key, item.description))
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
            return Create(strong_id=None, evidence=rules.creation(description))

    key = rules.name_key(description)
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
    leader = leaders.by_name.get(key)
    if leader is not None:
        leader_key, leader_description = leader
        evidence = rules.same(description, leader_description)
        if evidence is not None and evidence.zone == "accept":
            return Reuse(evidence=evidence, leader=leader_key)
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
    on. The strong-id and creation pricing are the same either way.
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
        if self.names_alone:
            if evidence.title_similarity < self.config.high_similarity_threshold:
                return None
        elif not describes_same_recording(candidate, description):
            return None
        return self._priced(
            "canonical_reuse", confidence, self._zone(confidence), evidence
        )

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


def review_for(
    deferral: DeferToReview[Track],
    *,
    connector: str,
    connector_track_id: UUID,
    user_id: str,
) -> MatchReview:
    """The review a deferred ISRC collision queues against the owner."""
    return MatchReview(
        user_id=user_id,
        track_id=deferral.owner.id,
        connector_name=connector,
        connector_track_id=connector_track_id,
        match_method="isrc_suspect",
        confidence=deferral.review.confidence,
        match_weight=deferral.review.match_weight,
        confidence_evidence=deferral.review.evidence,
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
    "plan_canonical_resolution",
    "plan_resolution",
    "review_for",
]
