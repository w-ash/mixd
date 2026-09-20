"""Artist evidence pricing: tier order, the identity floor, and the Last.fm cap.

These are properties rather than pinned numbers. What matters is that an id
anchor can auto-accept, a curated alias can auto-accept, Last.fm evidence
never can however good the name looks, and the tiers stay in order — all of
which survive a recalibration of the underlying m/u probabilities, while a
pinned score would not.
"""

import pytest

from src.domain.matching.artist_confidence import calculate_artist_confidence
from src.domain.matching.config import MatchingConfig
from src.domain.matching.evaluation_service import MatchEvaluationService
from src.domain.matching.types import ArtistEvidenceLevel

# Mirrors the shipped settings defaults: the Last.fm cap is calibrated to land
# inside this review band, so the band is part of what these tests assert.
ACCEPT_THRESHOLD = 85
REVIEW_THRESHOLD = 50

SIMILARITIES = (0.0, 0.3, 0.5, 0.7, 0.85, 0.95, 1.0)


def make_config() -> MatchingConfig:
    return MatchingConfig(
        identical_similarity_score=1.0,
        variation_similarity_score=0.6,
        auto_accept_threshold=ACCEPT_THRESHOLD,
        review_threshold=REVIEW_THRESHOLD,
        high_similarity_threshold=0.9,
        phonetic_similarity_score=0.85,
    )


def evaluator() -> MatchEvaluationService:
    return MatchEvaluationService(config=make_config())


class TestIdentityGradeEvidence:
    @pytest.mark.parametrize("level", ["connector_id", "mbid"])
    def test_an_id_anchor_auto_accepts(self, level: ArtistEvidenceLevel):
        evidence = calculate_artist_confidence(level, config=make_config())
        assert evaluator().should_accept_match(evidence.final_score, "mb_url_rel")

    def test_a_curated_alias_auto_accepts(self):
        evidence = calculate_artist_confidence("alias_name", config=make_config())
        assert evaluator().should_accept_match(evidence.final_score, "mb_url_rel")

    def test_an_id_anchor_ignores_the_name_string(self):
        weak_name = calculate_artist_confidence(
            "connector_id", name_similarity=0.0, config=make_config()
        )
        no_name = calculate_artist_confidence("connector_id", config=make_config())
        assert weak_name.match_weight == no_name.match_weight


class TestLastfmCap:
    @pytest.mark.parametrize("similarity", SIMILARITIES)
    def test_a_name_match_never_auto_accepts(self, similarity: float):
        evidence = calculate_artist_confidence(
            "name", name_similarity=similarity, lastfm=True, config=make_config()
        )
        assert not evaluator().should_accept_match(evidence.final_score, "artist_title")

    @pytest.mark.parametrize("level", ["connector_id", "mbid", "alias_name", "name"])
    def test_the_cap_applies_whatever_lastfm_claims(self, level: ArtistEvidenceLevel):
        # Last.fm's own mbid flips with ``autocorrect``, so even an id-level
        # claim from Last.fm is capped evidence.
        evidence = calculate_artist_confidence(
            level, name_similarity=1.0, lastfm=True, config=make_config()
        )
        assert evidence.final_score < ACCEPT_THRESHOLD

    def test_a_perfect_capped_name_still_reaches_review(self):
        evidence = calculate_artist_confidence(
            "name", name_similarity=1.0, lastfm=True, config=make_config()
        )
        assert evaluator().should_review_match(evidence.final_score, "artist_title")

    def test_the_cap_is_a_ceiling_not_a_floor(self):
        capped = calculate_artist_confidence(
            "name", name_similarity=0.1, lastfm=True, config=make_config()
        )
        uncapped = calculate_artist_confidence(
            "name", name_similarity=0.1, config=make_config()
        )
        assert capped.match_weight == uncapped.match_weight
        assert capped.final_score < REVIEW_THRESHOLD


class TestOrdering:
    def test_levels_are_strictly_ordered_by_weight(self):
        config = make_config()
        weights = [
            calculate_artist_confidence("connector_id", config=config).match_weight,
            calculate_artist_confidence("mbid", config=config).match_weight,
            calculate_artist_confidence("alias_name", config=config).match_weight,
            calculate_artist_confidence(
                "name", name_similarity=0.95, config=config
            ).match_weight,
            calculate_artist_confidence(
                "name", name_similarity=0.75, config=config
            ).match_weight,
            calculate_artist_confidence(
                "name", name_similarity=1.0, lastfm=True, config=config
            ).match_weight,
            calculate_artist_confidence(
                "name", name_similarity=0.1, config=config
            ).match_weight,
        ]
        assert weights == sorted(weights, reverse=True)
        assert len(set(weights)) == len(weights)

    def test_a_missing_name_is_neutral_rather_than_a_mismatch(self):
        config = make_config()
        missing = calculate_artist_confidence("name", config=config)
        mismatch = calculate_artist_confidence(
            "name", name_similarity=0.1, config=config
        )
        assert missing.match_weight > mismatch.match_weight


class TestEvidenceSerialization:
    def test_as_dict_keeps_the_level_and_score_and_omits_falsy_flags(self):
        evidence = calculate_artist_confidence("mbid", config=make_config())
        stored = evidence.as_dict()
        assert stored["level"] == "mbid"
        assert stored["final_score"] == evidence.final_score
        assert "lastfm" not in stored
        assert "name_similarity" not in stored

    def test_as_dict_rounds_the_similarity_and_records_the_cap(self):
        evidence = calculate_artist_confidence(
            "name", name_similarity=0.876_54, lastfm=True, config=make_config()
        )
        stored = evidence.as_dict()
        assert stored["name_similarity"] == 0.88
        assert stored["lastfm"] is True
        assert isinstance(stored["match_weight"], float)
