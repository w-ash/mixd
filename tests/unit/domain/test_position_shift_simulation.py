"""Tests for position shift simulation in API execution strategy.

Validates that the enhanced APIExecutionStrategy properly handles index shifts
during sequential operation execution, preventing position conflicts.
"""

import pytest

from src.domain.entities.playlist import Playlist
from src.domain.entities.track import Artist, Track, TrackList
from src.domain.playlist.diff_engine import (
    PlaylistOperation,
    PlaylistOperationType,
    calculate_playlist_diff,
)
from src.domain.playlist.execution_strategies import APIExecutionStrategy


@pytest.mark.unit
class TestPositionShiftSimulation:
    """Test position shift simulation for API operations."""

    @pytest.fixture
    def api_strategy(self):
        """Create API execution strategy for testing."""
        return APIExecutionStrategy()

    @pytest.fixture
    def sample_tracks(self):
        """Create sample tracks for testing."""
        return [
            Track(id=1, title="Track A", artists=[Artist(name="Artist 1")]),
            Track(id=2, title="Track B", artists=[Artist(name="Artist 2")]),
            Track(id=3, title="Track C", artists=[Artist(name="Artist 3")]),
            Track(id=4, title="Track D", artists=[Artist(name="Artist 4")]),
            Track(id=5, title="Track E", artists=[Artist(name="Artist 5")]),
        ]

    def test_move_operations_reverse_order(self, api_strategy, sample_tracks):
        """Move operations should be sorted in reverse order by old_position."""
        # Create move operations with different old_positions
        move_ops = [
            PlaylistOperation(
                operation_type=PlaylistOperationType.MOVE,
                track=sample_tracks[0],
                position=0,
                old_position=1,
                spotify_uri="test:1",
            ),
            PlaylistOperation(
                operation_type=PlaylistOperationType.MOVE,
                track=sample_tracks[1],
                position=1,
                old_position=3,
                spotify_uri="test:2",
            ),
            PlaylistOperation(
                operation_type=PlaylistOperationType.MOVE,
                track=sample_tracks[2],
                position=2,
                old_position=2,
                spotify_uri="test:3",
            ),
        ]

        adjusted_moves = api_strategy._adjust_move_operations(move_ops)

        # Should be sorted by old_position in descending order
        assert len(adjusted_moves) == 3
        assert adjusted_moves[0].old_position == 3  # Highest position first
        assert adjusted_moves[1].old_position == 2
        assert adjusted_moves[2].old_position == 1  # Lowest position last

    def test_remove_operations_reverse_order(self, api_strategy, sample_tracks):
        """Remove operations should be sorted in reverse order to avoid index shifts."""
        operations = [
            PlaylistOperation(
                operation_type=PlaylistOperationType.REMOVE,
                track=sample_tracks[0],
                position=0,
                old_position=1,
                spotify_uri="test:1",
            ),
            PlaylistOperation(
                operation_type=PlaylistOperationType.REMOVE,
                track=sample_tracks[1],
                position=1,
                old_position=3,
                spotify_uri="test:2",
            ),
            PlaylistOperation(
                operation_type=PlaylistOperationType.ADD,
                track=sample_tracks[2],
                position=2,
                spotify_uri="test:3",
            ),
        ]

        adjusted_ops = api_strategy.simulate_position_shifts(operations)

        # Remove operations should be first and in reverse order
        remove_ops = [
            op
            for op in adjusted_ops
            if op.operation_type == PlaylistOperationType.REMOVE
        ]
        assert len(remove_ops) == 2
        assert remove_ops[0].old_position == 3  # Higher position first
        assert remove_ops[1].old_position == 1  # Lower position last

    def test_complex_reordering_scenario(self, api_strategy, sample_tracks):
        """Test complex reordering with many move operations."""
        # Create a scenario with many moves that could cause index conflicts
        current_playlist = Playlist(name="Test", tracks=sample_tracks.copy())

        # Completely reverse the playlist
        target_tracks = list(reversed(sample_tracks))
        target_tracklist = TrackList(tracks=target_tracks)

        # Calculate diff and plan operations
        diff = calculate_playlist_diff(current_playlist, target_tracklist)
        execution_plan = api_strategy.plan_operations(diff)

        # Should have position shift simulation enabled
        assert execution_plan.execution_metadata["position_shift_simulation"] is True

        # Move operations should be in dependency-safe order
        move_ops = [
            op
            for op in execution_plan.operations
            if op.operation_type == PlaylistOperationType.MOVE
        ]

        if len(move_ops) > 1:
            # Verify operations are in reverse order by old_position
            old_positions = [op.old_position for op in move_ops]
            assert old_positions == sorted(old_positions, reverse=True)

    def test_dependency_order_calculation(self, api_strategy, sample_tracks):
        """Test dependency order calculation for move operations."""
        operations = [
            PlaylistOperation(
                operation_type=PlaylistOperationType.ADD,
                track=sample_tracks[0],
                position=0,
                spotify_uri="test:add",
            ),
            PlaylistOperation(
                operation_type=PlaylistOperationType.MOVE,
                track=sample_tracks[1],
                position=1,
                old_position=4,
                spotify_uri="test:move1",
            ),
            PlaylistOperation(
                operation_type=PlaylistOperationType.MOVE,
                track=sample_tracks[2],
                position=2,
                old_position=2,
                spotify_uri="test:move2",
            ),
            PlaylistOperation(
                operation_type=PlaylistOperationType.REMOVE,
                track=sample_tracks[3],
                position=3,
                old_position=3,
                spotify_uri="test:remove",
            ),
        ]

        dependency_order = api_strategy._calculate_dependency_order(operations)

        # Should return indices for move operations in reverse order
        if dependency_order:
            # Find the move operations in the original list
            move_indices = [
                i
                for i, op in enumerate(operations)
                if op.operation_type == PlaylistOperationType.MOVE
            ]

            # Dependency order should prefer higher old_positions first
            assert len(dependency_order) == len(move_indices)

    def test_empty_operations_handling(self, api_strategy):
        """Test handling of empty operations list."""
        result = api_strategy.simulate_position_shifts([])
        assert result == []

        dependency_order = api_strategy._calculate_dependency_order([])
        assert dependency_order is None

    def test_single_move_operation(self, api_strategy, sample_tracks):
        """Single move operation should not need dependency ordering."""
        operations = [
            PlaylistOperation(
                operation_type=PlaylistOperationType.MOVE,
                track=sample_tracks[0],
                position=1,
                old_position=0,
                spotify_uri="test:move",
            )
        ]

        dependency_order = api_strategy._calculate_dependency_order(operations)
        assert dependency_order is None  # No conflicts with single operation

        adjusted_ops = api_strategy.simulate_position_shifts(operations)
        assert len(adjusted_ops) == 1
        assert adjusted_ops[0] == operations[0]

    def test_execution_plan_metadata(self, api_strategy, sample_tracks):
        """Test that execution plan includes position shift metadata."""
        current_playlist = Playlist(name="Test", tracks=sample_tracks.copy())
        target_tracklist = TrackList(
            tracks=sample_tracks[2:] + sample_tracks[:2]
        )  # Reorder

        diff = calculate_playlist_diff(current_playlist, target_tracklist)
        plan = api_strategy.plan_operations(diff)

        metadata = plan.execution_metadata
        assert metadata["strategy"] == "api"
        assert metadata["position_shift_simulation"] is True
        assert "initial_operations" in metadata
        assert "sequenced_operations" in metadata
        assert "dependency_conflicts_resolved" in metadata

    def test_large_playlist_efficiency(self, api_strategy):
        """Test efficiency with large number of operations."""
        # Create large playlist with many tracks
        tracks = [
            Track(id=i, title=f"Track {i}", artists=[Artist(name=f"Artist {i}")])
            for i in range(100)
        ]

        current_playlist = Playlist(name="Large Test", tracks=tracks)
        target_tracks = list(reversed(tracks))  # Reverse order - worst case
        target_tracklist = TrackList(tracks=target_tracks)

        diff = calculate_playlist_diff(current_playlist, target_tracklist)
        plan = api_strategy.plan_operations(diff)

        # Should complete efficiently
        assert plan.execution_metadata["position_shift_simulation"] is True

        # Should have many move operations, all properly ordered
        move_ops = [
            op
            for op in plan.operations
            if op.operation_type == PlaylistOperationType.MOVE
        ]

        if len(move_ops) > 1:
            # Verify reverse ordering
            old_positions = [
                op.old_position for op in move_ops if op.old_position is not None
            ]
            assert old_positions == sorted(old_positions, reverse=True)
