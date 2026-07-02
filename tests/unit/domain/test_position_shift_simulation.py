"""Tests for API playlist operation sequencing (position-shift simulation).

Validates that plan_api_operations / simulate_position_shifts properly handle
index shifts during sequential operation execution, preventing position
conflicts.
"""

import pytest

from src.domain.entities.playlist import Playlist
from src.domain.entities.track import Artist, Track, TrackList
from src.domain.playlist.diff_engine import (
    PlaylistOperation,
    PlaylistOperationType,
    calculate_playlist_diff,
)
from src.domain.playlist.execution_strategies import (
    plan_api_operations,
    simulate_position_shifts,
)


class TestPositionShiftSimulation:
    """Test position shift simulation for API operations."""

    @pytest.fixture
    def sample_tracks(self):
        """Create sample tracks for testing."""
        return [
            Track(title="Track A", artists=[Artist(name="Artist 1")]),
            Track(title="Track B", artists=[Artist(name="Artist 2")]),
            Track(title="Track C", artists=[Artist(name="Artist 3")]),
            Track(title="Track D", artists=[Artist(name="Artist 4")]),
            Track(title="Track E", artists=[Artist(name="Artist 5")]),
        ]

    def test_move_operations_reverse_order(self, sample_tracks):
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

        # Moves with no removals: sorted by old_position descending for reverse execution.
        adjusted_moves = simulate_position_shifts(move_ops)

        # Should be sorted by old_position in descending order
        assert len(adjusted_moves) == 3
        assert adjusted_moves[0].old_position == 3  # Highest position first
        assert adjusted_moves[1].old_position == 2
        assert adjusted_moves[2].old_position == 1  # Lowest position last

    def test_remove_operations_reverse_order(self, sample_tracks):
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

        adjusted_ops = simulate_position_shifts(operations)

        # Remove operations should be first and in reverse order
        remove_ops = [
            op
            for op in adjusted_ops
            if op.operation_type == PlaylistOperationType.REMOVE
        ]
        assert len(remove_ops) == 2
        assert remove_ops[0].old_position == 3  # Higher position first
        assert remove_ops[1].old_position == 1  # Lower position last

    def test_complex_reordering_scenario(self, sample_tracks):
        """Test complex reordering with many move operations."""
        # Create a scenario with many moves that could cause index conflicts
        current_playlist = Playlist.from_tracklist(
            name="Test", tracklist=sample_tracks.copy()
        )

        # Completely reverse the playlist
        target_tracks = list(reversed(sample_tracks))
        target_tracklist = TrackList(tracks=target_tracks)

        # Calculate diff and plan operations
        diff = calculate_playlist_diff(current_playlist, target_tracklist)
        operations = plan_api_operations(diff)

        # Move operations should be in dependency-safe order
        move_ops = [
            op for op in operations if op.operation_type == PlaylistOperationType.MOVE
        ]

        if len(move_ops) > 1:
            # Verify operations are in reverse order by old_position
            old_positions = [op.old_position for op in move_ops]
            assert old_positions == sorted(old_positions, reverse=True)

    def test_empty_operations_handling(self):
        """Test handling of empty operations list."""
        result = simulate_position_shifts([])
        assert result == []

    def test_single_move_operation(self, sample_tracks):
        """Single move operation should pass through position simulation unchanged."""
        operations = [
            PlaylistOperation(
                operation_type=PlaylistOperationType.MOVE,
                track=sample_tracks[0],
                position=1,
                old_position=0,
                spotify_uri="test:move",
            )
        ]

        adjusted_ops = simulate_position_shifts(operations)
        assert len(adjusted_ops) == 1
        assert adjusted_ops[0] == operations[0]

    def test_large_playlist_efficiency(self):
        """Test efficiency with large number of operations."""
        # Create large playlist with many tracks
        tracks = [
            Track(id=i, title=f"Track {i}", artists=[Artist(name=f"Artist {i}")])
            for i in range(100)
        ]

        current_playlist = Playlist.from_tracklist(name="Large Test", tracklist=tracks)
        target_tracks = list(reversed(tracks))  # Reverse order - worst case
        target_tracklist = TrackList(tracks=target_tracks)

        diff = calculate_playlist_diff(current_playlist, target_tracklist)
        operations = plan_api_operations(diff)

        # Should have many move operations, all properly ordered
        move_ops = [
            op for op in operations if op.operation_type == PlaylistOperationType.MOVE
        ]

        if len(move_ops) > 1:
            # Verify reverse ordering
            old_positions = [
                op.old_position for op in move_ops if op.old_position is not None
            ]
            assert old_positions == sorted(old_positions, reverse=True)
