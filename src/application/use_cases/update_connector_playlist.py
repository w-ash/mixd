"""Synchronizes Spotify/Apple Music playlists with local track collections.

Calculates minimal track changes (add/remove/move) to update external service
playlists while preserving track timestamps and minimizing API calls.
"""

from datetime import UTC, datetime
from typing import Any

from attrs import define, field

from src.config import get_logger, settings
from src.domain.entities import ConnectorPlaylist
from src.domain.entities.playlist import ConnectorPlaylistItem, Playlist
from src.domain.entities.track import TrackList
from src.domain.playlist import (
    PlaylistOperationType,
    calculate_playlist_diff,
)
from src.domain.playlist.execution_strategies import get_execution_strategy
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class UpdateConnectorPlaylistCommand:
    """Input parameters for updating a Spotify/Apple Music playlist.

    Contains the target playlist ID, desired track list, and update options
    like append-only mode vs full synchronization.
    """

    playlist_id: str
    new_tracklist: TrackList
    connector: str  # "spotify", "apple_music", etc.
    dry_run: bool = False
    append_mode: bool = False  # True=append, False=overwrite with preservation
    playlist_name: str | None = None  # Optional name update
    playlist_description: str | None = None  # Optional description update
    preserve_timestamps: bool = True  # Whether to use proper sequencing
    batch_size: int = 100  # API batch size limit
    max_api_calls: int = 50  # Maximum API calls allowed
    metadata: dict[str, Any] = field(factory=dict)
    timestamp: datetime = field(factory=lambda: datetime.now(UTC))

    def validate(self) -> bool:
        """Checks if playlist ID, tracks, and API limits are valid.

        Returns:
            True if command can be executed safely.
        """
        if not self.playlist_id:
            return False

        if not self.new_tracklist.tracks:
            return False

        if not self.connector:
            return False

        if self.batch_size > settings.api.spotify_large_batch_size:  # Spotify API limit
            return False

        return not self.max_api_calls < 1


@define(frozen=True, slots=True)
class UpdateConnectorPlaylistResult:
    """Playlist update outcome with operation counts and timing metrics.

    Reports how many tracks were added/removed/moved, API calls made,
    and execution time for monitoring playlist sync performance.
    """

    playlist_id: str
    connector: str
    operations_performed: int = 0
    api_calls_made: int = 0
    tracks_added: int = 0
    tracks_removed: int = 0
    tracks_moved: int = 0
    execution_time_ms: int = 0
    confidence_score: float = 1.0
    external_metadata: dict[str, Any] = field(factory=dict)  # e.g., Spotify snapshot_id
    errors: list[str] = field(factory=list)

    @property
    def operation_summary(self) -> dict[str, Any]:
        """Dictionary of operation counts and success status for logging."""
        return {
            "playlist_id": self.playlist_id,
            "connector": self.connector,
            "operations_performed": self.operations_performed,
            "api_calls_made": self.api_calls_made,
            "added": self.tracks_added,
            "removed": self.tracks_removed,
            "moved": self.tracks_moved,
            "execution_time_ms": self.execution_time_ms,
            "confidence_score": self.confidence_score,
            "success": len(self.errors) == 0,
        }


@define(slots=True)
class UpdateConnectorPlaylistUseCase:
    """Synchronizes external music service playlists with local track data.

    Calculates minimal changes (remove→add→move operations) to update Spotify/Apple
    Music playlists efficiently. Preserves track timestamps and handles API batching.
    Updates local database after successful external API calls.
    """

    def _count_operation_types(self, operations: list) -> tuple[int, int, int]:
        """Count add/remove/move operations. Returns (added, removed, moved)."""
        tracks_added = sum(
            1 for op in operations if op.operation_type == PlaylistOperationType.ADD
        )
        tracks_removed = sum(
            1 for op in operations if op.operation_type == PlaylistOperationType.REMOVE
        )
        tracks_moved = sum(
            1 for op in operations if op.operation_type == PlaylistOperationType.MOVE
        )
        return tracks_added, tracks_removed, tracks_moved

    async def execute(
        self, command: UpdateConnectorPlaylistCommand, uow: UnitOfWorkProtocol
    ) -> UpdateConnectorPlaylistResult:
        """Updates Spotify/Apple Music playlist with new track collection.

        Calculates minimal changes between current and desired playlists,
        executes API operations in optimal order, and updates local database.

        Args:
            command: Playlist update parameters and options.
            uow: Database transaction manager and repository access.

        Returns:
            Operation results with counts, timing, and success status.

        Raises:
            ValueError: If command validation fails.
        """
        if not command.validate():
            raise ValueError("Invalid command: failed business rule validation")

        start_time = datetime.now(UTC)

        logger.info(
            "Starting connector playlist update",
            playlist_id=command.playlist_id,
            connector=command.connector,
            track_count=len(command.new_tracklist.tracks),
            dry_run=command.dry_run,
        )

        async with uow:
            try:
                # Step 1: Get current playlist state (from internal database)
                current_playlist = await self._get_current_playlist(
                    command.playlist_id, command.connector, uow
                )

                # Step 2: Handle track updates based on mode
                if command.append_mode:
                    # Append mode: add new tracks to end of external playlist
                    (
                        api_calls_made,
                        external_metadata,
                        operations_performed,
                        tracks_added,
                    ) = await self._append_tracks_to_connector(
                        current_playlist, command, uow
                    )
                    tracks_removed = 0
                    tracks_moved = 0
                    confidence_score = 1.0  # High confidence for simple append
                else:
                    # Overwrite mode: use diff engine with preservation
                    diff = calculate_playlist_diff(
                        current_playlist, command.new_tracklist
                    )

                    if not diff.has_changes:
                        logger.info(
                            "No changes detected, connector playlist already up to date"
                        )
                        return UpdateConnectorPlaylistResult(
                            playlist_id=command.playlist_id,
                            connector=command.connector,
                            execution_time_ms=int(
                                (datetime.now(UTC) - start_time).total_seconds() * 1000
                            ),
                            confidence_score=diff.confidence_score,
                        )

                    # Step 3: Use unified execution strategy for API operations
                    api_strategy = get_execution_strategy("api")
                    execution_plan = api_strategy.plan_operations(diff)
                    sequenced_operations = execution_plan.operations

                    tracks_added, tracks_removed, tracks_moved = (
                        self._count_operation_types(sequenced_operations)
                    )
                    logger.debug(
                        f"Planned {len(sequenced_operations)} operations for {command.connector}",
                        remove_ops=tracks_removed,
                        add_ops=tracks_added,
                        move_ops=tracks_moved,
                        execution_metadata=execution_plan.execution_metadata,
                    )

                    # Step 4: Execute operations against external service (if not dry run)
                    api_calls_made = 0
                    external_metadata = {}
                    operations_performed = 0
                    tracks_added = 0
                    tracks_removed = 0
                    tracks_moved = 0

                    if not command.dry_run:
                        (
                            api_calls_made,
                            external_metadata,
                            operations_performed,
                            tracks_added,
                            tracks_removed,
                            tracks_moved,
                        ) = await self._execute_external_operations(
                            current_playlist, sequenced_operations, command, uow
                        )
                    confidence_score = diff.confidence_score

                # Handle metadata updates if specified
                if command.playlist_name or command.playlist_description:
                    await self._update_connector_playlist_metadata(
                        command.playlist_id, command, uow
                    )

                # Step 5: Calculate execution metrics
                execution_time = int(
                    (datetime.now(UTC) - start_time).total_seconds() * 1000
                )

                result = UpdateConnectorPlaylistResult(
                    playlist_id=command.playlist_id,
                    connector=command.connector,
                    operations_performed=operations_performed,
                    api_calls_made=api_calls_made,
                    tracks_added=tracks_added,
                    tracks_removed=tracks_removed,
                    tracks_moved=tracks_moved,
                    execution_time_ms=execution_time,
                    confidence_score=confidence_score,
                    external_metadata=external_metadata,
                )

                logger.info(
                    "Connector playlist update completed",
                    playlist_id=command.playlist_id,
                    connector=command.connector,
                    operations_performed=operations_performed,
                    api_calls_made=api_calls_made,
                    execution_time_ms=execution_time,
                    dry_run=command.dry_run,
                )

                return result

            except Exception as e:
                logger.error(
                    "Connector playlist update failed",
                    error=str(e),
                    playlist_id=command.playlist_id,
                    connector=command.connector,
                )
                raise

    async def _get_current_playlist(
        self, playlist_id: str, connector: str, uow: UnitOfWorkProtocol
    ) -> Playlist:
        """Retrieves playlist from local database using external service ID.

        Resolves Spotify/Apple Music playlist ID to internal playlist record.
        Auto-creates local playlist if it doesn't exist.

        Args:
            playlist_id: External service playlist ID.
            connector: Service name ("spotify", "apple_music").
            uow: Database access manager.

        Returns:
            Local playlist entity with current track list.
        """
        playlist_repo = uow.get_playlist_repository()

        # Resolve connector ID to canonical playlist
        playlist = await playlist_repo.get_playlist_by_connector(
            connector, playlist_id, raise_if_not_found=False
        )

        if playlist is None:
            # Auto-create canonical playlist if it doesn't exist
            logger.info(
                f"Creating canonical playlist for {connector} playlist {playlist_id}"
            )
            playlist = await self._create_canonical_for_connector_playlist(
                playlist_id, connector, uow
            )

        return playlist

    async def _execute_external_operations(
        self,
        current_playlist: Playlist,
        sequenced_operations: list,
        command: UpdateConnectorPlaylistCommand,
        uow: UnitOfWorkProtocol,
    ) -> tuple[int, dict[str, Any], int, int, int, int]:
        """Executes playlist changes on external service then updates local database.

        Applies remove/add/move operations to Spotify/Apple Music playlist via API,
        then immediately updates local database with the new state for consistency.

        Args:
            current_playlist: Current playlist state before changes.
            sequenced_operations: Ordered remove→add→move operations to apply.
            command: Update parameters and configuration.
            uow: Database access manager.

        Returns:
            Tuple of (api_calls_made, external_metadata, operations_performed,
                     tracks_added, tracks_removed, tracks_moved).
        """
        logger.debug(
            f"Executing {len(sequenced_operations)} operations against {command.connector}"
        )

        # Count operations by type
        tracks_added, tracks_removed, tracks_moved = self._count_operation_types(
            sequenced_operations
        )

        # Step 1: Execute operations against external API
        api_response = await self._execute_connector_api_operations(
            current_playlist, sequenced_operations, command, uow
        )

        # Step 2: Optimistic database update if API succeeded
        if api_response["success"]:
            await self._update_connector_playlist_optimistic(
                current_playlist=current_playlist,
                applied_operations=sequenced_operations,
                api_metadata=api_response["metadata"],
                command=command,
                uow=uow,
            )

        logger.info(
            "External operations completed with connector_playlist table update",
            connector=command.connector,
            operations=len(sequenced_operations),
            api_calls=api_response["api_calls_made"],
            add_ops=tracks_added,
            remove_ops=tracks_removed,
            move_ops=tracks_moved,
            success=api_response["success"],
        )

        return (
            api_response["api_calls_made"],
            api_response["metadata"],
            len(sequenced_operations) if api_response["success"] else 0,
            tracks_added if api_response["success"] else 0,
            tracks_removed if api_response["success"] else 0,
            tracks_moved if api_response["success"] else 0,
        )

    async def _execute_connector_api_operations(
        self,
        current_playlist: Playlist,
        sequenced_operations: list,
        command: UpdateConnectorPlaylistCommand,
        uow: UnitOfWorkProtocol,
    ) -> dict[str, Any]:
        """Executes playlist operations via connector, trusting its implementation."""
        try:
            # Get connector and execute operations (connector handles batching, rate limits, etc.)
            connector_provider = uow.get_service_connector_provider()
            connector = connector_provider.get_connector(command.connector)

            tracks_added, tracks_removed, tracks_moved = self._count_operation_types(
                sequenced_operations
            )
            logger.info(
                "Executing differential operations on external playlist",
                connector=command.connector,
                playlist_id=command.playlist_id,
                operations_count=len(sequenced_operations),
                remove_ops=tracks_removed,
                add_ops=tracks_added,
                move_ops=tracks_moved,
            )

            # Trust connector's sophisticated implementation (it handles all API details correctly)
            # Pass track repository for canonical URI resolution
            track_repo = uow.get_track_repository()
            final_snapshot_id = await connector.execute_playlist_operations(
                command.playlist_id, sequenced_operations, track_repo=track_repo
            )

            # Build simple response metadata (let repository handle detailed metadata)
            external_metadata = {
                "last_modified": datetime.now(UTC).isoformat(),
                "operations_applied": len(sequenced_operations),
                "snapshot_id": final_snapshot_id,
            }

            return {
                "success": True,
                "api_calls_made": len(sequenced_operations),  # Estimate
                "metadata": external_metadata,
                "error": None,
            }

        except Exception as e:
            logger.error(
                "External playlist update failed",
                connector=command.connector,
                playlist_id=command.playlist_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return {
                "success": False,
                "api_calls_made": 0,
                "metadata": {},
                "error": str(e),
            }

    async def _update_connector_playlist_optimistic(
        self,
        current_playlist: Playlist,
        applied_operations: list,
        api_metadata: dict[str, Any],
        command: UpdateConnectorPlaylistCommand,
        uow: UnitOfWorkProtocol,
    ) -> None:
        """Updates local database after successful external API calls."""
        try:
            connector_repo = uow.get_connector_playlist_repository()

            # Create playlist items from final desired state
            updated_items = self._create_playlist_items_from_tracklist(command)

            # Get existing connector playlist for ID continuity
            existing = await connector_repo.get_by_connector_id(
                command.connector, command.playlist_id
            )

            # Let repository handle ConnectorPlaylist construction and persistence
            updated_connector_playlist = ConnectorPlaylist(
                id=existing.id if existing else None,
                connector_name=command.connector,
                connector_playlist_identifier=command.playlist_id,
                name=current_playlist.name,
                description=current_playlist.description,
                items=updated_items,
                raw_metadata=api_metadata,
                last_updated=datetime.now(UTC),
            )

            await connector_repo.upsert_model(updated_connector_playlist)

            logger.debug(
                "Connector playlist table updated optimistically",
                connector=command.connector,
                playlist_id=command.playlist_id,
                operations_applied=len(applied_operations),
                snapshot_id=api_metadata.get("snapshot_id"),
                items_count=len(updated_items),
            )

        except Exception as e:
            logger.warning(
                "Failed to update connector_playlist table after successful API call",
                connector=command.connector,
                playlist_id=command.playlist_id,
                error=str(e),
            )

    def _create_playlist_items_from_tracklist(
        self, command: UpdateConnectorPlaylistCommand
    ) -> list[ConnectorPlaylistItem]:
        """Creates playlist items from target track list."""
        items = []
        for i, track in enumerate(command.new_tracklist.tracks):
            if (
                track.connector_track_identifiers
                and track.connector_track_identifiers.get(command.connector)
            ):
                connector_track_id = track.connector_track_identifiers[
                    command.connector
                ]
                item = ConnectorPlaylistItem(
                    connector_track_identifier=connector_track_id,
                    position=i,
                    added_at=datetime.now(UTC).isoformat(),
                    added_by_id="narada",
                    extras={
                        "track_uri": f"{command.connector}:track:{connector_track_id}",
                        "local": False,
                    },
                )
                items.append(item)
        return items

    async def _append_tracks_to_connector(
        self,
        current_playlist: Playlist,
        command: UpdateConnectorPlaylistCommand,
        uow: UnitOfWorkProtocol,
    ) -> tuple[int, dict, int, int]:
        """Adds new tracks to end of external playlist without removing existing ones.

        Filters out duplicate tracks and appends only new ones to preserve
        existing playlist content and user-added tracks.

        Args:
            current_playlist: Current playlist state.
            command: Update command with new tracks and configuration.
            uow: Database access manager.

        Returns:
            Tuple of (api_calls_made, external_metadata, operations_performed, tracks_added).
        """
        # Filter out tracks that already exist to avoid duplicates
        existing_track_ids = {track.id for track in current_playlist.tracks if track.id}
        new_tracks = [
            track
            for track in command.new_tracklist.tracks
            if not track.id or track.id not in existing_track_ids
        ]

        if not new_tracks:
            logger.info("No new tracks to append to connector playlist")
            return 0, {}, 0, 0

        logger.info(
            f"Appending {len(new_tracks)} new tracks to {command.connector} playlist"
        )

        if command.dry_run:
            # For dry run, estimate API calls without executing
            estimated_api_calls = (
                len(new_tracks) + 99
            ) // 100  # Ceiling division for batches
            return estimated_api_calls, {}, len(new_tracks), len(new_tracks)

        # Execute append operation via connector
        connector_provider = uow.get_service_connector_provider()
        connector_instance = connector_provider.get_connector(command.connector)

        # Append tracks to external playlist
        api_metadata = await connector_instance.append_tracks_to_playlist(
            command.playlist_id, new_tracks
        )

        # Update connector_playlist table optimistically
        await self._update_connector_playlist_optimistic(
            current_playlist, [], api_metadata, command, uow
        )

        # Estimate API calls made (typically one API call for batch append)
        api_calls_made = 1

        return api_calls_made, api_metadata, len(new_tracks), len(new_tracks)

    async def _update_connector_playlist_metadata(
        self,
        playlist_id: str,
        command: UpdateConnectorPlaylistCommand,
        uow: UnitOfWorkProtocol,
    ) -> None:
        """Updates playlist name and description on external service.

        Args:
            playlist_id: External playlist ID.
            command: Command with metadata updates.
            uow: Database access manager.
        """
        if command.dry_run:
            logger.info("Skipping metadata update in dry run mode")
            return

        connector_provider = uow.get_service_connector_provider()
        connector_instance = connector_provider.get_connector(command.connector)

        metadata_updates = {}
        if command.playlist_name:
            metadata_updates["name"] = command.playlist_name
        if command.playlist_description:
            metadata_updates["description"] = command.playlist_description

        if metadata_updates:
            logger.info(
                f"Updating {command.connector} playlist metadata",
                playlist_id=playlist_id,
                updates=metadata_updates,
            )
            await connector_instance.update_playlist_metadata(
                playlist_id, metadata_updates
            )

    async def _create_canonical_for_connector_playlist(
        self,
        connector_playlist_id: str,
        connector: str,
        uow: UnitOfWorkProtocol,
    ) -> Playlist:
        """Creates local playlist record for external service playlist.

        Fetches playlist metadata from Spotify/Apple Music and creates
        corresponding local database record for tracking and synchronization.

        Args:
            connector_playlist_id: External service playlist ID.
            connector: Service name ("spotify", "apple_music").
            uow: Database access manager.

        Returns:
            Newly created local playlist entity.
        """
        # Get connector instance to fetch playlist details
        connector_provider = uow.get_service_connector_provider()
        connector_instance = connector_provider.get_connector(connector)

        # Fetch playlist metadata from external service
        connector_playlist_info = await connector_instance.get_playlist_details(
            connector_playlist_id
        )

        # Create canonical playlist with fetched info
        canonical_playlist = Playlist(
            name=connector_playlist_info.get("name", f"{connector.title()} Playlist"),
            description=connector_playlist_info.get(
                "description", f"Imported from {connector.title()}"
            ),
            tracks=[],  # Will be populated separately if needed
            connector_playlist_identifiers={connector: connector_playlist_id},
            metadata={
                "created_from_connector": connector,
                "original_connector_id": connector_playlist_id,
                "auto_created": True,
            },
        )

        # Save to database
        playlist_repo = uow.get_playlist_repository()
        saved_playlist = await playlist_repo.save_playlist(canonical_playlist)

        logger.info(
            f"Auto-created canonical playlist {saved_playlist.id} for {connector} playlist {connector_playlist_id}"
        )

        return saved_playlist
