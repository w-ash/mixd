"""Tests for ISRC validation and quality assessment.

Verifies structural ISRC validation, match reliability assessment,
and collision detection for data integrity monitoring.
"""

from src.domain.matching.isrc_validation import (
    assess_isrc_match_reliability,
)


class TestAssessISRCMatchReliability:
    """Test ISRC match reliability assessment using duration comparison."""

    def test_no_duration_data_is_not_suspect(self):
        result = assess_isrc_match_reliability(None)
        assert result.suspect is False

    def test_close_duration_is_not_suspect(self):
        result = assess_isrc_match_reliability(500)  # 0.5s
        assert result.suspect is False

    def test_moderate_duration_diff_is_not_suspect(self):
        result = assess_isrc_match_reliability(5_000)  # 5s
        assert result.suspect is False

    def test_exactly_at_threshold_is_not_suspect(self):
        result = assess_isrc_match_reliability(10_000)  # exactly 10s
        assert result.suspect is False

    def test_large_duration_diff_is_suspect(self):
        result = assess_isrc_match_reliability(15_000)  # 15s
        assert result.suspect is True
        assert "remaster" in result.reason or "different version" in result.reason

    def test_very_large_duration_diff_is_suspect(self):
        result = assess_isrc_match_reliability(60_000)  # 60s
        assert result.suspect is True

    def test_zero_duration_diff_is_not_suspect(self):
        result = assess_isrc_match_reliability(0)
        assert result.suspect is False
