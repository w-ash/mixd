"""The resolution planner: every arm of "reuse, create, or ask a person".

Pure: the probes' answers are handed in as maps, and the outcomes are
asserted on directly. The track rules are exercised through
``plan_canonical_resolution`` so the arms are tested against the real
pricing, not a stub of it.
"""

from collections.abc import Hashable

from attrs import define

from src.config import create_matching_config
from src.domain.entities import Artist, Track
from src.domain.entities.track_mapping import MatchMethod
from src.domain.matching.canonical_resolution import (
    Create,
    DeferToReview,
    Described,
    Outcome,
    ResolutionEvidence,
    Reuse,
    TrackResolutionRules,
    plan_canonical_resolution,
    plan_resolution,
    review_for,
)
from src.domain.matching.recording_identity import (
    RecordingDescription,
    describe_track,
    identity_key,
)
from src.domain.matching.types import MatchZone
from tests.fixtures import TEST_USER_ID

CONFIG = create_matching_config()
ISRC = "GBEXH1900012"


def _described(
    key: str,
    *,
    title: str = "Ibrik",
    artist: str = "Bonobo",
    duration_ms: int | None = 245_733,
    isrc: str | None = None,
) -> Described[str, RecordingDescription]:
    return Described(
        key=key,
        description=RecordingDescription(title, artist, duration_ms),
        strong_id=isrc,
    )


def _canonical(
    *,
    title: str = "Ibrik",
    artist: str = "Bonobo",
    duration_ms: int | None = 245_733,
    isrc: str | None = None,
) -> Track:
    return Track(
        title=title,
        artists=[Artist(name=artist)],
        duration_ms=duration_ms,
        isrc=isrc,
        user_id=TEST_USER_ID,
    )


def _by_name(*owners: Track) -> dict[Hashable, list[Track]]:
    buckets: dict[Hashable, list[Track]] = {}
    for owner in owners:
        buckets.setdefault(identity_key(describe_track(owner)), []).append(owner)
    return buckets


def _plan(
    *items: Described[str, RecordingDescription],
    isrc_owners: dict[str, Track] | None = None,
    name_owners: dict[Hashable, list[Track]] | None = None,
) -> dict[str, Outcome[str, Track]]:
    return plan_canonical_resolution(
        list(items),
        isrc_owners=isrc_owners or {},
        name_owners=name_owners or {},
        config=CONFIG,
    )


class TestStrongIdOwners:
    def test_a_trustworthy_isrc_owner_is_reused(self):
        owner = _canonical(isrc=ISRC)

        (outcome,) = _plan(
            _described("a", isrc=ISRC), isrc_owners={ISRC: owner}
        ).values()

        assert isinstance(outcome, Reuse)
        assert outcome.canonical is owner
        assert outcome.leader is None
        assert outcome.evidence.method == "isrc_match"
        assert outcome.evidence.zone == "accept"

    def test_a_missing_duration_still_lets_the_isrc_decide(self):
        """No cross-check available: the ISRC's own assertion stands."""
        owner = _canonical(isrc=ISRC, duration_ms=None)

        (outcome,) = _plan(
            _described("a", isrc=ISRC), isrc_owners={ISRC: owner}
        ).values()

        assert isinstance(outcome, Reuse)
        assert outcome.canonical is owner

    def test_a_suspect_collision_defers_to_review_and_withholds_the_isrc(self):
        owner = _canonical(isrc=ISRC, duration_ms=200_000)

        (outcome,) = _plan(
            _described("a", isrc=ISRC, duration_ms=215_000),
            isrc_owners={ISRC: owner},
        ).values()

        assert isinstance(outcome, DeferToReview)
        assert outcome.owner is owner
        assert outcome.review.method == "isrc_suspect"
        assert outcome.review.zone == "review"
        assert outcome.create.strong_id is None
        assert outcome.create.evidence.method == "direct"

    def test_a_suspect_collision_is_never_folded_onto_a_name_match(self):
        """The review exists to put the question in front of a person; a
        name match must not settle it by the back door."""
        owner = _canonical(isrc=ISRC, duration_ms=200_000)
        twin = _canonical(duration_ms=215_000)

        (outcome,) = _plan(
            _described("a", isrc=ISRC, duration_ms=215_000),
            isrc_owners={ISRC: owner},
            name_owners=_by_name(twin),
        ).values()

        assert isinstance(outcome, DeferToReview)

    def test_the_review_names_the_owner_and_the_connector_track(self):
        owner = _canonical(isrc=ISRC, duration_ms=200_000)
        (outcome,) = _plan(
            _described("a", isrc=ISRC, duration_ms=215_000),
            isrc_owners={ISRC: owner},
        ).values()
        assert isinstance(outcome, DeferToReview)

        review = review_for(
            outcome, connector="spotify", connector_track_id=owner.id, user_id="u"
        )

        assert review.track_id == owner.id
        assert review.match_method == "isrc_suspect"
        assert review.confidence == outcome.review.confidence
        assert review.user_id == "u"


class TestStrongIdsInsideOneBatch:
    def test_a_later_twin_folds_onto_the_batch_leader(self):
        plan = _plan(_described("a", isrc=ISRC), _described("b", isrc=ISRC))

        assert isinstance(plan["a"], Create)
        assert plan["a"].strong_id == ISRC
        assert isinstance(plan["b"], Reuse)
        assert plan["b"].leader == "a"
        assert plan["b"].canonical is None
        assert plan["b"].evidence.method == "isrc_match"

    def test_a_suspect_in_batch_claim_creates_without_the_id_and_no_review(self):
        """The owner is unpersisted, so there is nothing to review against."""
        plan = _plan(
            _described("a", isrc=ISRC, duration_ms=200_000),
            _described("b", isrc=ISRC, duration_ms=215_000),
        )

        assert isinstance(plan["b"], Create)
        assert plan["b"].strong_id is None

    def test_a_deferred_creation_is_not_a_leader(self):
        owner = _canonical(isrc=ISRC, duration_ms=200_000)

        plan = _plan(
            _described("a", isrc=ISRC, duration_ms=215_000),
            _described("b", duration_ms=215_000),
            isrc_owners={ISRC: owner},
        )

        assert isinstance(plan["a"], DeferToReview)
        assert isinstance(plan["b"], Create)


class TestNameOwners:
    def test_a_remaster_with_its_own_isrc_reuses_the_original(self):
        original = _canonical(isrc=ISRC)

        (outcome,) = _plan(
            _described("a", isrc="USA2B2056087"), name_owners=_by_name(original)
        ).values()

        assert isinstance(outcome, Reuse)
        assert outcome.canonical is original
        assert outcome.evidence.method == "canonical_reuse"
        assert outcome.evidence.zone == "accept"

    def test_a_different_length_keeps_its_own_canonical(self):
        original = _canonical(duration_ms=235_400)

        (outcome,) = _plan(
            _described("a", duration_ms=138_213), name_owners=_by_name(original)
        ).values()

        assert isinstance(outcome, Create)
        assert outcome.refused is None

    def test_an_unknown_length_is_never_reused_on_names_alone(self):
        original = _canonical(duration_ms=None)

        (outcome,) = _plan(_described("a"), name_owners=_by_name(original)).values()

        assert isinstance(outcome, Create)

    def test_a_blank_artist_cannot_be_compared_and_creates(self):
        original = _canonical()

        (outcome,) = _plan(
            _described("a", artist=""), name_owners=_by_name(original)
        ).values()

        assert isinstance(outcome, Create)
        assert outcome.evidence.method == "direct"

    def test_a_creation_keeps_its_uncontested_isrc(self):
        (outcome,) = _plan(_described("a", isrc=ISRC)).values()

        assert isinstance(outcome, Create)
        assert outcome.strong_id == ISRC
        assert outcome.evidence.zone == "accept"


class TestNameLeaders:
    def test_two_pressings_in_one_batch_share_the_first_ones_canonical(self):
        plan = _plan(_described("a", isrc=ISRC), _described("b", isrc="USA2B2056087"))

        assert isinstance(plan["a"], Create)
        assert isinstance(plan["b"], Reuse)
        assert plan["b"].leader == "a"
        assert plan["b"].evidence.method == "canonical_reuse"

    def test_two_masters_of_different_lengths_each_keep_their_canonical(self):
        plan = _plan(
            _described("a", duration_ms=448_200), _described("b", duration_ms=216_000)
        )

        assert isinstance(plan["a"], Create)
        assert isinstance(plan["b"], Create)

    def test_the_first_creation_leads_the_bucket_for_every_later_twin(self):
        plan = _plan(_described("a"), _described("b"), _described("c"))

        assert isinstance(plan["b"], Reuse)
        assert plan["b"].leader == "a"
        assert isinstance(plan["c"], Reuse)
        assert plan["c"].leader == "a"


@define(frozen=True, slots=True)
class _NameRules:
    """Entity-generic check: rules over bare strings, pricing by a table.

    ``prices`` maps a candidate to the confidence ``same`` returns for it;
    a candidate absent from the table fails the gate. The planner never
    looks past the ``ResolutionRules`` surface, so this is the artist and
    album instantiation in miniature.
    """

    prices: dict[str, int]

    def describe(self, entity: str) -> str:
        return entity

    def name_key(self, description: str) -> Hashable | None:
        return description.lower() or None

    def strong_match(
        self, description: str, owner: str
    ) -> tuple[ResolutionEvidence, bool]:
        return self._priced("isrc_match", 100), description != owner

    def same(self, description: str, candidate: str) -> ResolutionEvidence | None:
        confidence = self.prices.get(candidate)
        if confidence is None:
            return None
        return self._priced("canonical_reuse", confidence)

    def creation(self, description: str) -> ResolutionEvidence:
        return self._priced("direct", 100)

    @staticmethod
    def _priced(method: MatchMethod, confidence: int) -> ResolutionEvidence:
        zone: MatchZone = "accept" if confidence >= 85 else "review"
        return ResolutionEvidence(method, confidence, zone, 0.0)


class TestRefusals:
    def test_a_priced_but_unaccepted_candidate_is_recorded_on_the_creation(self):
        outcomes = plan_resolution(
            [Described(key="k", description="Ibrik")],
            strong_owners={},
            name_owners={"ibrik": ["IBRIK", "Ibrik"]},
            rules=_NameRules(prices={"IBRIK": 60, "Ibrik": 70}),
        )

        outcome = outcomes["k"]
        assert isinstance(outcome, Create)
        # The first refusal is kept, not the best or the last.
        assert outcome.refused == "IBRIK"
        assert outcome.refusal is not None
        assert outcome.refusal.confidence == 60
        assert outcome.refusal.zone == "review"

    def test_the_first_accepted_candidate_wins_over_a_later_one(self):
        outcomes = plan_resolution(
            [Described(key="k", description="Ibrik")],
            strong_owners={},
            name_owners={"ibrik": ["IBRIK", "Ibrik"]},
            rules=_NameRules(prices={"IBRIK": 60, "Ibrik": 90}),
        )

        outcome = outcomes["k"]
        assert isinstance(outcome, Reuse)
        assert outcome.canonical == "Ibrik"

    def test_a_gated_out_candidate_leaves_no_refusal(self):
        outcomes = plan_resolution(
            [Described(key="k", description="Ibrik")],
            strong_owners={},
            name_owners={"ibrik": ["IBRIK"]},
            rules=_NameRules(prices={}),
        )

        outcome = outcomes["k"]
        assert isinstance(outcome, Create)
        assert outcome.refused is None

    def test_names_alone_refuses_a_low_title_similarity_outright(self):
        rules = TrackResolutionRules(CONFIG, names_alone=True)

        assert (
            rules.same(
                RecordingDescription("Ibrik", "Bonobo"),
                RecordingDescription("Kerala", "Bonobo"),
            )
            is None
        )

    def test_names_alone_prices_on_names_when_no_duration_is_known(self):
        """The recording gate would refuse an unknown length; the identifier
        probe (Last.fm carries no duration) prices the names instead."""
        rules = TrackResolutionRules(CONFIG, names_alone=True)

        evidence = rules.same(
            RecordingDescription("Ibrik", "Bonobo"),
            RecordingDescription("Ibrik", "Bonobo"),
        )

        assert evidence is not None
        assert evidence.method == "canonical_reuse"
        assert evidence.zone == "accept"


class TestOutcomeInvariants:
    def test_a_reuse_names_exactly_one_target(self):
        evidence = TrackResolutionRules(CONFIG).creation(
            RecordingDescription("Ibrik", "Bonobo")
        )
        try:
            _ = Reuse[str, Track](evidence=evidence)
        except ValueError:
            pass
        else:
            raise AssertionError("a Reuse with neither canonical nor leader")

    def test_a_deferred_creation_must_withhold_the_id(self):
        rules = TrackResolutionRules(CONFIG)
        owner = _canonical(isrc=ISRC)
        evidence = rules.creation(RecordingDescription("Ibrik", "Bonobo"))
        try:
            _ = DeferToReview(
                owner=owner,
                review=evidence,
                create=Create(strong_id=ISRC, evidence=evidence),
            )
        except ValueError:
            pass
        else:
            raise AssertionError("a deferred creation claimed the contested id")
