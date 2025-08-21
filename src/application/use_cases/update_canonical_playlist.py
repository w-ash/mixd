"""Updates internal playlists by adding, removing, or reordering tracks.

Manages playlist modifications in the local database without syncing to external
music services. Supports both append mode (add tracks to end) and differential
mode (calculate minimal changes to transform current playlist into target state).
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
from src.domain.playlist.execution_strategies import (
    execute_with_strategy,
    get_execution_strategy,
)
from src.domain.repositories import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.metrics import (
    get_all_connectors_metrics,
    get_all_field_mappings,
)

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class UpdateCanonicalPlaylistCommand:
    """Command containing all data needed to update a playlist.

    Args:
        playlist_id: Internal database ID of playlist to modify
        new_tracklist: Target tracks the playlist should contain after update
        dry_run: If True, calculate changes but don't save to database
        append_mode: If True, add tracks to end; if False, replace entire playlist
        playlist_name: New name for playlist (optional)
        playlist_description: New description for playlist (optional)
        metadata: Additional custom metadata to store
        timestamp: When this command was created
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
        """Checks if command has required data for execution.

        Returns:
            True if playlist_id exists and tracklist has tracks or ConnectorPlaylist metadata
        """
        if not self.playlist_id:
            return False

        # Allow empty tracks if ConnectorPlaylist metadata is present for processing
        has_tracks = bool(self.new_tracklist.tracks)
        has_connector_playlist = bool(
            self.new_tracklist.metadata
            and self.new_tracklist.metadata.get("connector_playlist")
        )

        return has_tracks or has_connector_playlist


@define(frozen=True, slots=True)
class UpdateCanonicalPlaylistResult:
    """Results from a playlist update operation.

    Attributes:
        playlist: The updated playlist entity
        operations_performed: Total number of database operations executed
        tracks_added: Number of tracks added to playlist
        tracks_removed: Number of tracks removed from playlist
        tracks_moved: Number of tracks that changed position
        execution_time_ms: How long the operation took in milliseconds
        confidence_score: How confident the diff algorithm was (0.0-1.0)
        errors: List of error messages if any operations failed
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
        """Returns operation statistics as a dictionary."""
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
    """Updates playlists stored in the local database.

    Handles two update modes:
    1. Append mode: Adds new tracks to the end of existing playlist
    2. Differential mode: Calculates minimal changes to transform current
       playlist into target state, preserving as many existing tracks as possible

    Also extracts music metadata from track connector data for analytics.
    """

    metrics_service: MetricsApplicationService = field(
        factory=MetricsApplicationService
    )

    async def execute(
        self, command: UpdateCanonicalPlaylistCommand, uow: UnitOfWorkProtocol
    ) -> UpdateCanonicalPlaylistResult:
        """Updates a playlist with new tracks and optionally new metadata.

        Args:
            command: Contains playlist ID, target tracks, and update options
            uow: Database transaction manager and repository access

        Returns:
            Result containing updated playlist and operation statistics

        Raises:
            ValueError: If command validation fails or playlist ID is invalid
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

                # Step 1.5: Process ConnectorPlaylist data if present
                processed_tracklist = command.new_tracklist
                if (
                    command.new_tracklist.metadata
                    and command.new_tracklist.metadata.get("connector_playlist")
                ):
                    from src.application.services.connector_playlist_processing_service import (
                        ConnectorPlaylistProcessingService,
                    )

                    processing_service = ConnectorPlaylistProcessingService()
                    processed_tracklist = (
                        await processing_service.process_connector_playlist(
                            command.new_tracklist.metadata["connector_playlist"], uow
                        )
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
                        current_playlist, processed_tracklist, uow, command.dry_run
                    )
                    tracks_removed = 0
                    tracks_moved = 0
                    confidence_score = 1.0  # High confidence for simple append

                    # Extract metrics from new tracks (only for non-dry runs)
                    if not command.dry_run:
                        await self._extract_track_metrics(
                            processed_tracklist.tracks, uow
                        )
                else:
                    # Overwrite mode: use diff engine with preservation
                    diff = calculate_playlist_diff(
                        current_playlist, processed_tracklist
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
                        ) = await self._execute_operations(
                            current_playlist, diff, processed_tracklist, uow
                        )
                    confidence_score = diff.confidence_score

                    # Extract metrics from new tracks (only for non-dry runs)
                    if not command.dry_run:
                        await self._extract_track_metrics(
                            processed_tracklist.tracks, uow
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
        """Loads playlist from database by its internal ID.

        Args:
            playlist_id: Internal database ID of playlist
            uow: Database transaction manager

        Returns:
            Current playlist entity

        Raises:
            ValueError: If playlist_id is not a valid integer
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
        target_tracklist: TrackList,
        uow: UnitOfWorkProtocol,
    ) -> tuple[Playlist, int, int, int, int]:
        """Applies calculated add/remove operations to transform playlist.

        Args:
            current_playlist: Playlist before changes
            diff: Calculated operations to apply (adds and removes)
            uow: Database transaction manager

        Returns:
            Tuple of (updated_playlist, operations_performed, tracks_added,
                     tracks_removed, tracks_moved)
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
        tracks_moved = sum(
            1
            for op in diff.operations
            if op.operation_type == PlaylistOperationType.MOVE
        )

        # Use unified execution strategy for canonical playlists
        # This provides consistent behavior with mathematical guarantees from LIS algorithm
        canonical_strategy = get_execution_strategy("canonical")
        updated_tracks, execution_metadata = execute_with_strategy(
            canonical_strategy, current_playlist, target_tracklist, diff
        )

        logger.debug(
            "Applied canonical execution strategy",
            execution_metadata=execution_metadata,
        )

        # Create updated playlist with preserved metadata
        updated_playlist = evolve(
            current_playlist,
            tracks=updated_tracks,
            metadata={
                **current_playlist.metadata,
                "last_updated": datetime.now(UTC).isoformat(),
                "update_operations": len(diff.operations),
                "execution_strategy": execution_metadata,
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
        """Updates playlist name and/or description if provided in command.

        Args:
            current_playlist: Playlist to update
            command: Contains optional new name and description
            uow: Database transaction manager

        Returns:
            Playlist with updated metadata, or unchanged playlist if no updates
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
                connector_playlist_identifiers=current_playlist.connector_playlist_identifiers.copy(),
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
        """Adds new unique tracks to the end of the playlist.

        Filters out tracks that already exist in the playlist to prevent duplicates.

        Args:
            current_playlist: Playlist to append tracks to
            new_tracklist: Tracks to add (duplicates will be filtered out)
            uow: Database transaction manager
            dry_run: If True, calculate result but don't save to database

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
        """Extracts analytics metrics from track metadata for each music service.

        Processes connector metadata (from Spotify, Last.fm, etc.) to extract
        standardized metrics like popularity, energy, and danceability for storage.

        Args:
            tracks: Tracks containing connector metadata to process
            uow: Database transaction manager
        """
        if not tracks:
            return

        # Group tracks by connector to batch process metadata
        for connector, available_metrics in get_all_connectors_metrics().items():
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
                    field_map=get_all_field_mappings(),
                    uow=uow,
                )
