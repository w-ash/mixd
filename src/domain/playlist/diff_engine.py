"""Calculates minimal operations to synchronize playlists between different states.

Compares current and target playlist states to generate the smallest set of add, remove,
and move operations needed for synchronization. Optimizes for Spotify API constraints
like batch limits and rate limits. Uses database-first track matching to avoid expensive
re-identification of tracks that already have known Spotify mappings.
"""

from enum import Enum
from typing import Final

from attrs import define, field
from loguru import logger as _loguru_logger

from src.domain.entities.playlist import Playlist
from src.domain.entities.track import Track, TrackList

logger = _loguru_logger.bind(module=__name__)

_DEBUG_TRUNCATION: Final = 10


def _get_track_uri(track: Track) -> str | None:
    """Get infrastructure-agnostic track URI for playlist operations."""
    if track.id:
        return f"canonical:{track.id}"
    elif track.title and track.artists:
        return f"content:{track.title}:{track.artists_display}"
    return None


class PlaylistOperationType(Enum):
    """Operations that can be performed on playlist tracks for synchronization."""

    ADD = "add"
    REMOVE = "remove"
    MOVE = "move"


@define(frozen=True, slots=True)
class PlaylistOperation:
    """Single track operation needed to synchronize playlists.

    Represents an atomic add, remove, or move operation with position information
    and Spotify URI. Includes methods to convert to Spotify API request format.
    """

    operation_type: PlaylistOperationType
    track: Track
    position: int
    old_position: int | None = None
    spotify_uri: str | None = None


@define(frozen=True, slots=True)
class PlaylistDiff:
    """Result of comparing current and target playlist states.

    Contains operations needed to transform current playlist to match target,
    plus metadata like API call estimates and confidence scores for planning.
    """

    operations: list[PlaylistOperation] = field(factory=list)
    confidence_score: float = 1.0  # How confident we are in the match quality

    @property
    def has_changes(self) -> bool:
        """True if any operations are needed to synchronize playlists."""
        return bool(self.operations)

    @property
    def operation_summary(self) -> dict[str, int]:
        """Count of operations by type (add, remove, move)."""
        summary = {op_type.value: 0 for op_type in PlaylistOperationType}
        for op in self.operations:
            summary[op.operation_type.value] += 1
        return summary


def match_tracks_with_db_lookup(
    current_tracks: list[Track], target_tracks: list[Track]
) -> tuple[list[Track], list[Track], list[Track]]:
    """Find matching tracks between playlists using canonical track identity.

    Matches tracks by their canonical track.id first, then falls back to content-based
    matching for tracks without canonical IDs. This keeps the domain layer
    infrastructure-agnostic.

    Args:
        current_tracks: Tracks in current playlist state.
        target_tracks: Tracks in desired playlist state.

    Returns:
        Tuple of (matched_tracks, unmatched_current, unmatched_target).
    """

    # Match tracks using canonical identity (infrastructure-agnostic)
    matched: list[Track] = []
    unmatched_current: list[Track] = []
    consumed_target_indices: set[int] = set()

    def tracks_are_equivalent(track1: Track, track2: Track) -> bool:
        """Check if two tracks represent the same musical work."""
        # Primary: Canonical ID matching
        if track1.id and track2.id:
            return track1.id == track2.id

        # Fallback: Content-based matching
        if (
            not track1.title
            or not track2.title
            or not track1.artists
            or not track2.artists
        ):
            return False

        title_match = track1.title.lower().strip() == track2.title.lower().strip()

        # Artist matching - at least one artist must match
        track1_artists = {artist.name.lower().strip() for artist in track1.artists}
        track2_artists = {artist.name.lower().strip() for artist in track2.artists}
        artist_match = bool(track1_artists & track2_artists)

        # Album matching (optional - tracks can be same without same album)
        album_match = True  # Default to True if either has no album
        if track1.album and track2.album:
            album_match = track1.album.lower().strip() == track2.album.lower().strip()

        return title_match and artist_match and album_match

    # Phase 1: Hash-based canonical ID matching — O(n)
    target_id_index: dict[int, list[int]] = {}
    for idx, t in enumerate(target_tracks):
        if t.id is not None:
            target_id_index.setdefault(t.id, []).append(idx)

    id_unmatched_current: list[Track] = []
    for current_track in current_tracks:
        if current_track.id is not None and current_track.id in target_id_index:
            available = target_id_index[current_track.id]
            if available:
                target_idx = available.pop(0)
                matched.append(current_track)
                consumed_target_indices.add(target_idx)
                continue
        id_unmatched_current.append(current_track)

    # Phase 2: Content-based matching for remaining tracks — O(m²) where m ≪ n
    for current_track in id_unmatched_current:
        match_found = False
        for target_idx, target_track in enumerate(target_tracks):
            if target_idx in consumed_target_indices:
                continue
            if tracks_are_equivalent(current_track, target_track):
                matched.append(current_track)
                consumed_target_indices.add(target_idx)
                match_found = True
                break
        if not match_found:
            unmatched_current.append(current_track)

    # Remaining target tracks are unmatched
    unmatched_target = [
        target_track
        for target_idx, target_track in enumerate(target_tracks)
        if target_idx not in consumed_target_indices
    ]

    # Count tracks with canonical IDs vs content-based matching
    canonical_matches = sum(1 for track in matched if track.id is not None)
    content_matches = len(matched) - canonical_matches

    logger.debug(
        f"Track matching results: {len(matched)} matched, "
        + f"{len(unmatched_current)} unmatched current, {len(unmatched_target)} unmatched target. "
        + f"Canonical ID matches: {canonical_matches}, content-based matches: {content_matches}."
    )

    return matched, unmatched_current, unmatched_target


def calculate_remove_operations(
    unmatched_current_tracks: list[Track], current_tracks: list[Track]
) -> list[PlaylistOperation]:
    """Generate REMOVE operations for tracks in current but not target playlist."""
    operations: list[PlaylistOperation] = []
    position_index = {id(track): idx for idx, track in enumerate(current_tracks)}

    for track in unmatched_current_tracks:
        position = position_index.get(id(track))
        if position is not None:
            operations.append(
                PlaylistOperation(
                    operation_type=PlaylistOperationType.REMOVE,
                    track=track,
                    position=position,
                    old_position=position,
                    spotify_uri=_get_track_uri(track),
                )
            )
        else:
            logger.warning(
                f"Track {track.title} not found in current playlist for removal"
            )

    return operations


def calculate_add_operations(
    unmatched_target_tracks: list[Track], target_tracks: list[Track]
) -> list[PlaylistOperation]:
    """Generate ADD operations for tracks in target but not current playlist."""
    operations: list[PlaylistOperation] = []

    position_index = {id(track): idx for idx, track in enumerate(target_tracks)}

    for track in unmatched_target_tracks:
        target_position = position_index.get(id(track))
        if target_position is not None:
            operations.append(
                PlaylistOperation(
                    operation_type=PlaylistOperationType.ADD,
                    track=track,
                    position=target_position,
                    spotify_uri=_get_track_uri(track),
                )
            )
        else:
            logger.warning(
                f"Track {track.title} not found in target playlist for addition"
            )

    return operations


def calculate_longest_increasing_subsequence(sequence: list[int]) -> list[int]:
    """Calculate the Longest Increasing Subsequence (LIS) of a sequence.

    Uses dynamic programming to find the LIS in O(n log n) time complexity.
    Returns the indices of elements that form the LIS.

    Args:
        sequence: List of integers representing positions

    Returns:
        List of indices forming the longest increasing subsequence
    """
    if not sequence:
        return []

    n = len(sequence)
    # dp[i] stores the smallest ending element of increasing subsequence of length i+1
    dp: list[int] = []
    # parent[i] stores the index of previous element in LIS ending at position i
    parent = [-1] * n
    # lis_indices[i] stores the actual index in dp array for position i
    lis_indices = [-1] * n
    # position_of[k] = index of element currently occupying dp[k] — O(1) parent lookup
    position_of: list[int] = []

    for i in range(n):
        # Binary search for the position to insert/replace
        left, right = 0, len(dp)
        while left < right:
            mid = (left + right) // 2
            if dp[mid] < sequence[i]:
                left = mid + 1
            else:
                right = mid

        if left == len(dp):
            dp.append(sequence[i])
            position_of.append(i)
        else:
            dp[left] = sequence[i]
            position_of[left] = i

        lis_indices[i] = left
        parent[i] = position_of[left - 1] if left > 0 else -1

    # Reconstruct the LIS indices
    lis_length = len(dp)
    if lis_length == 0:
        return []

    # Find the last element of LIS
    last_index = -1
    for i in range(n - 1, -1, -1):
        if lis_indices[i] == lis_length - 1:
            last_index = i
            break

    # Reconstruct the path
    result: list[int] = []
    current = last_index
    while current != -1:
        result.append(current)
        current = parent[current]

    result.reverse()
    return result


def calculate_lis_reorder_operations(
    current_tracks: list[Track], target_tracks: list[Track]
) -> list[PlaylistOperation]:
    """Generate minimal MOVE operations using Longest Increasing Subsequence.

    Uses LIS algorithm to identify tracks that are already in correct relative order,
    then generates minimal move operations for the remaining tracks to achieve
    the target ordering with maximum efficiency. Handles duplicates correctly by
    preserving all position mappings.

    Args:
        current_tracks: Current ordered list of tracks
        target_tracks: Target ordered list of tracks (desired final order)

    Returns:
        List of minimal move operations to transform current to target order
    """
    if not current_tracks or not target_tracks:
        return []

    # Position-aware comparison: treat each playlist position as unique entity
    # Each position represents a unique playlist track instance, even for duplicate tracks
    target_positions_in_current: list[int] = []
    target_track_refs: list[tuple[int, Track, int]] = []

    # Step 1: Direct position-to-position matching for identical tracks
    direct_matches = 0
    first_mismatch = None

    for target_pos, target_track in enumerate(target_tracks):
        if target_track.id is None:
            continue

        # Check if current playlist has a track at this same position
        if target_pos < len(current_tracks):
            current_track = current_tracks[target_pos]

            # If same track at same position, this is already correct (no move needed)
            if current_track.id == target_track.id:
                target_positions_in_current.append(target_pos)
                target_track_refs.append((target_pos, target_track, target_pos))
                direct_matches += 1
            elif first_mismatch is None:
                # Record first mismatch for debugging
                first_mismatch = (target_pos, current_track.id, target_track.id)

    logger.debug(
        f"Position-by-position matching: {direct_matches} direct matches out of {len(target_tracks)} positions"
    )
    if first_mismatch:
        logger.debug(
            f"First mismatch at position {first_mismatch[0]}: current track {first_mismatch[1]} vs target track {first_mismatch[2]}"
        )

    # Step 2: For remaining target positions, find where those tracks currently are
    matched_positions: set[int] = {
        ref[0] for ref in target_track_refs
    }  # target positions already matched
    matched_current_positions: set[int] = {
        ref[2] for ref in target_track_refs
    }  # current positions already used

    # Build index: track.id -> [current positions] for unmatched tracks — O(n)
    current_id_index: dict[int, list[int]] = {}
    for pos, t in enumerate(current_tracks):
        if t.id is not None and pos not in matched_current_positions:
            current_id_index.setdefault(t.id, []).append(pos)

    for target_pos, target_track in enumerate(target_tracks):
        if target_track.id is None or target_pos in matched_positions:
            continue
        available = current_id_index.get(target_track.id, [])
        if available:
            current_pos = available.pop(0)
            target_positions_in_current.append(current_pos)
            target_track_refs.append((target_pos, target_track, current_pos))
            matched_current_positions.add(current_pos)

    if not target_positions_in_current:
        return []  # No tracks to move

    # Find LIS of current positions - these tracks are already in correct relative order
    lis_indices = calculate_longest_increasing_subsequence(target_positions_in_current)

    # Convert LIS indices back to (target_pos, current_pos) pairs that don't need to move
    positions_in_correct_order: set[tuple[int, int]] = set()
    for lis_idx in lis_indices:
        target_pos, target_track, current_pos = target_track_refs[lis_idx]
        positions_in_correct_order.add((target_pos, current_pos))

    # Debug: log some examples of what's being identified as needing to move
    tracks_to_move: list[tuple[int, int, int | None]] = [
        (target_pos, current_pos, target_track.id)
        for target_pos, target_track, current_pos in target_track_refs
        if (target_pos, current_pos) not in positions_in_correct_order
    ]

    logger.debug(
        f"LIS optimization: {len(positions_in_correct_order)} track instances already in correct order, "
        + f"{len(target_track_refs) - len(positions_in_correct_order)} need to move"
    )

    if (
        tracks_to_move and len(tracks_to_move) <= _DEBUG_TRUNCATION
    ):  # Only log if small number
        logger.debug(
            f"Tracks identified as needing moves: {tracks_to_move[:_DEBUG_TRUNCATION]}"
        )

    # Generate move operations only for track instances not in LIS
    operations: list[PlaylistOperation] = []

    for target_pos, target_track, current_pos in target_track_refs:
        if (target_pos, current_pos) not in positions_in_correct_order:
            current_track = current_tracks[current_pos]

            operations.append(
                PlaylistOperation(
                    operation_type=PlaylistOperationType.MOVE,
                    track=current_track,
                    position=target_pos,  # Target position
                    old_position=current_pos,  # Current position
                    spotify_uri=_get_track_uri(target_track),
                )
            )

    logger.debug(
        f"Generated {len(operations)} LIS-optimized move operations "
        + f"(saved {len(positions_in_correct_order)} unnecessary moves)"
    )

    return operations


def calculate_move_operations(
    matched_tracks: list[Track], current_tracks: list[Track], target_tracks: list[Track]
) -> list[PlaylistOperation]:
    """Generate MOVE operations for tracks that exist in both but need reordering.

    Uses Longest Increasing Subsequence (LIS) algorithm to generate minimal move
    operations, avoiding unnecessary position changes for tracks already in correct order.
    Provides true idempotency and mathematical correctness for single-pass execution.
    """
    operations: list[PlaylistOperation] = []

    if not matched_tracks:
        return operations

    # Use LIS-based minimal move calculation
    operations = calculate_lis_reorder_operations(current_tracks, target_tracks)

    logger.debug(
        f"Calculated {len(operations)} LIS-optimized move operations for {len(matched_tracks)} matched tracks"
    )

    return operations


def calculate_confidence_score(
    matched_tracks: list[Track], operations: list[PlaylistOperation]
) -> float:
    """Calculate confidence score as ratio of matched to total tracks."""
    total_tracks = len(matched_tracks) + len(operations)
    if total_tracks == 0:
        return 1.0

    return len(matched_tracks) / total_tracks


def calculate_playlist_diff(
    current_playlist: Playlist, target_playlist: Playlist | TrackList
) -> PlaylistDiff:
    """Calculate minimal operations to transform current playlist to match target.

    Main diff calculation function. Matches tracks using canonical track IDs,
    generates add/remove operations, and estimates costs. Pure domain logic
    with no database dependencies.

    Args:
        current_playlist: Current state of the playlist.
        target_playlist: Desired final state (Playlist or TrackList).

    Returns:
        PlaylistDiff containing operations and metadata.
    """
    # Materialize track lists once (avoids repeated property access)
    current_tracks = current_playlist.tracks
    target_tracks = target_playlist.tracks

    # Fast path: identical ID sequences means no changes
    current_ids = [t.id for t in current_tracks]
    target_ids = [t.id for t in target_tracks]
    if current_ids == target_ids and all(tid is not None for tid in current_ids):
        return PlaylistDiff()

    logger.debug(
        f"Calculating diff: {len(current_tracks)} → {len(target_tracks)} tracks"
    )

    # Step 1: Use sophisticated database-first track matching
    (
        matched_tracks,
        unmatched_current,
        unmatched_target,
    ) = match_tracks_with_db_lookup(current_tracks, target_tracks)

    # Step 2: Calculate operations using functional composition
    remove_operations = calculate_remove_operations(unmatched_current, current_tracks)
    add_operations = calculate_add_operations(unmatched_target, target_tracks)
    move_operations = calculate_move_operations(
        matched_tracks, current_tracks, target_tracks
    )

    # Combine all operations
    all_operations = remove_operations + add_operations + move_operations

    # Step 3: Calculate metadata
    confidence = calculate_confidence_score(matched_tracks, all_operations)

    return PlaylistDiff(
        operations=all_operations,
        confidence_score=confidence,
    )


# Operation sequencing for Spotify API compatibility
def sequence_operations_for_spotify(
    operations: list[PlaylistOperation],
) -> list[PlaylistOperation]:
    """Order operations to preserve Spotify track metadata during synchronization.

    Sequences operations as: remove first, then add, then move. This prevents
    losing track addition timestamps when Spotify treats add+remove of same
    track as modification rather than replacement.
    """
    if not operations:
        return []

    # Partition operations by type
    def get_operation_priority(op: PlaylistOperation) -> int:
        """Return priority for operation sequencing (lower number = execute first)."""
        priority_map = {
            PlaylistOperationType.REMOVE: 0,
            PlaylistOperationType.ADD: 1,
            PlaylistOperationType.MOVE: 2,
        }
        return priority_map[op.operation_type]

    # Sort operations by priority to achieve proper sequencing
    return sorted(operations, key=get_operation_priority)
