"""Unit tests for shared operation counter utilities."""

import pytest

from src.application.use_cases._shared.operation_counters import count_operation_types
from src.application.use_cases._shared.playlist_results import OperationCounts
from src.domain.entities.track import Artist, Track
from src.domain.playlist import PlaylistOperation, PlaylistOperationType


class TestCountOperationTypes:
    """Tests for count_operation_types() utility function."""

    def test_count_empty_operations_list(self) -> None:
        """Should return zero counts for empty list."""
        result = count_operation_types([])

        assert result.added == 0
        assert result.removed == 0
        assert result.moved == 0

    def test_count_only_add_operations(self) -> None:
        """Should correctly count only ADD operations."""
        track1 = Track(title="Track 1", artists=[Artist(name="Artist 1")])
        track2 = Track(title="Track 2", artists=[Artist(name="Artist 2")])
        track3 = Track(title="Track 3", artists=[Artist(name="Artist 3")])

        operations = [
            PlaylistOperation(
                operation_type=PlaylistOperationType.ADD,
                track=track1,
                position=0,
            ),
            PlaylistOperation(
                operation_type=PlaylistOperationType.ADD,
                track=track2,
                position=1,
            ),
            PlaylistOperation(
                operation_type=PlaylistOperationType.ADD,
                track=track3,
                position=2,
            ),
        ]

        result = count_operation_types(operations)

        assert result.added == 3
        assert result.removed == 0
        assert result.moved == 0

    def test_count_only_remove_operations(self) -> None:
        """Should correctly count only REMOVE operations."""
        track1 = Track(title="Track 1", artists=[Artist(name="Artist 1")])
        track2 = Track(title="Track 2", artists=[Artist(name="Artist 2")])

        operations = [
            PlaylistOperation(
                operation_type=PlaylistOperationType.REMOVE,
                track=track1,
                position=0,
            ),
            PlaylistOperation(
                operation_type=PlaylistOperationType.REMOVE,
                track=track2,
                position=1,
            ),
        ]

        result = count_operation_types(operations)

        assert result.added == 0
        assert result.removed == 2
        assert result.moved == 0

    def test_count_only_move_operations(self) -> None:
        """Should correctly count only MOVE operations."""
        track1 = Track(title="Track 1", artists=[Artist(name="Artist 1")])

        operations = [
            PlaylistOperation(
                operation_type=PlaylistOperationType.MOVE,
                track=track1,
                position=0,
                old_position=5,
            ),
        ]

        result = count_operation_types(operations)

        assert result.added == 0
        assert result.removed == 0
        assert result.moved == 1

    def test_count_mixed_operations(self) -> None:
        """Should correctly count mixed operation types."""
        tracks = [
            Track(title=f"Track {i}", artists=[Artist(name=f"Artist {i}")])
            for i in range(7)
        ]

        operations = [
            PlaylistOperation(
                operation_type=PlaylistOperationType.ADD,
                track=tracks[0],
                position=0,
            ),
            PlaylistOperation(
                operation_type=PlaylistOperationType.ADD,
                track=tracks[1],
                position=1,
            ),
            PlaylistOperation(
                operation_type=PlaylistOperationType.REMOVE,
                track=tracks[2],
                position=2,
            ),
            PlaylistOperation(
                operation_type=PlaylistOperationType.REMOVE,
                track=tracks[3],
                position=3,
            ),
            PlaylistOperation(
                operation_type=PlaylistOperationType.REMOVE,
                track=tracks[4],
                position=4,
            ),
            PlaylistOperation(
                operation_type=PlaylistOperationType.MOVE,
                track=tracks[5],
                position=0,
                old_position=10,
            ),
            PlaylistOperation(
                operation_type=PlaylistOperationType.MOVE,
                track=tracks[6],
                position=1,
                old_position=11,
            ),
        ]

        result = count_operation_types(operations)

        assert result.added == 2
        assert result.removed == 3
        assert result.moved == 2

    def test_returns_operation_counts_type(self) -> None:
        """Should return OperationCounts typed object."""
        track = Track(title="Track 1", artists=[Artist(name="Artist 1")])

        operations = [
            PlaylistOperation(
                operation_type=PlaylistOperationType.ADD,
                track=track,
                position=0,
            ),
        ]

        result = count_operation_types(operations)

        assert isinstance(result, OperationCounts)

    def test_operation_counts_immutable(self) -> None:
        """OperationCounts should be immutable (frozen)."""
        result = count_operation_types([])

        # Should raise because OperationCounts is frozen
        with pytest.raises(Exception):  # attrs.FrozenInstanceError
            result.added = 5  # type: ignore[misc]
