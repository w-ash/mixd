"""Unit tests for Spotify import play filtering logic.

Tests focus on real music listening scenarios and user behavior patterns
to ensure the filtering logic correctly distinguishes between legitimate
listens and skips/partial plays.
"""

import pytest

from src.infrastructure.connectors.spotify.play_resolver import (
    should_include_spotify_play as should_include_play,
)


@pytest.mark.unit
class TestPlayFilteringRealMusicScenarios:
    """Test play filtering with realistic music listening patterns."""

    def test_pop_song_full_listen(self):
        """User listens to most of a 3:30 pop song - should include."""
        pop_song_duration = 210_000  # 3:30
        listened_duration = 180_000  # 3:00 (85% of song)

        assert should_include_play(listened_duration, pop_song_duration) is True

    def test_pop_song_skip_after_chorus(self):
        """User skips pop song after 90 seconds - should exclude (< 50%)."""
        pop_song_duration = 210_000  # 3:30
        listened_duration = 90_000  # 1:30 (43% of song)

        assert should_include_play(listened_duration, pop_song_duration) is False

    def test_interlude_track_half_listen(self):
        """User listens to half of 30-second interlude - should include (50% rule)."""
        interlude_duration = 30_000  # 30 seconds
        listened_duration = 15_000  # 15 seconds (exactly 50%)

        assert should_include_play(listened_duration, interlude_duration) is True

    def test_interlude_track_quick_skip(self):
        """User quickly skips 30-second interlude after 5 seconds - should exclude."""
        interlude_duration = 30_000  # 30 seconds
        listened_duration = 5_000  # 5 seconds (16% of song)

        assert should_include_play(listened_duration, interlude_duration) is False

    def test_prog_rock_epic_partial_listen(self):
        """User listens to 5 minutes of 20-minute prog epic - should include (4min rule)."""
        prog_epic_duration = 1_200_000  # 20 minutes
        listened_duration = 300_000  # 5 minutes (25% but > 4min threshold)

        assert should_include_play(listened_duration, prog_epic_duration) is True

    def test_prog_rock_epic_quick_skip(self):
        """User skips prog epic after 2 minutes - should exclude (< 4min threshold)."""
        prog_epic_duration = 1_200_000  # 20 minutes
        listened_duration = 120_000  # 2 minutes (10% and < 4min threshold)

        assert should_include_play(listened_duration, prog_epic_duration) is False

    def test_ballad_emotional_moment(self):
        """User listens to 4+ minutes of 5-minute ballad - should include."""
        ballad_duration = 300_000  # 5 minutes
        listened_duration = 250_000  # 4:10 (83% and > 4min threshold)

        assert should_include_play(listened_duration, ballad_duration) is True


class TestPlayFilteringThresholds:
    """Test exact threshold boundaries and fallback behavior."""

    def test_four_minute_threshold_exact(self):
        """Play of exactly 4 minutes should be included."""
        assert should_include_play(240_000, 600_000) is True  # 4min of 10min track
        assert should_include_play(240_000, None) is True  # 4min with no duration

    def test_four_minute_threshold_just_under(self):
        """Play of just under 4 minutes should be excluded."""
        assert should_include_play(239_999, 600_000) is False  # 3:59.999 of 10min track
        assert should_include_play(239_999, None) is False  # 3:59.999 with no duration

    def test_fifty_percent_threshold_exact(self):
        """Play of exactly 50% should be included."""
        track_duration = 180_000  # 3 minutes
        fifty_percent = 90_000  # 1.5 minutes

        assert should_include_play(fifty_percent, track_duration) is True

    def test_fifty_percent_threshold_just_under(self):
        """Play of just under 50% should be excluded."""
        track_duration = 180_000  # 3 minutes
        just_under_fifty = 89_999  # 1:29.999

        assert should_include_play(just_under_fifty, track_duration) is False

    def test_no_duration_fallback_behavior(self):
        """When track duration unknown, should fall back to 4-minute rule."""
        # >= 4 minutes always pass, < 4 minutes always fail when no duration info
        assert should_include_play(300_000, None) is True  # 5 minutes - pass
        assert (
            should_include_play(180_000, None) is False
        )  # 3 minutes - fail (no duration info)


class TestPlayFilteringUserBehaviorEdgeCases:
    """Test edge cases based on real user behavior patterns."""

    def test_replay_behavior_longer_than_track(self):
        """User replays song, listening longer than track duration."""
        track_duration = 180_000  # 3 minutes
        replay_duration = 250_000  # 4+ minutes (replayed)

        # Should still be included (longer than original is fine)
        assert should_include_play(replay_duration, track_duration) is True

    def test_zero_duration_plays(self):
        """Instantaneous skips should always be excluded."""
        assert should_include_play(0, 180_000) is False
        assert should_include_play(0, None) is False

    def test_very_short_tracks_podcast_bumpers(self):
        """Very short tracks like podcast bumpers - 50% rule should apply."""
        bumper_duration = 5_000  # 5 seconds
        half_listen = 2_500  # 2.5 seconds (50%)
        quick_skip = 1_000  # 1 second (20%)

        assert should_include_play(half_listen, bumper_duration) is True
        assert should_include_play(quick_skip, bumper_duration) is False

    def test_classical_movement_scenarios(self):
        """Classical music movements with various lengths."""
        # Short movement - 2 minutes
        short_movement = 120_000
        one_minute_listen = 60_000  # 50% exactly
        assert should_include_play(one_minute_listen, short_movement) is True

        # Long movement - 15 minutes
        long_movement = 900_000
        five_minute_listen = 300_000  # 33% but > 4min threshold
        assert should_include_play(five_minute_listen, long_movement) is True
