"""Tests for ISRC validation and quality assessment.

Verifies structural ISRC validation, match reliability assessment,
and collision detection for data integrity monitoring.
"""

import pytest

from src.domain.matching.isrc_validation import (
    assess_isrc_match_reliability,
    validate_isrc_structure,
)


class TestValidateISRCStructure:
    """Test structural ISRC validation beyond basic format checking."""

    @pytest.mark.parametrize(
        "isrc",
        [
            "USRC17607839",  # US registrant
            "GBAYE0000351",  # UK registrant
            "FRUM71500001",  # French registrant
            "DEAB71600001",  # German registrant
        ],
    )
    def test_valid_isrc_structures(self, isrc: str):
        result = validate_isrc_structure(isrc)
        assert result.valid is True
        assert result.country_code == isrc[:2]
        assert result.registrant_code == isrc[2:5]
        assert result.year == isrc[5:7]
        assert result.designation_code == isrc[7:12]
        assert result.error == ""

    def test_parses_components_correctly(self):
        result = validate_isrc_structure("USRC17607839")
        assert result.country_code == "US"
        assert result.registrant_code == "RC1"
        assert result.year == "76"
        assert result.designation_code == "07839"

    def test_empty_isrc(self):
        result = validate_isrc_structure("")
        assert result.valid is False
        assert "empty" in result.error

    def test_wrong_length(self):
        result = validate_isrc_structure("USRC176")
        assert result.valid is False
        assert "12 characters" in result.error

    def test_too_long(self):
        result = validate_isrc_structure("USRC1760783900")
        assert result.valid is False
        assert "12 characters" in result.error

    def test_lowercase_country_code_rejected(self):
        """Expects normalized (uppercase) input."""
        result = validate_isrc_structure("usrc17607839")
        assert result.valid is False

    def test_numeric_country_code_rejected(self):
        result = validate_isrc_structure("12RC17607839")
        assert result.valid is False

    def test_alpha_in_designation_rejected(self):
        """Designation code must be 5 digits."""
        result = validate_isrc_structure("USRC176ABCDE")
        assert result.valid is False

    def test_alpha_in_year_rejected(self):
        """Year must be 2 digits."""
        result = validate_isrc_structure("USRC1AB07839")
        assert result.valid is False


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
