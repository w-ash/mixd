"""Tests for domain matching algorithms.

These tests verify the pure business logic of track matching and confidence scoring.
"""

import operator

import pytest

from src.config import create_matching_config
from src.domain.matching import ConfidenceEvidence, MatchResult
from src.domain.matching.algorithms import (
    calculate_confidence,
    calculate_title_similarity,
    select_best_by_title_similarity,
)

config = create_matching_config()


class TestCalculateTitleSimilarity:
    """Test cases for title similarity calculation."""

    @pytest.mark.parametrize(
        ("title1", "title2"),
        [
            ("Paranoid Android", "Paranoid Android"),
            ("Paranoid Android", "paranoid android"),
        ],
    )
    def test_titles_equal_ignoring_case_score_identical(self, title1, title2):
        assert calculate_title_similarity(title1, title2, config) == 1.0

    @pytest.mark.parametrize(
        ("title1", "title2"),
        [
            ("Paranoid Android", "Paranoid Android - Live"),
            ("Karma Police", "Karma Police (Remix)"),
            # The longer title can sit on either side of the comparison.
            ("Karma Police (Remix)", "Karma Police"),
        ],
    )
    def test_a_variation_marker_scores_as_a_variation(self, title1, title2):
        """A title plus a variation marker is a variant, penalized to 0.6."""
        assert calculate_title_similarity(title1, title2, config) == 0.6

    def test_completely_different_titles(self):
        """Test that completely different titles return low similarity."""
        result = calculate_title_similarity("Paranoid Android", "Yesterday", config)
        assert result < 0.3  # Should be very low similarity


class TestCalculateConfidence:
    """Test cases for Fellegi-Sunter probabilistic confidence calculation.

    The model uses log-likelihood ratios instead of additive penalties.
    Each attribute comparison contributes evidence for or against a match.
    Final scores are sigmoid-mapped to 0-100.
    """

    def test_perfect_isrc_match(self):
        """ISRC match with identical metadata: every attribute at its top level.

        Each score is the level's log-likelihood ratio ln(m/u) from the
        Fellegi-Sunter table: title exact ln(0.95/0.005), artist exact
        ln(0.95/0.002), duration close ln(0.95/0.10), ISRC exact
        ln(0.99/0.0001). Their sum saturates the sigmoid at 100.
        """
        internal_track = {
            "title": "Paranoid Android",
            "artists": ["Radiohead"],
            "duration_ms": 386000,
        }
        service_track = {
            "title": "Paranoid Android",
            "artist": "Radiohead",
            "duration_ms": 386000,
        }

        confidence, evidence = calculate_confidence(
            internal_track, service_track, "isrc", config
        )

        assert confidence == 100
        assert evidence.as_dict() == {
            "base_score": 100,
            "title_score": 5.25,
            "artist_score": 6.16,
            "duration_score": 2.25,
            "title_similarity": 1.0,
            "artist_similarity": 1.0,
            "duration_diff_ms": 0,
            "final_score": 100,
            "match_weight": 22.8619,
        }

    def test_good_artist_title_match(self):
        """Good artist/title matches should score well."""
        internal_track = {
            "title": "Karma Police",
            "artists": ["Radiohead"],
            "duration_ms": 261000,
        }
        service_track = {
            "title": "Karma Police",
            "artist": "Radiohead",
            "duration_ms": 261500,  # Slight duration difference
        }

        confidence, evidence = calculate_confidence(
            internal_track, service_track, "artist_title", config
        )

        assert confidence >= 90  # High for exact title/artist + close duration
        assert evidence.title_similarity == 1.0  # Perfect title match
        assert evidence.match_weight > 0

    def test_variation_match_penalty(self):
        """Title variations should produce lower match weight than exact matches."""
        internal_track = {
            "title": "Creep",
            "artists": ["Radiohead"],
            "duration_ms": 238000,
        }
        service_track = {
            "title": "Creep - Live",
            "artist": "Radiohead",
            "duration_ms": 245000,
        }

        _, variation_evidence = calculate_confidence(
            internal_track, service_track, "artist_title", config
        )

        assert variation_evidence.title_similarity == 0.6  # Variation similarity score
        # Variation should have lower match weight than a perfect match
        _, perfect_evidence = calculate_confidence(
            internal_track,
            {"title": "Creep", "artist": "Radiohead", "duration_ms": 238000},
            "artist_title",
            config,
        )
        assert variation_evidence.match_weight < perfect_evidence.match_weight

    def test_missing_duration_is_neutral(self):
        """Missing duration contributes neutral evidence (no penalty, no boost)."""
        internal_track = {
            "title": "High and Dry",
            "artists": ["Radiohead"],
            "duration_ms": None,  # Missing duration
        }
        service_track = {
            "title": "High and Dry",
            "artist": "Radiohead",
            "duration_ms": 256000,
        }

        confidence, evidence = calculate_confidence(
            internal_track, service_track, "artist_title", config
        )

        # Duration missing is neutral in Fellegi-Sunter (log(0.5/0.5) = 0)
        assert evidence.duration_score == 0.0
        assert evidence.duration_missing is True
        # Should still score well from title + artist
        assert confidence >= 85

    def test_missing_title_and_artist_are_neutral(self):
        """Empty service title/artist classify as MISSING (LLR 0), not mismatch.

        An empty-metadata ISRC match therefore scores on ISRC evidence alone
        (weight ≈ +9.20 → confidence 100) instead of eating two false
        mismatch penalties (v0.8.18 FM1g).
        """
        internal_track = {
            "title": "Gold Rush",
            "artists": ["Neon Priest"],
            "duration_ms": 200000,
        }
        service_track = {
            "title": "",
            "artist": "",
            "duration_ms": None,
        }

        confidence, evidence = calculate_confidence(
            internal_track, service_track, "isrc", config
        )

        assert evidence.title_score == 0.0
        assert evidence.artist_score == 0.0
        assert evidence.title_similarity == 0.0
        assert evidence.artist_similarity == 0.0
        assert evidence.duration_missing is True
        assert confidence == 100  # sigmoid(+9.2003) → 100

    def test_evidence_as_dict_emits_duration_missing_only_when_true(self):
        """duration_missing serializes like isrc_suspect: only when set."""
        internal_track = {
            "title": "Gold Rush",
            "artists": ["Neon Priest"],
            "duration_ms": 200000,
        }
        _, with_duration = calculate_confidence(
            internal_track,
            {"title": "Gold Rush", "artist": "Neon Priest", "duration_ms": 200000},
            "artist_title",
            config,
        )
        _, without_duration = calculate_confidence(
            internal_track,
            {"title": "Gold Rush", "artist": "Neon Priest", "duration_ms": None},
            "artist_title",
            config,
        )

        assert "duration_missing" not in with_duration.as_dict()
        assert without_duration.as_dict()["duration_missing"] is True

    def test_isrc_grade_weight_is_method_gated(self):
        """Only 'isrc'/'mbid' methods earn the ISRC evidence weight.

        Guards the v0.8.18 FM1d demotion end-to-end: the same track pair
        scored as 'artist_title' must weigh less than as 'mbid' — so a
        provider that stops emitting 'mbid' actually stops the inflation.
        """
        internal_track = {
            "title": "Gold Rush",
            "artists": ["Neon Priest"],
            "duration_ms": 200000,
        }
        service_track = {
            "title": "Gold Rush",
            "artist": "Neon Priest",
            "duration_ms": 200000,
        }

        _, as_artist_title = calculate_confidence(
            internal_track, service_track, "artist_title", config
        )
        _, as_mbid = calculate_confidence(internal_track, service_track, "mbid", config)

        # ISRC_EXACT contributes ln(0.99/0.0001) ≈ +9.20 only via the method.
        assert as_mbid.match_weight - as_artist_title.match_weight == pytest.approx(
            9.2003, abs=1e-3
        )

    def test_artist_mismatch_reduces_confidence(self):
        """Artist mismatches should produce negative evidence and lower weight."""
        internal_track = {
            "title": "Yesterday",
            "artists": ["The Beatles"],
            "duration_ms": 125000,
        }
        service_track = {
            "title": "Yesterday",
            "artist": "Frank Sinatra",  # Wrong artist
            "duration_ms": 125000,
        }

        _, evidence = calculate_confidence(
            internal_track, service_track, "artist_title", config
        )

        assert evidence.artist_similarity < 0.5  # Low artist similarity
        assert evidence.artist_score < 0  # Negative log-likelihood ratio

        # Same track with correct artist should have higher weight
        _, correct_evidence = calculate_confidence(
            internal_track,
            {"title": "Yesterday", "artist": "The Beatles", "duration_ms": 125000},
            "artist_title",
            config,
        )
        assert evidence.match_weight < correct_evidence.match_weight

    def test_isrc_suspect_reduces_weight(self):
        """Suspect ISRC (large duration diff) should produce lower weight."""
        internal_track = {
            "title": "Paranoid Android",
            "artists": ["Radiohead"],
            "duration_ms": 386000,
        }

        clean, clean_evidence = calculate_confidence(
            internal_track,
            {"title": "Paranoid Android", "artist": "Radiohead", "duration_ms": 386000},
            "isrc",
            config,
        )
        suspect, suspect_evidence = calculate_confidence(
            internal_track,
            {
                "title": "Paranoid Android",
                "artist": "Radiohead",
                "duration_ms": 406000,
            },  # +20s
            "isrc",
            config,
        )

        assert suspect_evidence.isrc_suspect is True
        assert clean_evidence.isrc_suspect is False
        assert suspect_evidence.match_weight < clean_evidence.match_weight

    def test_diacritic_insensitive_matching(self):
        """Diacritics should not significantly affect confidence."""
        internal_track = {
            "title": "Hyperballad",
            "artists": ["Björk"],
            "duration_ms": 325000,
        }
        service_track = {
            "title": "Hyperballad",
            "artist": "Bjork",  # Without diacritics
            "duration_ms": 325000,
        }

        confidence, evidence = calculate_confidence(
            internal_track, service_track, "artist_title", config
        )

        assert confidence >= 90  # Should score very high despite diacritic diff
        # Normalization strips the diacritic: an exact artist match, not a
        # phonetic near-miss.
        assert evidence.artist_similarity == 1.0


class TestConfidenceEvidence:
    """Serialization of evidence for track_mappings.confidence_evidence."""

    def test_as_dict_rounds_scores_and_emits_set_flags(self):
        evidence = ConfidenceEvidence(
            base_score=90,
            title_score=-5.456,
            artist_score=-2.456,
            duration_score=-1.0149,
            title_similarity=0.8549,
            artist_similarity=0.9251,
            duration_diff_ms=2000,
            final_score=82,
            isrc_suspect=True,
            duration_missing=True,
            match_weight=1.234_56,
        )

        assert evidence.as_dict() == {
            "base_score": 90,
            "title_score": -5.46,
            "artist_score": -2.46,
            "duration_score": -1.01,
            "title_similarity": 0.85,
            "artist_similarity": 0.93,
            "duration_diff_ms": 2000,
            "final_score": 82,
            "isrc_suspect": True,
            "duration_missing": True,
            "match_weight": 1.2346,
        }

    def test_as_dict_omits_unset_flags_and_a_zero_weight(self):
        stored = ConfidenceEvidence(base_score=90, final_score=90).as_dict()

        assert "isrc_suspect" not in stored
        assert "duration_missing" not in stored
        assert "match_weight" not in stored


class TestMatchResult:
    """The derived views a MatchResult gives its consumers."""

    @pytest.mark.parametrize(
        ("success", "review_required", "zone"),
        [
            (True, False, "accept"),
            (False, True, "review"),
            (False, False, "reject"),
        ],
    )
    def test_zone_follows_the_decision_booleans(self, success, review_required, zone):
        result = MatchResult(
            track="mock_track",
            success=success,
            review_required=review_required,
            match_method="artist_title",
        )

        assert result.zone == zone

    def test_evidence_dict_serializes_present_evidence_and_is_none_without(self):
        evidence = ConfidenceEvidence(base_score=85, final_score=85)
        with_evidence = MatchResult(
            track="mock", success=True, match_method="isrc", evidence=evidence
        )
        without = MatchResult(track="mock", success=True, match_method="isrc")

        assert with_evidence.evidence_dict == evidence.as_dict()
        assert without.evidence_dict is None


class TestSelectBestByTitleSimilarity:
    """Tests for the shared best-candidate-by-title-similarity utility."""

    def test_empty_candidates_returns_none(self):
        result = select_best_by_title_similarity("Creep", [], lambda c: c, config)
        assert result is None

    def test_single_candidate_returned(self):
        result = select_best_by_title_similarity(
            "Creep", ["Creep"], lambda c: c, config
        )
        assert result is not None
        assert result.candidate == "Creep"
        assert result.similarity == config.identical_similarity_score

    def test_best_candidate_selected(self):
        candidates = ["Creep - Live", "Creep", "Totally Different"]
        result = select_best_by_title_similarity(
            "Creep", candidates, lambda c: c, config
        )
        assert result is not None
        assert result.candidate == "Creep"

    def test_threshold_rejection(self):
        result = select_best_by_title_similarity(
            "Paranoid Android",
            ["Completely Different Song"],
            lambda c: c,
            config,
            min_similarity=0.9,
        )
        assert result is None

    def test_none_name_filtered(self):
        """Candidates whose get_name returns None should be skipped."""
        candidates = [{"name": None}, {"name": "Creep"}]
        result = select_best_by_title_similarity(
            "Creep",
            candidates,
            operator.itemgetter("name"),
            config,
        )
        assert result is not None
        assert result.candidate == {"name": "Creep"}

    def test_all_none_names_returns_none(self):
        candidates = [{"name": None}, {"name": None}]
        result = select_best_by_title_similarity(
            "Creep",
            candidates,
            operator.itemgetter("name"),
            config,
        )
        assert result is None
