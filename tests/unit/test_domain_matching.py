"""Tests for domain layer matching algorithms and types.

These tests verify that the domain layer has zero external dependencies
and that the core business logic functions correctly.
"""

import pytest

from src.domain.matching import (
    CONFIDENCE_CONFIG,
    ConfidenceEvidence,
    MatchResult,
    calculate_confidence,
    calculate_title_similarity,
)


class TestTitleSimilarity:
    """Test title similarity calculation algorithm."""

    def test_identical_titles(self):
        """Test identical titles return maximum similarity."""
        result = calculate_title_similarity("Paranoid Android", "Paranoid Android")
        assert result == 1.0

    def test_case_insensitive(self):
        """Test that comparison is case-insensitive."""
        result = calculate_title_similarity("PARANOID ANDROID", "paranoid android")
        assert result == 1.0

    def test_variation_markers(self):
        """Test that variation markers are detected and penalized."""
        result = calculate_title_similarity(
            "Paranoid Android", "Paranoid Android - Live"
        )
        assert result == CONFIDENCE_CONFIG["variation_similarity_score"]

        result = calculate_title_similarity("Song Title", "Song Title (Remix)")
        assert result == CONFIDENCE_CONFIG["variation_similarity_score"]

    def test_fuzzy_matching(self):
        """Test fuzzy matching for different titles."""
        result = calculate_title_similarity("Yesterday", "Yellow Submarine")
        assert 0.0 <= result <= 1.0
        assert result < 0.5  # Should be low similarity

    def test_word_order_tolerance(self):
        """Test that word order differences are handled well."""
        result = calculate_title_similarity(
            "The Final Countdown", "Final Countdown, The"
        )
        assert result > 0.8  # Should be high similarity despite word order


class TestConfidenceCalculation:
    """Test confidence calculation algorithm."""

    def test_isrc_match_high_confidence(self):
        """Test that ISRC matches get high base confidence."""
        internal_data = {
            "title": "Test Song",
            "artists": ["Test Artist"],
            "duration_ms": 240000,
        }
        service_data = {
            "title": "Test Song",
            "artist": "Test Artist",
            "duration_ms": 240000,
        }

        confidence, evidence = calculate_confidence(internal_data, service_data, "isrc")

        assert confidence >= 90
        assert evidence.base_score == CONFIDENCE_CONFIG["base_isrc"]
        assert evidence.final_score == confidence

    def test_title_mismatch_penalty(self):
        """Test that title mismatches reduce confidence."""
        internal_data = {
            "title": "Song A",
            "artists": ["Artist"],
            "duration_ms": 240000,
        }
        service_data = {
            "title": "Song B",
            "artist": "Artist",
            "duration_ms": 240000,
        }

        confidence, evidence = calculate_confidence(
            internal_data, service_data, "artist_title"
        )

        assert confidence < CONFIDENCE_CONFIG["base_artist_title"]
        assert evidence.title_score < 0  # Should have penalty

    def test_artist_mismatch_penalty(self):
        """Test that artist mismatches reduce confidence."""
        internal_data = {
            "title": "Song",
            "artists": ["Artist A"],
            "duration_ms": 240000,
        }
        service_data = {
            "title": "Song",
            "artist": "Artist B",
            "duration_ms": 240000,
        }

        confidence, evidence = calculate_confidence(
            internal_data, service_data, "artist_title"
        )

        assert confidence < CONFIDENCE_CONFIG["base_artist_title"]
        assert evidence.artist_score < 0  # Should have penalty

    def test_duration_mismatch_penalty(self):
        """Test that duration mismatches reduce confidence."""
        internal_data = {
            "title": "Song",
            "artists": ["Artist"],
            "duration_ms": 240000,
        }
        service_data = {
            "title": "Song",
            "artist": "Artist",
            "duration_ms": 180000,  # 60 second difference
        }

        confidence, evidence = calculate_confidence(
            internal_data, service_data, "artist_title"
        )

        assert confidence < CONFIDENCE_CONFIG["base_artist_title"]
        assert evidence.duration_score < 0  # Should have penalty
        assert evidence.duration_diff_ms == 60000

    def test_missing_duration_penalty(self):
        """Test that missing duration data applies penalty."""
        internal_data = {
            "title": "Song",
            "artists": ["Artist"],
            "duration_ms": None,
        }
        service_data = {
            "title": "Song",
            "artist": "Artist",
            "duration_ms": 240000,
        }

        _confidence, evidence = calculate_confidence(
            internal_data, service_data, "artist_title"
        )

        assert evidence.duration_score == -CONFIDENCE_CONFIG["duration_missing_penalty"]

    def test_confidence_bounds(self):
        """Test that confidence is bounded between min and max values."""
        # Test with data that should give very low confidence
        internal_data = {
            "title": "Completely Different Song",
            "artists": ["Different Artist"],
            "duration_ms": 60000,
        }
        service_data = {
            "title": "Another Song Entirely",
            "artist": "Another Artist",
            "duration_ms": 300000,
        }

        confidence, _evidence = calculate_confidence(
            internal_data, service_data, "artist_title"
        )

        assert (
            CONFIDENCE_CONFIG["min_confidence"]
            <= confidence
            <= CONFIDENCE_CONFIG["max_confidence"]
        )


class TestConfidenceEvidence:
    """Test ConfidenceEvidence type."""

    def test_as_dict(self):
        """Test conversion to dictionary."""
        evidence = ConfidenceEvidence(
            base_score=90,
            title_score=-5.0,
            artist_score=-2.5,
            duration_score=-1.0,
            title_similarity=0.85,
            artist_similarity=0.92,
            duration_diff_ms=2000,
            final_score=82,
        )

        result = evidence.as_dict()

        assert result["base_score"] == 90
        assert result["title_score"] == -5.0
        assert result["artist_score"] == -2.5
        assert result["duration_score"] == -1.0
        assert result["title_similarity"] == 0.85
        assert result["artist_similarity"] == 0.92
        assert result["duration_diff_ms"] == 2000
        assert result["final_score"] == 82

    def test_immutable(self):
        """Test that ConfidenceEvidence is immutable."""
        evidence = ConfidenceEvidence(base_score=90)

        with pytest.raises(AttributeError):
            evidence.base_score = 95  # Should not be allowed


class TestMatchResult:
    """Test MatchResult type."""

    def test_match_result_creation(self):
        """Test creating a MatchResult."""
        evidence = ConfidenceEvidence(base_score=90, final_score=85)

        result = MatchResult(
            track="mock_track",  # Using string for test
            success=True,
            connector_id="spotify:123",
            confidence=85,
            match_method="isrc",
            service_data={"title": "Test"},
            evidence=evidence,
        )

        assert result.success is True
        assert result.connector_id == "spotify:123"
        assert result.confidence == 85
        assert result.match_method == "isrc"
        assert result.service_data == {"title": "Test"}
        assert result.evidence == evidence

    def test_immutable(self):
        """Test that MatchResult is immutable."""
        result = MatchResult(track="mock", success=True)

        with pytest.raises(AttributeError):
            result.success = False  # Should not be allowed
