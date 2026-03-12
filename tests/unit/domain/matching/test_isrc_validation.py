"""Tests for ISRC validation and quality assessment.

Verifies structural ISRC validation, match reliability assessment,
and collision detection for data integrity monitoring.
"""

import pytest

from src.domain.matching.isrc_validation import (
    assess_isrc_match_reliability,
    find_isrc_collisions,
    validate_isrc_structure,
)
from tests.fixtures import make_track


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


class TestFindISRCCollisions:
    """Test ISRC collision detection for data integrity monitoring."""

    def test_no_collisions(self):
        tracks = [
            make_track(id=1, isrc="USRC17607839"),
            make_track(id=2, isrc="GBAYE0000351"),
            make_track(id=3, isrc="FRUM71500001"),
        ]
        assert find_isrc_collisions(tracks) == {}

    def test_detects_collision(self):
        shared_isrc = "USRC17607839"
        tracks = [
            make_track(id=1, isrc=shared_isrc),
            make_track(id=2, isrc=shared_isrc),
            make_track(id=3, isrc="GBAYE0000351"),
        ]
        collisions = find_isrc_collisions(tracks)
        assert shared_isrc in collisions
        assert len(collisions[shared_isrc]) == 2

    def test_multiple_collision_groups(self):
        tracks = [
            make_track(id=1, isrc="AAA"),
            make_track(id=2, isrc="AAA"),
            make_track(id=3, isrc="BBB"),
            make_track(id=4, isrc="BBB"),
            make_track(id=5, isrc="BBB"),
            make_track(id=6, isrc="CCC"),
        ]
        collisions = find_isrc_collisions(tracks)
        assert len(collisions) == 2
        assert len(collisions["AAA"]) == 2
        assert len(collisions["BBB"]) == 3

    def test_tracks_without_isrc_ignored(self):
        tracks = [
            make_track(id=1, isrc=None),
            make_track(id=2, isrc=None),
            make_track(id=3, isrc="USRC17607839"),
        ]
        assert find_isrc_collisions(tracks) == {}

    def test_empty_list(self):
        assert find_isrc_collisions([]) == {}
