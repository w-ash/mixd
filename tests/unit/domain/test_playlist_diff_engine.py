"""Comprehensive tests for the LIS-based playlist diff engine.

Tests validate 100% first-pass accuracy, minimal moves, and proper duplicate handling.
Critical for ensuring the three-layer architecture meets success criteria.
"""

import itertools

from attrs import evolve
import pytest

from src.domain.entities.playlist import Playlist
from src.domain.entities.track import ArtistCredit, Track, TrackList
from src.domain.playlist.diff_engine import (
    PlaylistOperation,
    PlaylistOperationType,
    calculate_add_operations,
    calculate_lis_reorder_operations,
    calculate_longest_increasing_subsequence,
    calculate_playlist_diff,
    calculate_remove_operations,
)
from tests.fixtures import TEST_USER_ID


def _assert_strictly_increasing_subsequence(
    sequence: list[int], indices: list[int]
) -> None:
    """Indices ascend and pick strictly increasing values."""
    assert indices == sorted(set(indices))
    values = [sequence[i] for i in indices]
    assert all(a < b for a, b in itertools.pairwise(values))


def _unmoved_tracks_keep_target_order(
    current: list[Track], target: list[Track], operations: list[PlaylistOperation]
) -> bool:
    """Whether single-track moves can reach ``target`` from ``current``.

    A move never changes the relative order of the tracks it does not touch, so
    the unmoved tracks must already appear in ``target`` order.
    """
    moved = {op.old_position for op in operations}
    unmoved = [track.id for pos, track in enumerate(current) if pos not in moved]
    remaining = iter([track.id for track in target])
    return all(track_id in remaining for track_id in unmoved)


class TestLongestIncreasingSubsequence:
    """Test the core LIS algorithm for correctness."""

    def test_empty_sequence(self):
        """Empty sequence should return empty LIS."""
        result = calculate_longest_increasing_subsequence([])
        assert result == []

    def test_single_element(self):
        """Single element sequence should return that element."""
        result = calculate_longest_increasing_subsequence([5])
        assert result == [0]

    def test_already_sorted(self):
        """Already sorted sequence should return all indices."""
        result = calculate_longest_increasing_subsequence([1, 2, 3, 4, 5])
        assert result == [0, 1, 2, 3, 4]

    def test_reverse_sorted(self):
        """Reverse sorted should return single element."""
        result = calculate_longest_increasing_subsequence([5, 4, 3, 2, 1])
        assert len(result) == 1  # Only one element can be in increasing order

    def test_complex_sequence(self):
        """[10, 9, 2, 5, 3, 7, 101, 18] has longest increasing runs of length 4."""
        sequence = [10, 9, 2, 5, 3, 7, 101, 18]
        result = calculate_longest_increasing_subsequence(sequence)

        # e.g., [2, 3, 7, 101] or [2, 5, 7, 18]
        assert len(result) == 4
        _assert_strictly_increasing_subsequence(sequence, result)

    def test_duplicates_in_sequence(self):
        """Equal values cannot share a strictly increasing run: [1, 3, 5] is longest."""
        sequence = [1, 3, 3, 5, 2, 4]
        result = calculate_longest_increasing_subsequence(sequence)

        assert len(result) == 3
        _assert_strictly_increasing_subsequence(sequence, result)


class TestLISReorderOperations:
    """Test LIS-based reorder operation generation."""

    @pytest.fixture
    def sample_tracks(self):
        """Create sample tracks for testing."""
        return [
            Track(
                title="Track A",
                artists=[ArtistCredit(credited_name="Artist 1")],
                user_id=TEST_USER_ID,
            ),
            Track(
                title="Track B",
                artists=[ArtistCredit(credited_name="Artist 2")],
                user_id=TEST_USER_ID,
            ),
            Track(
                title="Track C",
                artists=[ArtistCredit(credited_name="Artist 3")],
                user_id=TEST_USER_ID,
            ),
            Track(
                title="Track D",
                artists=[ArtistCredit(credited_name="Artist 4")],
                user_id=TEST_USER_ID,
            ),
            Track(
                title="Track E",
                artists=[ArtistCredit(credited_name="Artist 5")],
                user_id=TEST_USER_ID,
            ),
        ]

    def test_identical_order_no_operations(self, sample_tracks):
        """Identical playlists should generate zero move operations."""
        current = sample_tracks.copy()
        target = sample_tracks.copy()

        operations = calculate_lis_reorder_operations(current, target)
        assert len(operations) == 0

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "calculate_lis_reorder_operations lists same-position matches ahead of "
            "the other target positions, so the LIS runs on a permuted sequence and "
            "leaves tracks unmoved that must swap order"
        ),
    )
    @pytest.mark.parametrize(
        ("target_order", "minimal_moves"),
        [
            pytest.param([4, 3, 2, 1, 0], 4, id="odd-length-reversal"),
            pytest.param([4, 1, 2, 3, 0], 2, id="swap-the-ends"),
        ],
    )
    def test_reorder_reaches_the_target_when_a_track_is_already_in_place(
        self, sample_tracks, target_order: list[int], minimal_moves: int
    ):
        """Minimal moves = tracks outside the longest run already in target order.

        Both targets keep one track at its current position. The reversal's
        longest in-order run is 1 track (4 moves); swapping the ends keeps the
        middle 3 in order (2 moves).
        """
        current = sample_tracks.copy()
        target = [sample_tracks[i] for i in target_order]

        operations = calculate_lis_reorder_operations(current, target)

        assert _unmoved_tracks_keep_target_order(current, target, operations)
        assert len(operations) == minimal_moves

    def test_single_track_move(self, sample_tracks):
        """Swapping the first two tracks reaches the target in at most two moves."""
        current = sample_tracks.copy()  # [1, 2, 3, 4, 5]
        target = [
            sample_tracks[1],
            sample_tracks[0],
            sample_tracks[2],
            sample_tracks[3],
            sample_tracks[4],
        ]  # [2, 1, 3, 4, 5]

        operations = calculate_lis_reorder_operations(current, target)

        assert _unmoved_tracks_keep_target_order(current, target, operations)
        assert 1 <= len(operations) <= 2

    def test_duplicate_tracks_handling(self):
        """Duplicate tracks reorder by position, each copy matched once."""
        track_a = Track(
            title="Track A",
            artists=[ArtistCredit(credited_name="Artist 1")],
            user_id=TEST_USER_ID,
        )
        track_b = Track(
            title="Track B",
            artists=[ArtistCredit(credited_name="Artist 2")],
            user_id=TEST_USER_ID,
        )

        current = [track_a, track_b, track_a, track_b]  # [A, B, A, B]
        target = [track_b, track_a, track_b, track_a]  # [B, A, B, A]

        operations = calculate_lis_reorder_operations(current, target)

        assert _unmoved_tracks_keep_target_order(current, target, operations)
        assert all(op.operation_type == PlaylistOperationType.MOVE for op in operations)

    def test_partial_reorder_with_lis_optimization(self, sample_tracks):
        """Partial reorder should demonstrate LIS optimization savings."""
        current = sample_tracks.copy()  # [1, 2, 3, 4, 5]
        # Move track 2 to end: [1, 3, 4, 5, 2]
        target = [
            sample_tracks[0],
            sample_tracks[2],
            sample_tracks[3],
            sample_tracks[4],
            sample_tracks[1],
        ]

        operations = calculate_lis_reorder_operations(current, target)

        # With LIS optimization, tracks [A, C, D, E] should stay in place
        # Only track B should need to move
        assert len(operations) == 1
        assert operations[0].track.id == sample_tracks[1].id
        assert operations[0].position == 4  # Moving to end


class TestPlaylistDiffIntegration:
    """Test the complete diff engine with LIS optimizations."""

    @pytest.fixture
    def sample_playlist(self):
        """Create sample playlist for testing."""
        tracks = [
            Track(
                title="Track A",
                artists=[ArtistCredit(credited_name="Artist 1")],
                user_id=TEST_USER_ID,
            ),
            Track(
                title="Track B",
                artists=[ArtistCredit(credited_name="Artist 2")],
                user_id=TEST_USER_ID,
            ),
            Track(
                title="Track C",
                artists=[ArtistCredit(credited_name="Artist 3")],
                user_id=TEST_USER_ID,
            ),
            Track(
                title="Track D",
                artists=[ArtistCredit(credited_name="Artist 4")],
                user_id=TEST_USER_ID,
            ),
        ]
        return Playlist.from_tracklist(
            name="Test Playlist", tracklist=tracks, user_id=TEST_USER_ID
        )

    def test_no_changes_idempotent(self, sample_playlist):
        """Unchanged playlist should generate zero operations (idempotent)."""
        target_tracklist = TrackList(tracks=sample_playlist.tracks.copy())

        diff = calculate_playlist_diff(sample_playlist, target_tracklist)

        assert not diff.has_changes
        assert len(diff.operations) == 0
        assert diff.confidence_score == 1.0

    def test_add_operations_only(self, sample_playlist):
        """Adding tracks should generate only ADD operations."""
        new_track = Track(
            title="Track E",
            artists=[ArtistCredit(credited_name="Artist 5")],
            user_id=TEST_USER_ID,
        )
        target_tracks = [*sample_playlist.tracks, new_track]
        target_tracklist = TrackList(tracks=target_tracks)

        diff = calculate_playlist_diff(sample_playlist, target_tracklist)

        assert diff.has_changes
        add_ops = [
            op
            for op in diff.operations
            if op.operation_type == PlaylistOperationType.ADD
        ]
        move_ops = [
            op
            for op in diff.operations
            if op.operation_type == PlaylistOperationType.MOVE
        ]

        assert len(add_ops) == 1
        assert len(move_ops) == 0  # No moves needed when just adding
        assert add_ops[0].track.id == new_track.id

    def test_remove_operations_only(self, sample_playlist):
        """Removing tracks should generate only REMOVE operations."""
        target_tracks = sample_playlist.tracks[:-1]  # Remove last track
        target_tracklist = TrackList(tracks=target_tracks)

        diff = calculate_playlist_diff(sample_playlist, target_tracklist)

        assert diff.has_changes
        remove_ops = [
            op
            for op in diff.operations
            if op.operation_type == PlaylistOperationType.REMOVE
        ]
        move_ops = [
            op
            for op in diff.operations
            if op.operation_type == PlaylistOperationType.MOVE
        ]

        assert len(remove_ops) == 1
        assert len(move_ops) == 0  # No moves needed when just removing
        assert remove_ops[0].track.id == sample_playlist.tracks[-1].id

    def test_move_operations_with_lis_optimization(self, sample_playlist):
        """Reordering should use LIS optimization for minimal moves."""
        # Reverse the playlist order
        target_tracks = list(reversed(sample_playlist.tracks))
        target_tracklist = TrackList(tracks=target_tracks)

        diff = calculate_playlist_diff(sample_playlist, target_tracklist)

        assert diff.has_changes
        move_ops = [
            op
            for op in diff.operations
            if op.operation_type == PlaylistOperationType.MOVE
        ]
        add_ops = [
            op
            for op in diff.operations
            if op.operation_type == PlaylistOperationType.ADD
        ]
        remove_ops = [
            op
            for op in diff.operations
            if op.operation_type == PlaylistOperationType.REMOVE
        ]

        # Should only have move operations, no add/remove
        assert len(add_ops) == 0
        assert len(remove_ops) == 0

        # LIS optimization should reduce the number of moves needed
        # For a complete reversal of 4 tracks, should need 3 moves (LIS of 1)
        assert len(move_ops) == 3

    def test_complex_mixed_operations(self, sample_playlist):
        """Complex changes should generate correct mix of operations."""
        track_b = sample_playlist.tracks[1]
        # Remove track B, add new track, reorder remaining
        remaining_tracks = [t for t in sample_playlist.tracks if t.id != track_b.id]
        new_track = Track(
            title="Track E",
            artists=[ArtistCredit(credited_name="Artist 5")],
            user_id=TEST_USER_ID,
        )
        # Reorder: [new_track, track_D, track_A, track_C]
        target_tracks = [
            new_track,
            remaining_tracks[2],
            remaining_tracks[0],
            remaining_tracks[1],
        ]
        target_tracklist = TrackList(tracks=target_tracks)

        diff = calculate_playlist_diff(sample_playlist, target_tracklist)

        assert diff.has_changes

        # Count operations by type
        add_ops = [
            op
            for op in diff.operations
            if op.operation_type == PlaylistOperationType.ADD
        ]
        remove_ops = [
            op
            for op in diff.operations
            if op.operation_type == PlaylistOperationType.REMOVE
        ]
        assert len(add_ops) == 1  # Adding new track
        assert len(remove_ops) == 1  # Removing track B

        # Verify correct tracks in operations
        assert add_ops[0].track.id == new_track.id
        assert remove_ops[0].track.id == track_b.id

    def test_confidence_score_calculation(self, sample_playlist):
        """Confidence score should reflect match quality."""
        # Perfect match should have confidence 1.0
        target_tracklist = TrackList(tracks=sample_playlist.tracks.copy())
        diff = calculate_playlist_diff(sample_playlist, target_tracklist)
        assert diff.confidence_score == 1.0

        # 2 of 4 tracks kept, 2 removed: matched / (matched + operations) = 2 / 4
        target_tracks = sample_playlist.tracks[:2]  # Only first 2 tracks
        target_tracklist = TrackList(tracks=target_tracks)
        diff = calculate_playlist_diff(sample_playlist, target_tracklist)
        assert diff.confidence_score == 0.5

    def test_even_length_reversal_keeps_one_track_in_place(self):
        """Reversing 100 tracks leaves an in-order run of 1, so 99 tracks move."""
        # Create large playlist (100 tracks)
        tracks = [
            Track(
                title=f"Track {i}",
                artists=[ArtistCredit(credited_name=f"Artist {i}")],
                user_id=TEST_USER_ID,
            )
            for i in range(100)
        ]
        playlist = Playlist.from_tracklist(
            name="Large Playlist", tracklist=tracks, user_id=TEST_USER_ID
        )

        # Reverse the order for maximum reordering challenge
        target_tracks = list(reversed(tracks))
        target_tracklist = TrackList(tracks=target_tracks)

        diff = calculate_playlist_diff(playlist, target_tracklist)

        move_ops = [
            op
            for op in diff.operations
            if op.operation_type == PlaylistOperationType.MOVE
        ]
        assert len(move_ops) == 99


class TestDuplicateSharedInstanceDiff:
    """A single ``Track`` instance at several positions must yield one op per
    position, not collapse to the last.

    The PUSH path's ``external_as_playlist`` resolves duplicate remote ids to one
    shared canonical ``Track``, so the same object lands at multiple slots. The
    old ``{id(track): idx}`` index kept only the last slot, mis-targeting or
    dropping the duplicate removes/adds. These are regression guards for that.
    """

    def test_remove_collapsed_duplicates_targets_distinct_positions(self):
        """``[X, X] -> [Y]``: both X copies removed at their own positions."""
        x = Track(
            title="X", artists=[ArtistCredit(credited_name="A")], user_id=TEST_USER_ID
        )
        y = Track(
            title="Y", artists=[ArtistCredit(credited_name="B")], user_id=TEST_USER_ID
        )
        current = Playlist.from_tracklist(
            name="dup", tracklist=[x, x], user_id=TEST_USER_ID
        )
        # Sanity: the entries genuinely share one instance.
        assert current.tracks[0] is current.tracks[1]

        diff = calculate_playlist_diff(current, TrackList(tracks=[y]))

        remove_ops = [
            op
            for op in diff.operations
            if op.operation_type == PlaylistOperationType.REMOVE
        ]
        assert sorted(op.position for op in remove_ops) == [0, 1]
        add_ops = [
            op
            for op in diff.operations
            if op.operation_type == PlaylistOperationType.ADD
        ]
        assert [op.position for op in add_ops] == [0]
        assert all(op.track.id == y.id for op in add_ops)

    def test_remove_interleaved_duplicate_keeps_distinct_positions(self):
        """``[X, Y, X] -> [Y]``: both X copies removed at positions 0 and 2."""
        x = Track(
            title="X", artists=[ArtistCredit(credited_name="A")], user_id=TEST_USER_ID
        )
        y = Track(
            title="Y", artists=[ArtistCredit(credited_name="B")], user_id=TEST_USER_ID
        )
        current = Playlist.from_tracklist(
            name="dup", tracklist=[x, y, x], user_id=TEST_USER_ID
        )

        diff = calculate_playlist_diff(current, TrackList(tracks=[y]))

        remove_ops = [
            op
            for op in diff.operations
            if op.operation_type == PlaylistOperationType.REMOVE
        ]
        assert sorted(op.position for op in remove_ops) == [0, 2]

    def test_add_collapsed_duplicates_targets_distinct_positions(self):
        """``[Y] -> [X, X]``: both X copies added at their own positions."""
        x = Track(
            title="X", artists=[ArtistCredit(credited_name="A")], user_id=TEST_USER_ID
        )
        y = Track(
            title="Y", artists=[ArtistCredit(credited_name="B")], user_id=TEST_USER_ID
        )
        current = Playlist.from_tracklist(
            name="dup", tracklist=[y], user_id=TEST_USER_ID
        )

        diff = calculate_playlist_diff(current, TrackList(tracks=[x, x]))

        add_ops = [
            op
            for op in diff.operations
            if op.operation_type == PlaylistOperationType.ADD
        ]
        assert sorted(op.position for op in add_ops) == [0, 1]

    def test_remove_helper_consumes_each_shared_position_once(self):
        """Direct unit check: the remove helper consumes one slot per occurrence."""
        x = Track(
            title="X", artists=[ArtistCredit(credited_name="A")], user_id=TEST_USER_ID
        )
        ops = calculate_remove_operations([x, x], [x, x])
        assert sorted(op.position for op in ops) == [0, 1]

    def test_add_helper_consumes_each_shared_position_once(self):
        """Direct unit check: the add helper consumes one slot per occurrence."""
        x = Track(
            title="X", artists=[ArtistCredit(credited_name="A")], user_id=TEST_USER_ID
        )
        ops = calculate_add_operations([x, x], [x, x])
        assert sorted(op.position for op in ops) == [0, 1]


class TestKeyedByCanonicalTrackId:
    """The position index keys by canonical ``track.id``, not Python object
    identity (``id(track)``).

    ``external_as_playlist`` happens to share one ``Track`` instance per canonical
    id today, so object identity would key identically on real inputs — but
    ``id(track)`` is an ephemeral, non-serializable address, not a domain key. These
    guards pass distinct-but-equal instances (same ``track.id``, different objects)
    so they hold only when keying is by ``track.id``; an ``id(track)`` index would
    miss the lookup and silently drop the operation.
    """

    def test_remove_matches_distinct_instance_with_same_track_id(self):
        x = Track(
            title="X", artists=[ArtistCredit(credited_name="A")], user_id=TEST_USER_ID
        )
        x_copy = evolve(x)  # same track.id, distinct object
        assert x_copy is not x
        assert x_copy.id == x.id

        ops = calculate_remove_operations([x_copy], [x])

        assert [op.position for op in ops] == [0]

    def test_add_matches_distinct_instance_with_same_track_id(self):
        x = Track(
            title="X", artists=[ArtistCredit(credited_name="A")], user_id=TEST_USER_ID
        )
        x_copy = evolve(x)  # same track.id, distinct object
        assert x_copy is not x
        assert x_copy.id == x.id

        ops = calculate_add_operations([x_copy], [x])

        assert [op.position for op in ops] == [0]
