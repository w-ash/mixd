"""Tests for playlist execution strategies."""

import pytest

from src.domain.entities.playlist import Playlist
from src.domain.entities.track import Artist, Track, TrackList
from src.domain.playlist.diff_engine import (
    calculate_playlist_diff,
)
from src.domain.playlist.execution_strategies import (
    APIExecutionStrategy,
    CanonicalExecutionStrategy,
    execute_with_strategy,
    get_execution_strategy,
)


@pytest.fixture
def sample_tracks():
    """Create sample tracks for testing."""
    return [
        Track(title="Track A", artists=[Artist(name="Artist 1")]),
        Track(title="Track B", artists=[Artist(name="Artist 2")]),
        Track(title="Track C", artists=[Artist(name="Artist 3")]),
        Track(title="Track D", artists=[Artist(name="Artist 4")]),
        Track(title="Track E", artists=[Artist(name="Artist 5")]),
    ]


@pytest.fixture
def sample_playlist(sample_tracks):
    """Create a sample playlist."""
    return Playlist.from_tracklist(
        name="Test Playlist",
        tracklist=sample_tracks,
    )


@pytest.fixture
def reordered_playlist(sample_tracks):
    """Create a reordered version of the sample playlist."""
    reordered_tracks = [
        sample_tracks[2],
        sample_tracks[0],
        sample_tracks[1],
        sample_tracks[4],
        sample_tracks[3],
    ]
    return Playlist.from_tracklist(
        name="Test Playlist",
        tracklist=reordered_tracks,
    )


class TestCanonicalExecutionStrategy:
    """Test canonical execution strategy."""

    def test_plan_operations_uses_atomic_reorder(
        self, sample_playlist, reordered_playlist
    ):
        """Test that canonical strategy prefers atomic reordering."""
        strategy = CanonicalExecutionStrategy()
        target_tracklist = TrackList(tracks=reordered_playlist.tracks)
        diff = calculate_playlist_diff(sample_playlist, target_tracklist)

        plan = strategy.plan_operations(diff)

        assert plan.use_atomic_reorder is True
        assert plan.execution_metadata["strategy"] == "canonical"
        assert plan.execution_metadata["atomic_reorder"] is True

    def test_can_optimize_to_reorder_always_true(
        self, sample_playlist, reordered_playlist
    ):
        """Test that canonical strategy can always optimize to reordering."""
        strategy = CanonicalExecutionStrategy()
        target_tracklist = TrackList(tracks=reordered_playlist.tracks)
        diff = calculate_playlist_diff(sample_playlist, target_tracklist)

        result = strategy.can_optimize_to_reorder(diff)

        assert result is True


class TestAPIExecutionStrategy:
    """Test API execution strategy."""

    def test_plan_operations_uses_sequential_execution(
        self, sample_playlist, reordered_playlist
    ):
        """Test that API strategy uses sequential execution."""
        strategy = APIExecutionStrategy()
        target_tracklist = TrackList(tracks=reordered_playlist.tracks)
        diff = calculate_playlist_diff(sample_playlist, target_tracklist)

        plan = strategy.plan_operations(diff)

        assert plan.use_atomic_reorder is False
        assert plan.execution_metadata["strategy"] == "api"
        assert "position_shift_simulation" in plan.execution_metadata

    def test_can_optimize_to_reorder_always_false(
        self, sample_playlist, reordered_playlist
    ):
        """Test that API strategy cannot optimize to reordering."""
        strategy = APIExecutionStrategy()
        target_tracklist = TrackList(tracks=reordered_playlist.tracks)
        diff = calculate_playlist_diff(sample_playlist, target_tracklist)

        result = strategy.can_optimize_to_reorder(diff)

        assert result is False


class TestExecutionStrategyFactory:
    """Test execution strategy factory functions."""

    def test_get_execution_strategy_canonical(self):
        """Test getting canonical execution strategy."""
        strategy = get_execution_strategy("canonical")

        assert isinstance(strategy, CanonicalExecutionStrategy)

    def test_get_execution_strategy_api(self):
        """Test getting API execution strategy."""
        strategy = get_execution_strategy("api")

        assert isinstance(strategy, APIExecutionStrategy)

    def test_get_execution_strategy_invalid(self):
        """Test that invalid strategy type raises error."""
        with pytest.raises(ValueError, match="Unsupported target type"):
            get_execution_strategy("invalid")


class TestExecuteWithStrategy:
    """Test the execute_with_strategy function."""

    def test_execute_with_canonical_strategy(self, sample_playlist, reordered_playlist):
        """Test execution with canonical strategy."""
        strategy = CanonicalExecutionStrategy()
        target_tracklist = TrackList(tracks=reordered_playlist.tracks)
        diff = calculate_playlist_diff(sample_playlist, target_tracklist)

        updated_tracks, execution_metadata = execute_with_strategy(
            strategy, sample_playlist, target_tracklist, diff
        )

        # Should return reordered tracks
        assert len(updated_tracks) == len(reordered_playlist.tracks)
        assert [track.id for track in updated_tracks] == [
            track.id for track in reordered_playlist.tracks
        ]
        assert execution_metadata["strategy"] == "canonical"

    def test_execute_with_api_strategy(self, sample_playlist, reordered_playlist):
        """Test execution with API strategy."""
        strategy = APIExecutionStrategy()
        target_tracklist = TrackList(tracks=reordered_playlist.tracks)
        diff = calculate_playlist_diff(sample_playlist, target_tracklist)

        updated_tracks, execution_metadata = execute_with_strategy(
            strategy, sample_playlist, target_tracklist, diff
        )

        # Should return reordered tracks (currently falls back to reordering)
        assert len(updated_tracks) == len(reordered_playlist.tracks)
        assert [track.id for track in updated_tracks] == [
            track.id for track in reordered_playlist.tracks
        ]
        assert execution_metadata["strategy"] == "api"

    def test_execute_with_no_changes(self, sample_playlist):
        """Test execution when no changes are needed."""
        strategy = CanonicalExecutionStrategy()
        target_tracklist = TrackList(tracks=sample_playlist.tracks)
        diff = calculate_playlist_diff(sample_playlist, target_tracklist)

        updated_tracks, _execution_metadata = execute_with_strategy(
            strategy, sample_playlist, target_tracklist, diff
        )

        # Should return same tracks
        assert len(updated_tracks) == len(sample_playlist.tracks)
        assert [track.id for track in updated_tracks] == [
            track.id for track in sample_playlist.tracks
        ]
