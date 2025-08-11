"""Tests for domain matching algorithms.

These tests verify the pure business logic of track matching and confidence scoring.
"""

from src.config import settings
from src.domain.matching.algorithms import (
    calculate_confidence,
    calculate_title_similarity,
)


class TestCalculateTitleSimilarity:
    """Test cases for title similarity calculation."""

    def test_identical_titles(self):
        """Test that identical titles return perfect similarity."""
        result = calculate_title_similarity("Paranoid Android", "Paranoid Android")
        assert result == 1.0

    def test_different_case(self):
        """Test that case differences don't affect similarity."""
        result = calculate_title_similarity("Paranoid Android", "paranoid android")
        assert result == 1.0

    def test_live_variation(self):
        """Test that live variations are detected and penalized."""
        result = calculate_title_similarity(
            "Paranoid Android", "Paranoid Android - Live"
        )
        assert result == 0.6  # Should detect variation marker

    def test_remix_variation(self):
        """Test that remix variations are detected and penalized."""
        result = calculate_title_similarity("Karma Police", "Karma Police (Remix)")
        assert result == 0.6  # Should detect variation marker

    def test_completely_different_titles(self):
        """Test that completely different titles return low similarity."""
        result = calculate_title_similarity("Paranoid Android", "Yesterday")
        assert result < 0.3  # Should be very low similarity


class TestCalculateConfidence:
    """Test cases for confidence calculation."""

    def test_perfect_isrc_match(self):
        """Test that ISRC matches get high confidence."""
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
            internal_track, service_track, "isrc"
        )

        assert confidence >= 90  # Should be very high for ISRC match
        assert evidence.base_score == 95  # Base ISRC score
        assert evidence.final_score == confidence

    def test_good_artist_title_match(self):
        """Test that good artist/title matches score well."""
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
            internal_track, service_track, "artist_title"
        )

        assert confidence >= 85  # Should be high for good match
        assert evidence.base_score == 90  # Base artist/title score
        assert evidence.title_similarity == 1.0  # Perfect title match

    def test_variation_match_penalty(self):
        """Test that title variations receive appropriate penalties."""
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

        confidence, evidence = calculate_confidence(
            internal_track, service_track, "artist_title"
        )

        assert confidence < 85  # Should be penalized for variation
        assert evidence.title_similarity == 0.6  # Variation similarity score

    def test_missing_duration_penalty(self):
        """Test that missing duration data is penalized."""
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
            internal_track, service_track, "artist_title"
        )

        assert evidence.duration_score == -settings.matching.duration_missing_penalty  # Missing duration penalty
        assert confidence < 90  # Should be reduced from base score

    def test_artist_mismatch_penalty(self):
        """Test that artist mismatches are heavily penalized."""
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

        confidence, evidence = calculate_confidence(
            internal_track, service_track, "artist_title"
        )

        assert confidence < 70  # Should be significantly penalized
        assert evidence.artist_similarity < 0.5  # Low artist similarity

    def test_confidence_bounds(self):
        """Test that confidence scores stay within bounds."""
        # Test with terrible match that could go negative
        internal_track = {
            "title": "Track A",
            "artists": ["Artist A"],
            "duration_ms": 100000,
        }
        service_track = {
            "title": "Completely Different Track",
            "artist": "Different Artist",
            "duration_ms": 500000,  # Very different duration
        }

        confidence, evidence = calculate_confidence(
            internal_track, service_track, "artist_title"
        )

        assert 0 <= confidence <= 100  # Must stay within bounds
        assert evidence.final_score == confidence
