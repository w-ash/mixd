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

    def test_low_m_high_u_gives_negative_ratio(self):
        """Evidence against match."""
        level = ComparisonLevel("test", m_probability=0.05, u_probability=0.60)
        assert level.log_likelihood_ratio < 0

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


class TestClassifyTitle:
    """Test title comparison level classification."""

    def test_exact_match(self):
        assert classify_title(1.0, is_phonetic_match=False, is_variation=False) is TITLE_EXACT

    def test_variation_detected(self):
        assert classify_title(0.6, is_phonetic_match=False, is_variation=True) is TITLE_VARIATION

    def test_phonetic_match(self):
        assert classify_title(0.85, is_phonetic_match=True, is_variation=False) is TITLE_PHONETIC

    def test_high_fuzzy(self):
        assert classify_title(0.92, is_phonetic_match=False, is_variation=False) is TITLE_HIGH_FUZZY

    def test_moderate_fuzzy(self):
        assert classify_title(0.75, is_phonetic_match=False, is_variation=False) is TITLE_MODERATE_FUZZY

    def test_mismatch(self):
        assert classify_title(0.3, is_phonetic_match=False, is_variation=False) is TITLE_MISMATCH

    def test_variation_takes_precedence_over_phonetic(self):
        """Variation marker is a stronger signal than phonetic match."""
        assert classify_title(0.85, is_phonetic_match=True, is_variation=True) is TITLE_VARIATION

    def test_exact_takes_precedence_over_all(self):
        assert classify_title(1.0, is_phonetic_match=True, is_variation=True) is TITLE_EXACT


class TestClassifyArtist:
    """Test artist comparison level classification."""

    def test_exact_match(self):
        assert classify_artist(1.0, is_phonetic_match=False) is ARTIST_EXACT

    def test_phonetic_match(self):
        assert classify_artist(0.85, is_phonetic_match=True) is ARTIST_PHONETIC

    def test_high_fuzzy(self):
        assert classify_artist(0.92, is_phonetic_match=False) is ARTIST_HIGH_FUZZY

    def test_low_fuzzy(self):
        assert classify_artist(0.75, is_phonetic_match=False) is ARTIST_LOW_FUZZY

    def test_mismatch(self):
        assert classify_artist(0.3, is_phonetic_match=False) is ARTIST_MISMATCH


class TestClassifyDuration:
    """Test duration comparison level classification."""

    def test_missing_duration(self):
        assert classify_duration(None) is DURATION_MISSING

    def test_close_match(self):
        assert classify_duration(500) is DURATION_CLOSE

    def test_exactly_at_close_boundary(self):
        assert classify_duration(1_000) is DURATION_CLOSE

    def test_near_match(self):
        assert classify_duration(2_000) is DURATION_NEAR

    def test_moderate_match(self):
        assert classify_duration(5_000) is DURATION_MODERATE

    def test_mismatch(self):
        assert classify_duration(15_000) is DURATION_MISMATCH

    def test_zero_diff(self):
        assert classify_duration(0) is DURATION_CLOSE


class TestClassifyISRC:
    """Test ISRC comparison level classification."""

    def test_exact_match(self):
        assert classify_isrc(isrc_matched=True, isrc_suspect=False, isrc_available=True) is ISRC_EXACT

    def test_suspect_match(self):
        assert classify_isrc(isrc_matched=True, isrc_suspect=True, isrc_available=True) is ISRC_SUSPECT

    def test_not_available(self):
        assert classify_isrc(isrc_matched=False, isrc_suspect=False, isrc_available=False) is ISRC_ABSENT

    def test_available_but_no_match(self):
        """ISRC available but didn't match — treat as neutral (absent)."""
        assert classify_isrc(isrc_matched=False, isrc_suspect=False, isrc_available=True) is ISRC_ABSENT


class TestCalculateMatchWeight:
    """Test match weight aggregation."""

    def test_empty_results(self):
        assert calculate_match_weight([]) == 0.0

    def test_single_attribute(self):
        results = [AttributeResult("title", TITLE_EXACT)]
        assert calculate_match_weight(results) == pytest.approx(
            TITLE_EXACT.log_likelihood_ratio
        )

    def test_multiple_attributes_sum(self):
        results = [
            AttributeResult("title", TITLE_EXACT),
            AttributeResult("artist", ARTIST_EXACT),
            AttributeResult("duration", DURATION_CLOSE),
        ]
        expected = sum(r.level.log_likelihood_ratio for r in results)
        assert calculate_match_weight(results) == pytest.approx(expected)

    def test_mismatches_reduce_weight(self):
        """Mismatches contribute negative log-likelihood ratios."""
        good = [
            AttributeResult("title", TITLE_EXACT),
            AttributeResult("artist", ARTIST_EXACT),
        ]
        bad = [
            AttributeResult("title", TITLE_MISMATCH),
            AttributeResult("artist", ARTIST_MISMATCH),
        ]
        assert calculate_match_weight(good) > calculate_match_weight(bad)

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

    def test_zero_weight_gives_50(self):
        """No evidence = 50% confidence (prior)."""
        assert weight_to_confidence(0.0) == 50

    def test_positive_weight_above_50(self):
        assert weight_to_confidence(2.0) > 50

    def test_negative_weight_below_50(self):
        assert weight_to_confidence(-2.0) < 50

    def test_very_large_positive_near_100(self):
        assert weight_to_confidence(15.0) >= 99

    def test_very_large_negative_near_0(self):
        assert weight_to_confidence(-15.0) <= 1

    def test_bounded_at_0(self):
        assert weight_to_confidence(-100.0) >= 0

    def test_bounded_at_100(self):
        assert weight_to_confidence(100.0) <= 100

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

    def test_isrc_perfect_match_scores_very_high(self):
        """ISRC match with matching metadata should score >95."""
        results = [
            AttributeResult("isrc", ISRC_EXACT),
            AttributeResult("title", TITLE_EXACT),
            AttributeResult("artist", ARTIST_EXACT),
            AttributeResult("duration", DURATION_CLOSE),
        ]
        weight = calculate_match_weight(results)
        confidence = weight_to_confidence(weight)
        assert confidence > 95

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

    def test_suspect_isrc_scores_lower_than_clean(self):
        """Suspect ISRC should produce lower match weight than clean ISRC."""
        clean = [
            AttributeResult("isrc", ISRC_EXACT),
            AttributeResult("title", TITLE_HIGH_FUZZY),
            AttributeResult("artist", ARTIST_HIGH_FUZZY),
            AttributeResult("duration", DURATION_CLOSE),
        ]
        suspect = [
            AttributeResult("isrc", ISRC_SUSPECT),
            AttributeResult("title", TITLE_HIGH_FUZZY),
            AttributeResult("artist", ARTIST_HIGH_FUZZY),
            AttributeResult("duration", DURATION_MISMATCH),
        ]
        # Compare raw weights since sigmoid saturates near 100 for strong matches
        assert calculate_match_weight(clean) > calculate_match_weight(suspect)

    def test_phonetic_match_scores_between_exact_and_fuzzy(self):
        """Phonetic match should score better than fuzzy-only but below exact."""
        exact = [
            AttributeResult("artist", ARTIST_EXACT),
            AttributeResult("title", TITLE_EXACT),
            AttributeResult("duration", DURATION_CLOSE),
        ]
        phonetic = [
            AttributeResult("artist", ARTIST_PHONETIC),
            AttributeResult("title", TITLE_PHONETIC),
            AttributeResult("duration", DURATION_CLOSE),
        ]
        fuzzy = [
            AttributeResult("artist", ARTIST_HIGH_FUZZY),
            AttributeResult("title", TITLE_HIGH_FUZZY),
            AttributeResult("duration", DURATION_CLOSE),
        ]
        exact_conf = weight_to_confidence(calculate_match_weight(exact))
        phonetic_conf = weight_to_confidence(calculate_match_weight(phonetic))
        fuzzy_conf = weight_to_confidence(calculate_match_weight(fuzzy))

        assert exact_conf >= phonetic_conf >= fuzzy_conf

    def test_missing_duration_is_neutral(self):
        """Missing duration should not significantly affect score."""
        with_duration = [
            AttributeResult("title", TITLE_EXACT),
            AttributeResult("artist", ARTIST_EXACT),
            AttributeResult("duration", DURATION_CLOSE),
        ]
        without_duration = [
            AttributeResult("title", TITLE_EXACT),
            AttributeResult("artist", ARTIST_EXACT),
            AttributeResult("duration", DURATION_MISSING),
        ]
        conf_with = weight_to_confidence(calculate_match_weight(with_duration))
        conf_without = weight_to_confidence(calculate_match_weight(without_duration))

        # Should be close — duration missing is neutral (log(1) = 0)
        assert abs(conf_with - conf_without) < 10
