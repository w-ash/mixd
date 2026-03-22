"""Unit tests for the sync safety check — pure domain function.

Tests the thresholds that flag destructive sync operations requiring
explicit user confirmation before proceeding.
"""

from src.domain.playlist.sync_safety import (
    REMOVAL_ABSOLUTE_THRESHOLD,
    REMOVAL_PERCENTAGE_THRESHOLD,
    check_sync_safety,
)


class TestCheckSyncSafety:
    """Threshold logic for flagging destructive sync operations."""

    def test_empty_playlist_not_flagged(self):
        """total_current=0 means nothing to destroy."""
        result = check_sync_safety(removals=0, total_current=0)
        assert result.flagged is False

    def test_no_removals_not_flagged(self):
        result = check_sync_safety(removals=0, total_current=100)
        assert result.flagged is False
        assert result.remaining_after_sync == 100

    def test_small_removal_not_flagged(self):
        """5% removal — well below 50% threshold."""
        result = check_sync_safety(removals=5, total_current=100)
        assert result.flagged is False
        assert result.remaining_after_sync == 95

    def test_high_percentage_below_absolute_not_flagged(self):
        """80% removal but only 8 tracks — below absolute threshold of 10."""
        result = check_sync_safety(removals=8, total_current=10)
        assert result.flagged is False

    def test_above_both_thresholds_flagged(self):
        """60/100 = 60% > 50%, and 60 > 10 absolute."""
        result = check_sync_safety(removals=60, total_current=100)
        assert result.flagged is True
        assert result.reason is not None
        assert "60" in result.reason
        assert "100" in result.reason

    def test_emptying_playlist_always_flagged(self):
        """Removing all tracks must always flag, regardless of count."""
        result = check_sync_safety(removals=3, total_current=3)
        assert result.flagged is True
        assert "all 3 tracks" in (result.reason or "")
        assert result.remaining_after_sync == 0

    def test_emptying_large_playlist_flagged(self):
        result = check_sync_safety(removals=150, total_current=150)
        assert result.flagged is True
        assert result.remaining_after_sync == 0

    def test_boundary_just_above_both_thresholds(self):
        """11 of 20 = 55% > 50%, and 11 > 10 absolute — flagged."""
        result = check_sync_safety(removals=11, total_current=20)
        assert result.flagged is True

    def test_boundary_exactly_at_percentage(self):
        """50% exactly is NOT > 50%, so not flagged (with 20 removals > 10)."""
        result = check_sync_safety(removals=20, total_current=40)
        assert result.flagged is False

    def test_boundary_exactly_at_absolute(self):
        """10 removals exactly is NOT > 10, so not flagged."""
        result = check_sync_safety(removals=10, total_current=15)
        assert result.flagged is False

    def test_result_contains_all_counts(self):
        result = check_sync_safety(removals=60, total_current=100)
        assert result.removals == 60
        assert result.total_current == 100
        assert result.remaining_after_sync == 40

    def test_thresholds_match_documented_values(self):
        """Verify thresholds match domain constants."""
        assert REMOVAL_PERCENTAGE_THRESHOLD == 0.50
        assert REMOVAL_ABSOLUTE_THRESHOLD == 10
