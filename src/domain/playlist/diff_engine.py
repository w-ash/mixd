"""Calculates minimal operations to synchronize playlists between different states.

Compares current and target playlist states to generate the smallest set of add, remove,
and move operations needed for synchronization. Optimizes for Spotify API constraints
like batch limits and rate limits. Uses database-first track matching to avoid expensive
re-identification of tracks that already have known Spotify mappings.
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

    def to_spotify_format(self) -> dict[str, Any]:
        """Convert operation to Spotify Web API request parameters.

        Returns:
            Dictionary with parameters for Spotify playlist modification endpoints.
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
    """Result of comparing current and target playlist states.

    Contains operations needed to transform current playlist to match target,
    plus metadata like API call estimates and confidence scores for planning.
    """

    operations: list[PlaylistOperation] = field(factory=list)
    unchanged_tracks: list[Track] = field(factory=list)
    api_call_estimate: int = 0
    confidence_score: float = 1.0  # How confident we are in the match quality

    @property
    def has_changes(self) -> bool:
        """True if any operations are needed to synchronize playlists."""
        return len(self.operations) > 0

    @property
    def operation_summary(self) -> dict[str, int]:
        """Count of operations by type (add, remove, move)."""
        summary = {op_type.value: 0 for op_type in PlaylistOperationType}
        for op in self.operations:
            summary[op.operation_type.value] += 1
        return summary


async def match_tracks_with_db_lookup(
    current_tracks: list[Track], target_tracks: list[Track], uow: UnitOfWorkProtocol
) -> tuple[list[Track], list[Track], list[Track]]:
    """Find matching tracks between playlists using database Spotify ID mappings.

    Bulk-loads existing Spotify mappings from database to identify which tracks
    are the same between current and target playlists. Avoids expensive track
    re-identification for tracks that already have known Spotify IDs.

    Args:
        current_tracks: Tracks in current playlist state.
        target_tracks: Tracks in desired playlist state.
        uow: Database access for loading existing Spotify mappings.

    Returns:
        Tuple of (matched_tracks, unmatched_current, unmatched_target).
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
        """Get Spotify ID from database mapping or track's connector_track_ids."""
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
    unmatched_current_tracks: list[Track], current_playlist: Playlist
) -> list[PlaylistOperation]:
    """Generate REMOVE operations for tracks in current but not target playlist."""
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
            logger.warning(
                f"Track {track.title} not found in current playlist for removal"
            )
            continue

    return operations


@curry
def calculate_add_operations(
    unmatched_target_tracks: list[Track], target_tracklist: TrackList
) -> list[PlaylistOperation]:
    """Generate ADD operations for tracks in target but not current playlist."""
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
            logger.warning(
                f"Track {track.title} not found in target playlist for addition"
            )
            continue

    return operations


@curry
def calculate_move_operations(
    matched_tracks: list[Track], current_playlist: Playlist, target_tracklist: TrackList
) -> list[PlaylistOperation]:
    """Generate MOVE operations for tracks that exist in both but need reordering.

    Compares positions of matched tracks between current and target playlists.
    Creates move operations for tracks that need to change position.
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

        if (
            current_pos is not None
            and target_pos is not None
            and current_pos != target_pos
        ):
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
    """Estimate Spotify API calls needed to execute operations.

    Accounts for Spotify's 100-track batch limits for add/remove operations.
    Move operations require individual API calls.
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
    matched_tracks: list[Track], operations: list[PlaylistOperation]
) -> float:
    """Calculate confidence score as ratio of matched to total tracks."""
    total_tracks = len(matched_tracks) + len(operations)
    if total_tracks == 0:
        return 1.0

    return len(matched_tracks) / total_tracks


async def calculate_playlist_diff(
    current_playlist: Playlist, target_tracklist: TrackList, uow: UnitOfWorkProtocol
) -> PlaylistDiff:
    """Calculate minimal operations to transform current playlist to match target.

    Main diff calculation function. Matches tracks, generates add/remove/move
    operations, and estimates API costs. Optimized to minimize Spotify API calls
    by reusing existing track mappings from database.

    Args:
        current_playlist: Current state of the playlist.
        target_tracklist: Desired final state of the playlist.
        uow: Database access for track matching optimization.

    Returns:
        PlaylistDiff containing operations and metadata.
    """
    logger.debug(
        f"Calculating diff: {len(current_playlist.tracks)} → {len(target_tracklist.tracks)} tracks"
    )

    # Step 1: Use sophisticated database-first track matching
    (
        matched_tracks,
        unmatched_current,
        unmatched_target,
    ) = await match_tracks_with_db_lookup(
        current_playlist.tracks, target_tracklist.tracks, uow
    )

    # Step 2: Calculate operations using functional composition
    remove_operations: list[PlaylistOperation] = cast(
        "list[PlaylistOperation]",
        calculate_remove_operations(unmatched_current, current_playlist),
    )
    add_operations: list[PlaylistOperation] = cast(
        "list[PlaylistOperation]",
        calculate_add_operations(unmatched_target, target_tracklist),
    )
    move_operations: list[PlaylistOperation] = cast(
        "list[PlaylistOperation]",
        calculate_move_operations(matched_tracks, current_playlist, target_tracklist),
    )

    # Combine all operations
    all_operations = remove_operations + add_operations + move_operations

    # Step 3: Calculate metadata
    api_calls: int = cast("int", estimate_api_calls(all_operations))
    confidence: float = cast(
        "float", calculate_confidence_score(matched_tracks, all_operations)
    )

    return PlaylistDiff(
        operations=all_operations,
        unchanged_tracks=matched_tracks,
        api_call_estimate=api_calls,
        confidence_score=confidence,
    )


# Operation sequencing for Spotify API compatibility
@curry
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

    # Partition operations by type using toolz
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
