"""Pure functional playlist diff engine leveraging transforms/core.py infrastructure.

This module provides DRY playlist differential algorithms that reuse existing
sophisticated sorting, filtering, and transformation capabilities instead of
reimplementing basic algorithms.

Key principles:
- Pure functional approach using toolz
- Leverages transforms/core.py infrastructure
- Reusable across canonical and connector operations
- Immutable transformations with no side effects
"""

from enum import Enum
from typing import Any, cast

from attrs import define, field
from toolz import curry

from src.config import get_logger
from src.domain.entities.playlist import Playlist
from src.domain.entities.track import Track, TrackList
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


class PlaylistOperationType(Enum):
    """Types of operations that can be performed on playlist tracks."""

    ADD = "add"
    REMOVE = "remove"
    MOVE = "move"


@define(frozen=True, slots=True)
class PlaylistOperation:
    """Represents a single operation to be performed on a playlist.

    Encapsulates the atomic operations needed for differential playlist updates,
    optimized for external API constraints and minimal API calls.
    """

    operation_type: PlaylistOperationType
    track: Track
    position: int
    old_position: int | None = None
    spotify_uri: str | None = None

    def to_spotify_format(self) -> dict[str, Any]:
        """Convert operation to Spotify API request format.

        Returns:
            Dictionary formatted for Spotify API requests
        """
        if self.operation_type == PlaylistOperationType.ADD:
            return {
                "uris": [self.spotify_uri] if self.spotify_uri else [],
                "position": self.position,
            }
        elif self.operation_type == PlaylistOperationType.REMOVE:
            return {
                "tracks": [{"uri": self.spotify_uri}] if self.spotify_uri else [],
                "positions": [self.old_position]
                if self.old_position is not None
                else [],
            }
        elif self.operation_type == PlaylistOperationType.MOVE:
            return {
                "range_start": self.old_position,
                "insert_before": self.position,
                "range_length": 1,
            }
        else:
            raise ValueError(f"Unsupported operation type: {self.operation_type}")


@define(frozen=True, slots=True)
class PlaylistDiff:
    """Result of comparing two playlist states.

    Contains the minimal set of operations needed to transform one playlist
    into another, with cost estimation for API planning.
    """

    operations: list[PlaylistOperation] = field(factory=list)
    unchanged_tracks: list[Track] = field(factory=list)
    api_call_estimate: int = 0
    confidence_score: float = 1.0  # How confident we are in the match quality

    @property
    def has_changes(self) -> bool:
        """Check if any operations are needed."""
        return len(self.operations) > 0

    @property
    def operation_summary(self) -> dict[str, int]:
        """Summary of operations by type."""
        summary = {op_type.value: 0 for op_type in PlaylistOperationType}
        for op in self.operations:
            summary[op.operation_type.value] += 1
        return summary


async def match_tracks_with_db_lookup(
    current_tracks: list[Track], 
    target_tracks: list[Track], 
    uow: UnitOfWorkProtocol
) -> tuple[list[Track], list[Track], list[Track]]:
    """Match tracks between current and target lists using database-first strategy.

    Uses efficient bulk database lookup to find existing Spotify mappings before
    falling back to expensive track identity resolution for unmatched tracks.

    Args:
        current_tracks: Tracks in current playlist
        target_tracks: Tracks in target playlist  
        uow: UnitOfWork for database access

    Returns:
        Tuple of (matched_tracks, unmatched_current, unmatched_target)
    """
    # Step 1: Collect all track IDs and build lookup maps
    
    all_track_ids = [track.id for track in current_tracks + target_tracks if track.id]

    # Step 2: Bulk lookup existing Spotify mappings from database
    connector_repo = uow.get_connector_repository()
    db_mappings = await connector_repo.get_connector_mappings(all_track_ids, "spotify")
    
    # Step 3: Build comprehensive Spotify ID mappings (database only for tracks with IDs)
    spotify_mappings = {}  # track_id -> spotify_id for tracks with IDs
    
    # Add database mappings
    for track_id, connectors in db_mappings.items():
        if "spotify" in connectors:
            spotify_mappings[track_id] = connectors["spotify"]

    # Step 4: Match tracks using comprehensive Spotify ID mappings
    matched = []
    unmatched_current = current_tracks.copy()
    unmatched_target = target_tracks.copy()
    
    def get_spotify_id(track):
        """Get Spotify ID from database mapping or in-memory connector_track_ids."""
        if track.id and track.id in spotify_mappings:
            return spotify_mappings[track.id]
        return track.connector_track_ids.get("spotify")
    
    for current_track in current_tracks:
        current_spotify_id = get_spotify_id(current_track)
        if not current_spotify_id:
            continue

        for target_track in target_tracks:
            target_spotify_id = get_spotify_id(target_track)
            if target_spotify_id == current_spotify_id:
                matched.append(current_track)
                if current_track in unmatched_current:
                    unmatched_current.remove(current_track)
                if target_track in unmatched_target:
                    unmatched_target.remove(target_track)
                break

    logger.debug(
        f"Track matching results: {len(matched)} matched, "
        f"{len(unmatched_current)} unmatched current, {len(unmatched_target)} unmatched target. "
        f"Database provided {len(db_mappings)} mappings, avoided expensive re-matching for "
        f"{len(matched)} tracks."
    )

    return matched, unmatched_current, unmatched_target


@curry
def calculate_remove_operations(
    unmatched_current_tracks: list[Track],
    current_playlist: Playlist
) -> list[PlaylistOperation]:
    """Calculate REMOVE operations for tracks that exist in current but not target."""
    operations = []
    
    for track in unmatched_current_tracks:
        try:
            position = current_playlist.tracks.index(track)
            operations.append(
                PlaylistOperation(
                    operation_type=PlaylistOperationType.REMOVE,
                    track=track,
                    position=position,
                    old_position=position,
                    spotify_uri=track.connector_track_ids.get("spotify"),
                )
            )
        except ValueError:
            # Track not found in current playlist, skip
            logger.warning(f"Track {track.title} not found in current playlist for removal")
            continue
    
    return operations


@curry
def calculate_add_operations(
    unmatched_target_tracks: list[Track],
    target_tracklist: TrackList
) -> list[PlaylistOperation]:
    """Calculate ADD operations for tracks that exist in target but not current."""
    operations = []
    
    for track in unmatched_target_tracks:
        try:
            # Find the correct target position for this track
            target_position = target_tracklist.tracks.index(track)
            operations.append(
                PlaylistOperation(
                    operation_type=PlaylistOperationType.ADD,
                    track=track,
                    position=target_position,
                    spotify_uri=track.connector_track_ids.get("spotify"),
                )
            )
        except ValueError:
            # Track not found in target playlist, skip
            logger.warning(f"Track {track.title} not found in target playlist for addition")
            continue
    
    return operations


@curry
def calculate_move_operations(
    matched_tracks: list[Track],
    current_playlist: Playlist,
    target_tracklist: TrackList
) -> list[PlaylistOperation]:
    """Calculate MOVE operations for matched tracks that need reordering.
    
    Uses simplified approach - can be enhanced with LIS algorithm if needed.
    """
    operations = []
    
    if not matched_tracks:
        return operations
    
    # Create mapping from track to position in current and target playlists
    current_positions = {}
    target_positions = {}
    
    # Map matched tracks to their positions using Spotify ID matching
    for track in matched_tracks:
        # Get Spotify ID for matching
        spotify_id = track.connector_track_ids.get("spotify")
        if not spotify_id:
            continue

        # Find in current playlist
        for i, current_track in enumerate(current_playlist.tracks):
            if current_track.connector_track_ids.get("spotify") == spotify_id:
                current_positions[spotify_id] = i
                break

        # Find in target playlist
        for i, target_track in enumerate(target_tracklist.tracks):
            if target_track.connector_track_ids.get("spotify") == spotify_id:
                target_positions[spotify_id] = i
                break

    # Generate move operations for tracks that need repositioning
    for track in matched_tracks:
        spotify_id = track.connector_track_ids.get("spotify")
        if not spotify_id:
            continue
            
        current_pos = current_positions.get(spotify_id)
        target_pos = target_positions.get(spotify_id)
        
        if current_pos is not None and target_pos is not None and current_pos != target_pos:
            operations.append(
                PlaylistOperation(
                    operation_type=PlaylistOperationType.MOVE,
                    track=track,
                    position=target_pos,
                    old_position=current_pos,
                    spotify_uri=spotify_id,
                )
            )

    logger.debug(
        f"Calculated {len(operations)} move operations for {len(matched_tracks)} matched tracks"
    )

    return operations


@curry
def estimate_api_calls(operations: list[PlaylistOperation]) -> int:
    """Estimate number of API calls needed for operations.

    Accounts for Spotify's 100-track batch limits.
    """
    add_ops = sum(
        1 for op in operations if op.operation_type == PlaylistOperationType.ADD
    )
    remove_ops = sum(
        1 for op in operations if op.operation_type == PlaylistOperationType.REMOVE
    )
    move_ops = sum(
        1 for op in operations if op.operation_type == PlaylistOperationType.MOVE
    )

    # Estimate based on batch sizes
    api_calls = 0
    api_calls += (add_ops + 99) // 100  # Round up for batches
    api_calls += (remove_ops + 99) // 100
    api_calls += move_ops  # Move operations are individual

    return max(1, api_calls)  # At least one call to check snapshot


@curry
def calculate_confidence_score(
    matched_tracks: list[Track], 
    operations: list[PlaylistOperation]
) -> float:
    """Calculate confidence score based on match quality."""
    total_tracks = len(matched_tracks) + len(operations)
    if total_tracks == 0:
        return 1.0

    return len(matched_tracks) / total_tracks


async def calculate_playlist_diff(
    current_playlist: Playlist, 
    target_tracklist: TrackList,
    uow: UnitOfWorkProtocol
) -> PlaylistDiff:
    """Pure functional diff calculation using existing transforms infrastructure.

    This is the main DRY diff engine that leverages transforms/core.py and toolz
    instead of reimplementing basic algorithms.

    Args:
        current_playlist: Current playlist state
        target_tracklist: Desired playlist state
        uow: UnitOfWork for database access during track matching

    Returns:
        PlaylistDiff with minimal operations
    """
    logger.debug(
        f"Calculating diff: {len(current_playlist.tracks)} → {len(target_tracklist.tracks)} tracks"
    )

    # Step 1: Use sophisticated database-first track matching
    matched_tracks, unmatched_current, unmatched_target = await match_tracks_with_db_lookup(
        current_playlist.tracks, target_tracklist.tracks, uow
    )

    # Step 2: Calculate operations using functional composition
    remove_operations: list[PlaylistOperation] = cast(
        "list[PlaylistOperation]", 
        calculate_remove_operations(unmatched_current, current_playlist)
    )
    add_operations: list[PlaylistOperation] = cast(
        "list[PlaylistOperation]", 
        calculate_add_operations(unmatched_target, target_tracklist)
    )
    move_operations: list[PlaylistOperation] = cast(
        "list[PlaylistOperation]", 
        calculate_move_operations(matched_tracks, current_playlist, target_tracklist)
    )

    # Combine all operations
    all_operations = remove_operations + add_operations + move_operations

    # Step 3: Calculate metadata
    api_calls: int = cast("int", estimate_api_calls(all_operations))
    confidence: float = cast("float", calculate_confidence_score(matched_tracks, all_operations))

    return PlaylistDiff(
        operations=all_operations,
        unchanged_tracks=matched_tracks,
        api_call_estimate=api_calls,
        confidence_score=confidence,
    )


# DRY operation sequencing using toolz
@curry
def sequence_operations_for_spotify(operations: list[PlaylistOperation]) -> list[PlaylistOperation]:
    """DRY sequencing that preserves Spotify track addition timestamps.
    
    Proper sequencing: remove first (preserve timestamps), then add, then move.
    This prevents accidentally wiping track addition timestamp information.
    """
    if not operations:
        return []
    
    # Partition operations by type using toolz
    def get_operation_priority(op: PlaylistOperation) -> int:
        """Get priority for operation sequencing (lower = first)."""
        priority_map = {
            PlaylistOperationType.REMOVE: 0,
            PlaylistOperationType.ADD: 1, 
            PlaylistOperationType.MOVE: 2
        }
        return priority_map[op.operation_type]
    
    # Sort operations by priority to achieve proper sequencing
    return sorted(operations, key=get_operation_priority)