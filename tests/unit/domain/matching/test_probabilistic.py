"""Tests for Fellegi-Sunter probabilistic scoring model.

Verifies comparison level classification, log-likelihood ratio computation,
match weight aggregation, and sigmoid conversion to 0-100 confidence scores.
"""

import math

import pytest

from src.domain.matching.probabilistic import (
    ARTIST_EXACT,
    ARTIST_HIGH_FUZZY,
    ARTIST_LOW_FUZZY,
    ARTIST_MISMATCH,
    ARTIST_MISSING,
    ARTIST_PHONETIC,
    DURATION_CLOSE,
    DURATION_MISMATCH,
    DURATION_MISSING,
    DURATION_MODERATE,
    DURATION_NEAR,
    ISRC_ABSENT,
    ISRC_EXACT,
    ISRC_SUSPECT,
    TITLE_EXACT,
    TITLE_HIGH_FUZZY,
    TITLE_MISMATCH,
    TITLE_MISSING,
    TITLE_MODERATE_FUZZY,
    TITLE_PHONETIC,
    TITLE_VARIATION,
    AttributeResult,
    ComparisonLevel,
    calculate_match_weight,
    classify_artist,
    classify_duration,
    classify_isrc,
    classify_title,
    weight_to_confidence,
)


class TestComparisonLevel:
    """Test ComparisonLevel log-likelihood ratio computation."""

    def test_high_m_low_u_gives_positive_ratio(self):
        """Rare random agreement with high match agreement = strong evidence."""
        level = ComparisonLevel("test", m_probability=0.99, u_probability=0.001)
        assert level.log_likelihood_ratio > 0
        assert level.log_likelihood_ratio == pytest.approx(math.log(0.99 / 0.001))

    def test_equal_m_u_gives_zero_ratio(self):
        """Equal probabilities = no discriminating power."""
        level = ComparisonLevel("test", m_probability=0.50, u_probability=0.50)
        assert level.log_likelihood_ratio == pytest.approx(0.0)

    def test_zero_u_probability_gives_max_ratio(self):
        level = ComparisonLevel("test", m_probability=0.99, u_probability=0.0)
        assert level.log_likelihood_ratio == 15.0

    def test_zero_m_probability_gives_min_ratio(self):
        level = ComparisonLevel("test", m_probability=0.0, u_probability=0.50)
        assert level.log_likelihood_ratio == -15.0

    def test_isrc_exact_has_highest_discriminating_power(self):
        """ISRC exact match should be the strongest single signal."""
        assert ISRC_EXACT.log_likelihood_ratio > TITLE_EXACT.log_likelihood_ratio
        assert ISRC_EXACT.log_likelihood_ratio > ARTIST_EXACT.log_likelihood_ratio


class TestDerivedLogLikelihoodRatio:
    """``log_likelihood_ratio`` is computed once at construction, not per access.

    Several reads happen per confidence evaluation against frozen module-level
    singletons, so the ratio is stored rather than recomputed.
    """

    def test_value_is_stored_not_recomputed(self):
        first = TITLE_EXACT.log_likelihood_ratio
        assert TITLE_EXACT.log_likelihood_ratio is first


class TestClassifyTitle:
    """Test title comparison level classification."""

    @pytest.mark.parametrize(
        ("similarity", "phonetic", "variation", "expected"),
        [
            (1.0, False, False, TITLE_EXACT),
            (1.0, True, True, TITLE_EXACT),  # exact outranks every flag
            (0.6, False, True, TITLE_VARIATION),
            (0.85, True, True, TITLE_VARIATION),  # variation outranks phonetic
            (0.85, True, False, TITLE_PHONETIC),
            (0.9, False, False, TITLE_HIGH_FUZZY),  # at the high threshold
            (0.89, False, False, TITLE_MODERATE_FUZZY),
            (0.7, False, False, TITLE_MODERATE_FUZZY),  # at the moderate bound
            (0.69, False, False, TITLE_MISMATCH),
        ],
    )
    def test_similarity_and_flags_pick_the_level(
        self, similarity, phonetic, variation, expected
    ):
        level = classify_title(
            similarity, is_phonetic_match=phonetic, is_variation=variation
        )
        assert level is expected

    def test_missing_title_is_neutral(self):
        """None similarity (nothing to compare) is MISSING — LLR exactly 0."""
        level = classify_title(None, is_phonetic_match=False, is_variation=False)
        assert level is TITLE_MISSING
        assert level.log_likelihood_ratio == 0.0


class TestClassifyArtist:
    """Test artist comparison level classification."""

    @pytest.mark.parametrize(
        ("similarity", "phonetic", "expected"),
        [
            (1.0, True, ARTIST_EXACT),  # exact outranks the phonetic flag
            (0.85, True, ARTIST_PHONETIC),
            (0.9, False, ARTIST_HIGH_FUZZY),  # at the high threshold
            (0.89, False, ARTIST_LOW_FUZZY),
            (0.7, False, ARTIST_LOW_FUZZY),  # at the moderate bound
            (0.69, False, ARTIST_MISMATCH),
        ],
    )
    def test_similarity_and_flag_pick_the_level(self, similarity, phonetic, expected):
        assert classify_artist(similarity, is_phonetic_match=phonetic) is expected

    def test_missing_artist_is_neutral(self):
        """None similarity (nothing to compare) is MISSING — LLR exactly 0."""
        level = classify_artist(None, is_phonetic_match=False)
        assert level is ARTIST_MISSING
        assert level.log_likelihood_ratio == 0.0


class TestClassifyDuration:
    """Test duration comparison level classification: bounds are inclusive."""

    @pytest.mark.parametrize(
        ("diff_ms", "expected"),
        [
            (None, DURATION_MISSING),
            (0, DURATION_CLOSE),
            (1_000, DURATION_CLOSE),
            (1_001, DURATION_NEAR),
            (3_000, DURATION_NEAR),
            (3_001, DURATION_MODERATE),
            (10_000, DURATION_MODERATE),
            (10_001, DURATION_MISMATCH),
        ],
    )
    def test_difference_picks_the_level(self, diff_ms, expected):
        assert classify_duration(diff_ms) is expected


class TestClassifyISRC:
    """Test ISRC comparison level classification."""

    @pytest.mark.parametrize(
        ("matched", "suspect", "available", "expected"),
        [
            (True, False, True, ISRC_EXACT),
            (True, True, True, ISRC_SUSPECT),
            (False, False, False, ISRC_ABSENT),
            # Available but not matched is neutral, not evidence against.
            (False, False, True, ISRC_ABSENT),
        ],
    )
    def test_flags_pick_the_level(self, matched, suspect, available, expected):
        level = classify_isrc(
            isrc_matched=matched, isrc_suspect=suspect, isrc_available=available
        )
        assert level is expected


class TestCalculateMatchWeight:
    """Test match weight aggregation."""

    def test_empty_results(self):
        assert calculate_match_weight([]) == 0.0

    def test_multiple_attributes_sum(self):
        """ln(0.95/0.005) + ln(0.95/0.002) + ln(0.95/0.10) from the m/u table."""
        results = [
            AttributeResult("title", TITLE_EXACT),
            AttributeResult("artist", ARTIST_EXACT),
            AttributeResult("duration", DURATION_CLOSE),
        ]
        assert calculate_match_weight(results) == pytest.approx(13.6616, abs=1e-4)

    def test_isrc_dominates_weight(self):
        """ISRC exact match alone should contribute more than title+artist mismatch."""
        isrc_only = [
            AttributeResult("isrc", ISRC_EXACT),
            AttributeResult("title", TITLE_MISMATCH),
            AttributeResult("artist", ARTIST_MISMATCH),
        ]
        # ISRC is so strong it should still be positive even with mismatches
        assert calculate_match_weight(isrc_only) > 0


class TestWeightToConfidence:
    """Test sigmoid conversion from match weight to 0-100 confidence."""

    @pytest.mark.parametrize(
        ("weight", "confidence"),
        [
            (0.0, 50),  # no evidence: the prior
            (2.0, 88),  # 1 / (1 + e^-2) = 0.8808
            (-2.0, 12),
            (-0.5, 38),  # 37.75 rounds, not truncates
            (15.0, 100),
            (-15.0, 0),
            (100.0, 100),
            (-100.0, 0),
        ],
    )
    def test_sigmoid_maps_weight_to_a_rounded_percentage(self, weight, confidence):
        assert weight_to_confidence(weight) == confidence

    def test_monotonic(self):
        """Higher weight should always give higher or equal confidence."""
        weights = [-10, -5, -2, -1, 0, 1, 2, 5, 10]
        confidences = [weight_to_confidence(w) for w in weights]
        for i in range(len(confidences) - 1):
            assert confidences[i] <= confidences[i + 1]


class TestEndToEndScoring:
    """Integration-style tests for realistic matching scenarios."""

    def test_perfect_match_scores_high(self):
        """Identical track across services should score >90."""
        results = [
            AttributeResult("title", TITLE_EXACT),
            AttributeResult("artist", ARTIST_EXACT),
            AttributeResult("duration", DURATION_CLOSE),
        ]
        weight = calculate_match_weight(results)
        confidence = weight_to_confidence(weight)
        assert confidence > 90

    def test_completely_different_tracks_score_low(self):
        """Non-matching tracks should score <20."""
        results = [
            AttributeResult("title", TITLE_MISMATCH),
            AttributeResult("artist", ARTIST_MISMATCH),
            AttributeResult("duration", DURATION_MISMATCH),
        ]
        weight = calculate_match_weight(results)
        confidence = weight_to_confidence(weight)
        assert confidence < 20

    @pytest.mark.parametrize(
        "tiers",
        [
            (TITLE_EXACT, TITLE_PHONETIC, TITLE_HIGH_FUZZY, TITLE_MODERATE_FUZZY),
            (ARTIST_EXACT, ARTIST_PHONETIC, ARTIST_HIGH_FUZZY, ARTIST_LOW_FUZZY),
        ],
        ids=["title", "artist"],
    )
    def test_phonetic_ranks_between_exact_and_fuzzy(self, tiers):
        """Each tier is strictly weaker evidence than the one above it.

        Compared on log-likelihood ratios: the sigmoid saturates at 100 for
        all three tiers once title, artist and duration agree, which would
        hide an inverted tier.
        """
        ratios = [level.log_likelihood_ratio for level in tiers]
        assert ratios == sorted(ratios, reverse=True)
        assert len(set(ratios)) == len(ratios)
