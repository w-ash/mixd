"""Unit tests for shared operation counter utilities."""

from src.application.use_cases._shared.playlist_results import count_operation_types
from src.domain.entities.track import ArtistCredit, Track
from src.domain.playlist import PlaylistOperation, PlaylistOperationType
from tests.fixtures import TEST_USER_ID


class TestCountOperationTypes:
    """Tests for count_operation_types() utility function."""

    def test_count_only_move_operations(self) -> None:
        """Should correctly count only MOVE operations."""
        track1 = Track(
            title="Track 1",
            artists=[ArtistCredit(credited_name="Artist 1")],
            user_id=TEST_USER_ID,
        )

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
            Track(
                title=f"Track {i}",
                artists=[ArtistCredit(credited_name=f"Artist {i}")],
                user_id=TEST_USER_ID,
            )
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
