"""Tests for ISRC match reliability assessment.

A duration gap of more than 10 seconds between two tracks sharing an ISRC
marks the match suspect (remaster or different version).
"""

import pytest

from src.domain.matching.isrc_validation import (
    assess_isrc_match_reliability,
)


class TestAssessISRCMatchReliability:
    """Test ISRC match reliability assessment using duration comparison."""

    @pytest.mark.parametrize(
        ("duration_diff_ms", "suspect"),
        [
            (None, False),  # no duration data: nothing to cross-check
            (0, False),
            (10_000, False),  # exactly 10s is still the same version
            (10_001, True),
            (60_000, True),
        ],
    )
    def test_suspect_only_past_ten_seconds(
        self, duration_diff_ms: int | None, suspect: bool
    ):
        assert assess_isrc_match_reliability(duration_diff_ms).suspect is suspect

    def test_large_duration_diff_is_suspect(self):
        result = assess_isrc_match_reliability(15_000)  # 15s
        assert result.suspect is True
        assert "remaster" in result.reason or "different version" in result.reason
