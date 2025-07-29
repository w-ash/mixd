"""UpdateCanonicalPlaylistUseCase for pure internal database playlist updates.

This use case handles updates to canonical (internal) playlists using the DRY
diff engine without external service dependencies. It focuses purely on
database operations following Clean Architecture principles.
"""

from datetime import UTC, datetime
from typing import Any

from attrs import define, evolve, field

from src.application.services.metrics_application_service import (
    MetricsApplicationService,
)
from src.config import get_logger
from src.domain.entities.playlist import Playlist
from src.domain.entities.track import Track, TrackList
from src.domain.playlist import (
    PlaylistDiff,
    PlaylistOperationType,
    calculate_playlist_diff,
)
from src.domain.repositories import UnitOfWorkProtocol
from src.infrastructure.connectors.metrics_config import (
    CONNECTOR_METRICS,
    FIELD_MAPPINGS,
)

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class UpdateCanonicalPlaylistCommand:
    """Command for updating a canonical playlist.

    Encapsulates all information needed to update an internal playlist
    with new tracks and metadata using differential operations.
    """

    playlist_id: str
    new_tracklist: TrackList
    dry_run: bool = False
    append_mode: bool = False  # True=append, False=overwrite with preservation
    playlist_name: str | None = None  # Optional name update
    playlist_description: str | None = None  # Optional description update
    metadata: dict[str, Any] = field(factory=dict)
    timestamp: datetime = field(factory=lambda: datetime.now(UTC))

    def validate(self) -> bool:
        """Validate command business rules.

        Returns:
            True if command is valid for execution
        """
        if not self.playlist_id:
            return False

        return bool(self.new_tracklist.tracks)


@define(frozen=True, slots=True)
class UpdateCanonicalPlaylistResult:
    """Result of canonical playlist update operation.

    Contains the updated playlist, operation statistics, and performance
    metrics for monitoring and debugging purposes.
    """

    playlist: Playlist
    operations_performed: int = 0
    tracks_added: int = 0
    tracks_removed: int = 0
    tracks_moved: int = 0
    execution_time_ms: int = 0
    confidence_score: float = 1.0
    errors: list[str] = field(factory=list)

    @property
    def operation_summary(self) -> dict[str, Any]:
        """Summary of operations performed."""
        return {
            "playlist_id": self.playlist.id,
            "operations_performed": self.operations_performed,
            "added": self.tracks_added,
            "removed": self.tracks_removed,
            "moved": self.tracks_moved,
            "execution_time_ms": self.execution_time_ms,
            "confidence_score": self.confidence_score,
            "success": len(self.errors) == 0,
        }


@define(slots=True)
class UpdateCanonicalPlaylistUseCase:
    """Use case for updating canonical (internal) playlists using DRY diff engine.

    Handles pure database operations for playlist updates following
    Clean Architecture principles with UnitOfWork pattern:
    - Uses DRY diff engine from domain layer
    - No constructor dependencies (pure domain layer)
    - All repository access through UnitOfWork parameter
    - Explicit transaction control in business logic
    - Simplified testing with single UnitOfWork mock
    """

    metrics_service: MetricsApplicationService = field(
        factory=MetricsApplicationService
    )

    async def execute(
        self, command: UpdateCanonicalPlaylistCommand, uow: UnitOfWorkProtocol
    ) -> UpdateCanonicalPlaylistResult:
        """Execute canonical playlist update operation.

        Args:
            command: Command with playlist update context
            uow: UnitOfWork for transaction management and repository access

        Returns:
            Result with updated playlist and operational metadata

        Raises:
            ValueError: If command validation fails
        """
        if not command.validate():
            raise ValueError("Invalid command: failed business rule validation")

        start_time = datetime.now(UTC)

        logger.info(
            "Starting canonical playlist update",
            playlist_id=command.playlist_id,
            track_count=len(command.new_tracklist.tracks),
            dry_run=command.dry_run,
        )

        async with uow:
            try:
                # Step 1: Get current playlist state
                current_playlist = await self._get_current_playlist(
                    command.playlist_id, uow
                )

                # Step 2: Handle metadata updates (name/description)
                if command.playlist_name or command.playlist_description:
                    current_playlist = await self._update_playlist_metadata(
                        current_playlist, command, uow
                    )

                # Step 3: Handle track updates based on mode
                if command.append_mode:
                    # Append mode: add new tracks to end of existing playlist
                    (
                        result_playlist,
                        operations_performed,
                        tracks_added,
                    ) = await self._append_tracks(
                        current_playlist, command.new_tracklist, uow, command.dry_run
                    )
                    tracks_removed = 0
                    tracks_moved = 0
                    confidence_score = 1.0  # High confidence for simple append

                    # Extract metrics from new tracks (only for non-dry runs)
                    if not command.dry_run:
                        await self._extract_track_metrics(
                            command.new_tracklist.tracks, uow
                        )
                else:
                    # Overwrite mode: use DRY diff engine with preservation
                    diff = await calculate_playlist_diff(
                        current_playlist, command.new_tracklist, uow
                    )

                    if not diff.has_changes:
                        logger.info("No changes detected, playlist already up to date")
                        return UpdateCanonicalPlaylistResult(
                            playlist=current_playlist,
                            execution_time_ms=int(
                                (datetime.now(UTC) - start_time).total_seconds() * 1000
                            ),
                            confidence_score=diff.confidence_score,
                        )

                    # Execute differential operations
                    result_playlist = current_playlist
                    operations_performed = 0
                    tracks_added = 0
                    tracks_removed = 0
                    tracks_moved = 0

                    if not command.dry_run:
                        (
                            result_playlist,
                            operations_performed,
                            tracks_added,
                            tracks_removed,
                            tracks_moved,
                        ) = await self._execute_operations(current_playlist, diff, uow)
                    confidence_score = diff.confidence_score

                    # Extract metrics from new tracks (only for non-dry runs)
                    if not command.dry_run:
                        await self._extract_track_metrics(
                            command.new_tracklist.tracks, uow
                        )

                # Commit changes if not dry run
                if not command.dry_run:
                    await uow.commit()

                # Step 4: Calculate execution metrics
                execution_time = int(
                    (datetime.now(UTC) - start_time).total_seconds() * 1000
                )

                result = UpdateCanonicalPlaylistResult(
                    playlist=result_playlist,
                    operations_performed=operations_performed,
                    tracks_added=tracks_added,
                    tracks_removed=tracks_removed,
                    tracks_moved=tracks_moved,
                    execution_time_ms=execution_time,
                    confidence_score=confidence_score,
                )

                logger.info(
                    "Canonical playlist update completed",
                    playlist_id=command.playlist_id,
                    operations_performed=operations_performed,
                    execution_time_ms=execution_time,
                    dry_run=command.dry_run,
                )

                return result

            except Exception as e:
                # Explicit rollback on business logic failure
                await uow.rollback()
                logger.error(
                    "Canonical playlist update failed",
                    error=str(e),
                    playlist_id=command.playlist_id,
                )
                raise

    async def _get_current_playlist(
        self, playlist_id: str, uow: UnitOfWorkProtocol
    ) -> Playlist:
        """Retrieve current playlist state from database.

        For canonical use case, playlist_id is always a canonical/internal ID.

        Args:
            playlist_id: Internal playlist ID
            uow: UnitOfWork for repository access

        Returns:
            Current playlist entity
        """
        playlist_repo = uow.get_playlist_repository()

        try:
            playlist = await playlist_repo.get_playlist_by_id(int(playlist_id))
            return playlist
        except ValueError:
            raise ValueError(
                f"Invalid playlist ID '{playlist_id}' - must be a canonical playlist ID"
            ) from None

    async def _execute_operations(
        self,
        current_playlist: Playlist,
        diff: PlaylistDiff,
        uow: UnitOfWorkProtocol,
    ) -> tuple[Playlist, int, int, int, int]:
        """Execute the differential operations on the playlist.

        Args:
            current_playlist: Current playlist state
            diff: Calculated operations to perform
            uow: UnitOfWork for repository access

        Returns:
            Tuple of (updated_playlist, operations_performed, tracks_added, tracks_removed, tracks_moved)
        """
        logger.debug(f"Executing {len(diff.operations)} operations")

        # Start with current tracks
        updated_tracks = current_playlist.tracks.copy()

        # Count operations by type
        tracks_added = sum(
            1
            for op in diff.operations
            if op.operation_type == PlaylistOperationType.ADD
        )
        tracks_removed = sum(
            1
            for op in diff.operations
            if op.operation_type == PlaylistOperationType.REMOVE
        )
        # MOVE operations not needed for canonical - track order is handled by ADD positions
        tracks_moved = 0

        # Apply operations to create the updated track list

        # Process REMOVE operations (in reverse order to avoid index shifting)
        remove_ops = [
            op
            for op in diff.operations
            if op.operation_type == PlaylistOperationType.REMOVE
        ]
        for op in sorted(remove_ops, key=lambda x: x.position, reverse=True):
            if 0 <= op.position < len(updated_tracks):
                updated_tracks.pop(op.position)

        # Process ADD operations
        add_ops = [
            op
            for op in diff.operations
            if op.operation_type == PlaylistOperationType.ADD
        ]
        for op in add_ops:
            position = min(op.position, len(updated_tracks))
            updated_tracks.insert(position, op.track)

        # Create updated playlist with preserved metadata
        updated_playlist = evolve(
            current_playlist,
            tracks=updated_tracks,
            metadata={
                **current_playlist.metadata,
                "last_updated": datetime.now(UTC).isoformat(),
                "update_operations": len(diff.operations),
            },
        )

        # Persist updated playlist using update_playlist for existing playlists
        if current_playlist.id is None:
            raise ValueError("Cannot update playlist without an ID")
        playlist_repo = uow.get_playlist_repository()
        saved_playlist = await playlist_repo.update_playlist(
            current_playlist.id, updated_playlist
        )

        return (
            saved_playlist,
            len(diff.operations),
            tracks_added,
            tracks_removed,
            tracks_moved,
        )

    async def _update_playlist_metadata(
        self,
        current_playlist: Playlist,
        command: UpdateCanonicalPlaylistCommand,
        uow: UnitOfWorkProtocol,
    ) -> Playlist:
        """Update playlist metadata (name/description).

        Args:
            current_playlist: Current playlist state
            command: Command with metadata updates
            uow: UnitOfWork for repository access

        Returns:
            Updated playlist with new metadata
        """
        updates = {}
        if command.playlist_name:
            updates["name"] = command.playlist_name
        if command.playlist_description:
            updates["description"] = command.playlist_description

        if updates:
            logger.info(
                "Updating playlist metadata",
                playlist_id=current_playlist.id,
                updates=updates,
            )
            # Preserve connector mappings when updating metadata
            updated_playlist = evolve(
                current_playlist,
                connector_playlist_ids=current_playlist.connector_playlist_ids.copy(),
                **updates,
            )

            # Persist metadata changes using update_playlist for existing playlists
            if current_playlist.id is None:
                raise ValueError("Cannot update playlist without an ID")
            playlist_repo = uow.get_playlist_repository()
            return await playlist_repo.update_playlist(
                current_playlist.id, updated_playlist
            )

        return current_playlist

    async def _append_tracks(
        self,
        current_playlist: Playlist,
        new_tracklist: TrackList,
        uow: UnitOfWorkProtocol,
        dry_run: bool,
    ) -> tuple[Playlist, int, int]:
        """Append new tracks to the end of existing playlist.

        Args:
            current_playlist: Current playlist state
            new_tracklist: Tracks to append
            uow: UnitOfWork for repository access
            dry_run: Whether to actually persist changes

        Returns:
            Tuple of (updated_playlist, operations_performed, tracks_added)
        """
        # Filter out tracks that already exist to avoid duplicates
        existing_track_ids = {track.id for track in current_playlist.tracks if track.id}
        new_tracks = [
            track
            for track in new_tracklist.tracks
            if not track.id or track.id not in existing_track_ids
        ]

        if not new_tracks:
            logger.info("No new tracks to append")
            return current_playlist, 0, 0

        logger.info(f"Appending {len(new_tracks)} new tracks to playlist")

        if dry_run:
            # For dry run, create result without persisting
            result_playlist = evolve(
                current_playlist, tracks=current_playlist.tracks + new_tracks
            )
            return result_playlist, len(new_tracks), len(new_tracks)

        # Create updated playlist with appended tracks
        updated_playlist = evolve(
            current_playlist,
            tracks=current_playlist.tracks + new_tracks,
            metadata={
                **current_playlist.metadata,
                "last_updated": datetime.now(UTC).isoformat(),
                "tracks_appended": len(new_tracks),
            },
        )

        # Persist updated playlist using update_playlist for existing playlists
        if current_playlist.id is None:
            raise ValueError("Cannot update playlist without an ID")
        playlist_repo = uow.get_playlist_repository()
        saved_playlist = await playlist_repo.update_playlist(
            current_playlist.id, updated_playlist
        )

        return saved_playlist, len(new_tracks), len(new_tracks)

    async def _extract_track_metrics(
        self, tracks: list["Track"], uow: UnitOfWorkProtocol
    ) -> None:
        """Extract metrics from connector metadata for all tracks.

        Args:
            tracks: List of tracks to extract metrics for
            uow: UnitOfWork for transaction management
        """
        if not tracks:
            return

        # Group tracks by connector to batch process metadata
        for connector, available_metrics in CONNECTOR_METRICS.items():
            # Find tracks that have metadata for this connector
            tracks_with_metadata = []
            fresh_metadata = {}

            for track in tracks:
                if (
                    track.id
                    and track.connector_metadata
                    and connector in track.connector_metadata
                ):
                    tracks_with_metadata.append(track)
                    fresh_metadata[track.id] = track.connector_metadata[connector]

            if fresh_metadata:
                logger.info(
                    f"Extracting {len(available_metrics)} metrics from {connector} for {len(tracks_with_metadata)} tracks",
                    connector=connector,
                    metrics=available_metrics,
                    track_count=len(tracks_with_metadata),
                )

                # Use the metrics service to batch process the fresh metadata
                await self.metrics_service.batch_process_fresh_metadata(
                    fresh_metadata=fresh_metadata,
                    connector=connector,
                    available_metrics=available_metrics,
                    field_map=FIELD_MAPPINGS,
                    uow=uow,
                )
