"""UpdateConnectorPlaylistUseCase for external service playlist updates.

This use case handles updates to external service playlists (like Spotify) using
the DRY diff engine with proper operation sequencing to preserve track addition
timestamps and minimize API calls.
"""

from datetime import UTC, datetime
from typing import Any, cast

from attrs import define, field

from src.config import get_logger
from src.domain.entities import ConnectorPlaylist
from src.domain.entities.playlist import ConnectorPlaylistItem, Playlist
from src.domain.entities.track import TrackList
from src.domain.playlist import (
    PlaylistOperation,
    PlaylistOperationType,
    calculate_playlist_diff,
    sequence_operations_for_spotify,
)
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class UpdateConnectorPlaylistCommand:
    """Command for updating a connector playlist.
    
    Encapsulates all information needed to update an external service playlist
    with new tracks using differential operations and proper sequencing.
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
        """Validate command business rules.

        Returns:
            True if command is valid for execution
        """
        if not self.playlist_id:
            return False

        if not self.new_tracklist.tracks:
            return False

        if not self.connector:
            return False

        if self.batch_size > 100:  # Spotify API limit
            return False

        return not self.max_api_calls < 1


@define(frozen=True, slots=True)
class UpdateConnectorPlaylistResult:
    """Result of connector playlist update operation.

    Contains the update status, operation statistics, and performance
    metrics for monitoring and debugging purposes.
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
        """Summary of operations performed."""
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
    """Use case for updating external service playlists using DRY diff engine.

    Handles external service API operations with proper sequencing following
    Clean Architecture principles:
    - Uses DRY diff engine from domain layer
    - Applies proper operation sequencing (remove→add→move) to preserve timestamps
    - Handles external service API calls and batching
    - No direct database modifications (canonical operations handle that)
    """

    async def execute(
        self, command: UpdateConnectorPlaylistCommand, uow: UnitOfWorkProtocol
    ) -> UpdateConnectorPlaylistResult:
        """Execute connector playlist update operation.

        Args:
            command: Command with playlist update context
            uow: UnitOfWork for transaction management and repository access

        Returns:
            Result with update status and operational metadata

        Raises:
            ValueError: If command validation fails
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
                    api_calls_made, external_metadata, operations_performed, tracks_added = await self._append_tracks_to_connector(
                        current_playlist, command, uow
                    )
                    tracks_removed = 0
                    tracks_moved = 0
                    confidence_score = 1.0  # High confidence for simple append
                else:
                    # Overwrite mode: use DRY diff engine with preservation
                    diff = await calculate_playlist_diff(
                        current_playlist, command.new_tracklist, uow
                    )

                    if not diff.has_changes:
                        logger.info("No changes detected, connector playlist already up to date")
                        return UpdateConnectorPlaylistResult(
                            playlist_id=command.playlist_id,
                            connector=command.connector,
                            execution_time_ms=int(
                                (datetime.now(UTC) - start_time).total_seconds() * 1000
                            ),
                            confidence_score=diff.confidence_score,
                        )

                    # Step 3: Apply proper operation sequencing for external service
                    sequenced_operations: list[PlaylistOperation] = cast(
                        "list[PlaylistOperation]", 
                        sequence_operations_for_spotify(diff.operations)
                    )
                    
                    logger.debug(
                        f"Sequenced {len(sequenced_operations)} operations for {command.connector}",
                        remove_ops=sum(1 for op in sequenced_operations if op.operation_type == PlaylistOperationType.REMOVE),
                        add_ops=sum(1 for op in sequenced_operations if op.operation_type == PlaylistOperationType.ADD),
                        move_ops=sum(1 for op in sequenced_operations if op.operation_type == PlaylistOperationType.MOVE),
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
        """Retrieve current playlist state from internal database.

        For connector use cases, playlist_id is always the connector ID,
        so we resolve it to the canonical playlist.

        Args:
            playlist_id: Connector playlist ID to resolve
            connector: Connector name (spotify, apple_music, etc.)
            uow: UnitOfWork for repository access

        Returns:
            Current canonical playlist entity
        """
        playlist_repo = uow.get_playlist_repository()

        # Resolve connector ID to canonical playlist
        playlist = await playlist_repo.get_playlist_by_connector(
            connector, playlist_id, raise_if_not_found=False
        )
        
        if playlist is None:
            # Auto-create canonical playlist if it doesn't exist
            logger.info(f"Creating canonical playlist for {connector} playlist {playlist_id}")
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
        """Execute operations against external service API with optimistic connector_playlist updates.

        This method implements the 2025 best practice of optimistic updates:
        1. Execute external API operations with proper sequencing
        2. If successful, immediately update connector_playlist table based on API response
        3. Use API response metadata for version tracking and drift detection

        Args:
            current_playlist: Current playlist state
            sequenced_operations: Operations in proper sequence (remove→add→move)
            command: Update command with configuration
            uow: UnitOfWork for repository access

        Returns:
            Tuple of (api_calls_made, external_metadata, operations_performed, 
                     tracks_added, tracks_removed, tracks_moved)
        """
        logger.debug(f"Executing {len(sequenced_operations)} operations against {command.connector}")

        # Count operations by type
        tracks_added = sum(
            1 for op in sequenced_operations if op.operation_type == PlaylistOperationType.ADD
        )
        tracks_removed = sum(
            1 for op in sequenced_operations if op.operation_type == PlaylistOperationType.REMOVE
        )
        tracks_moved = sum(
            1 for op in sequenced_operations if op.operation_type == PlaylistOperationType.MOVE
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
        """Execute operations against external service API using sophisticated differential operations.

        Uses the existing sophisticated diff engine to apply REMOVE → ADD → MOVE operations
        that preserve Spotify track added_at timestamps and maintain playlist continuity.
        
        Args:
            current_playlist: Current playlist state
            sequenced_operations: REMOVE → ADD → MOVE operations from diff engine
            command: Update command with configuration
            uow: UnitOfWork for accessing connector provider

        Returns:
            Dict with success status, metadata, and API call count
        """
        try:
            # Get appropriate connector service (Spotify, Apple Music, etc.)
            connector_provider = uow.get_service_connector_provider()
            connector = connector_provider.get_connector(command.connector)
            
            logger.info(
                "Executing differential operations on external playlist",
                connector=command.connector,
                playlist_id=command.playlist_id,
                operations_count=len(sequenced_operations),
                remove_ops=sum(1 for op in sequenced_operations if op.operation_type == PlaylistOperationType.REMOVE),
                add_ops=sum(1 for op in sequenced_operations if op.operation_type == PlaylistOperationType.ADD),
                move_ops=sum(1 for op in sequenced_operations if op.operation_type == PlaylistOperationType.MOVE),
            )
            
            # Execute sophisticated differential operations to preserve added_at timestamps
            # This uses the existing sophisticated diff engine that maintains track continuity
            final_snapshot_id = await connector.execute_playlist_operations(
                command.playlist_id, sequenced_operations
            )
            
            # Build response metadata
            external_metadata = {
                "last_modified": datetime.now(UTC).isoformat(),
                "operations_applied": len(sequenced_operations),
                "tracks_count": len(command.new_tracklist.tracks),
                "snapshot_id": final_snapshot_id,  # Actual snapshot from differential operations
            }
            
            # Add connector-specific metadata if available
            if hasattr(connector, 'get_playlist_metadata'):
                try:
                    connector_metadata = await connector.get_playlist_metadata(command.playlist_id)
                    external_metadata.update(connector_metadata)
                except Exception as metadata_error:
                    logger.warning(
                        "Failed to retrieve updated connector metadata",
                        connector=command.connector,
                        playlist_id=command.playlist_id,
                        error=str(metadata_error),
                    )

            logger.info(
                "Differential operations executed successfully",
                connector=command.connector,
                playlist_id=command.playlist_id,
                final_snapshot_id=final_snapshot_id,
                operations_count=len(sequenced_operations),
            )

            return {
                "success": True,
                "api_calls_made": len(sequenced_operations),  # One call per operation type group
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
        """Update connector_playlist table optimistically based on successful API response.

        This implements the optimistic update pattern where we immediately update
        our database based on successful API response, using API metadata for
        version tracking and future drift detection.

        Args:
            current_playlist: Current playlist state before operations
            applied_operations: Operations that were successfully applied
            api_metadata: Metadata from successful API response (snapshot_id, etc.)
            command: Update command with configuration
            uow: UnitOfWork for repository access
        """
        try:
            # Get connector playlist repository for updating connector_playlist table
            connector_repo = uow.get_connector_playlist_repository()
            
            # Create updated track items list based on desired final state
            updated_items = await self._calculate_updated_playlist_items(
                current_playlist, applied_operations, command
            )
            
            # Create ConnectorPlaylist domain model for the updated state
            
            # Get current connector playlist if it exists
            existing_connector_playlist = await connector_repo.get_by_connector_id(
                command.connector, command.playlist_id
            )
            
            # Create updated connector playlist model
            updated_connector_playlist = ConnectorPlaylist(
                id=existing_connector_playlist.id if existing_connector_playlist else None,
                connector_name=command.connector,
                connector_playlist_id=command.playlist_id,
                name=current_playlist.name,
                description=current_playlist.description,
                owner=api_metadata.get("owner_name"),
                owner_id=api_metadata.get("owner_id"),
                is_public=api_metadata.get("is_public", False),
                collaborative=api_metadata.get("collaborative", False),
                follower_count=api_metadata.get("follower_count"),
                items=updated_items,  # Updated track list based on operations
                raw_metadata=api_metadata,  # Store full API response for future drift detection
                last_updated=datetime.now(UTC),
            )
            
            # Optimistic update: save immediately based on successful API response
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
            # Log but don't fail the entire operation - external API succeeded
            # This ensures we don't roll back external changes due to database issues
            logger.warning(
                "Failed to update connector_playlist table after successful API call",
                connector=command.connector,
                playlist_id=command.playlist_id,
                error=str(e),
                # This would be a good candidate for a compensation queue in production
            )

    async def _calculate_updated_playlist_items(
        self, current_playlist: Playlist, applied_operations: list, command: UpdateConnectorPlaylistCommand
    ) -> list[ConnectorPlaylistItem]:
        """Calculate the updated playlist items based on desired final state.

        With the DRY approach from Priority 1, we replace the entire external playlist,
        so we can simply create items from the desired final state instead of applying operations.

        Args:
            current_playlist: Original playlist state (for metadata reference)
            applied_operations: Operations that were applied (for logging/metrics)
            command: Update command containing the desired final state

        Returns:
            Updated list of playlist items for connector_playlist table
        """
        # Create items list from desired final state (command.new_tracklist.tracks)
        items = []
        for i, track in enumerate(command.new_tracklist.tracks):
            if track.connector_track_ids and track.connector_track_ids.get(command.connector):
                item = ConnectorPlaylistItem(
                    connector_track_id=track.connector_track_ids[command.connector],
                    position=i,
                    added_at=datetime.now(UTC).isoformat(),  # External API determines actual added_at
                    added_by_id="narada",  # Could be parameterized
                    extras={
                        "track_uri": f"{command.connector}:track:{track.connector_track_ids[command.connector]}",
                        "local": False,
                        "primary_color": None,
                        "video_thumbnail": None,
                    }
                )
                items.append(item)

        logger.debug(
            "Calculated updated playlist items from desired final state",
            connector=command.connector,
            items_count=len(items),
            operations_applied=len(applied_operations),
        )
        
        return items

    def _estimate_api_calls(self, operations: list, batch_size: int) -> int:
        """Estimate API calls needed for operations.

        Args:
            operations: List of operations to execute
            batch_size: API batch size limit

        Returns:
            Estimated number of API calls
        """
        if not operations:
            return 0
            
        # Count operations by type
        add_ops = sum(1 for op in operations if op.operation_type == PlaylistOperationType.ADD)
        remove_ops = sum(1 for op in operations if op.operation_type == PlaylistOperationType.REMOVE)  
        move_ops = sum(1 for op in operations if op.operation_type == PlaylistOperationType.MOVE)

        # Calculate API calls: adds and removes can be batched, moves are individual
        api_calls = 0
        api_calls += (add_ops + batch_size - 1) // batch_size  # Ceiling division
        api_calls += (remove_ops + batch_size - 1) // batch_size
        api_calls += move_ops  # Move operations are typically individual API calls

        return api_calls

    async def _append_tracks_to_connector(
        self,
        current_playlist: Playlist,
        command: UpdateConnectorPlaylistCommand,
        uow: UnitOfWorkProtocol,
    ) -> tuple[int, dict, int, int]:
        """Append new tracks to external connector playlist.
        
        Args:
            current_playlist: Current playlist state
            command: Command with playlist context and configuration
            uow: UnitOfWork for repository access
            
        Returns:
            Tuple of (api_calls_made, external_metadata, operations_performed, tracks_added)
        """
        # Filter out tracks that already exist to avoid duplicates
        existing_track_ids = {track.id for track in current_playlist.tracks if track.id}
        new_tracks = [
            track for track in command.new_tracklist.tracks
            if not track.id or track.id not in existing_track_ids
        ]
        
        if not new_tracks:
            logger.info("No new tracks to append to connector playlist")
            return 0, {}, 0, 0
        
        logger.info(f"Appending {len(new_tracks)} new tracks to {command.connector} playlist")
        
        if command.dry_run:
            # For dry run, estimate API calls without executing
            estimated_api_calls = (len(new_tracks) + 99) // 100  # Ceiling division for batches
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
        """Update connector playlist metadata (name/description).
        
        Args:
            playlist_id: External playlist ID
            command: Command with metadata updates
            uow: UnitOfWork for repository access
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
            logger.info(f"Updating {command.connector} playlist metadata", 
                       playlist_id=playlist_id, updates=metadata_updates)
            await connector_instance.update_playlist_metadata(playlist_id, metadata_updates)

    async def _create_canonical_for_connector_playlist(
        self,
        connector_playlist_id: str,
        connector: str,
        uow: UnitOfWorkProtocol,
    ) -> Playlist:
        """Create canonical playlist for existing connector playlist.
        
        This handles the case where a connector playlist exists but no
        canonical playlist is linked to it.
        
        Args:
            connector_playlist_id: External playlist ID
            connector: Connector name
            uow: UnitOfWork for repository access
            
        Returns:
            Newly created canonical playlist
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
            description=connector_playlist_info.get("description", f"Imported from {connector.title()}"),
            tracks=[],  # Will be populated separately if needed
            connector_playlist_ids={connector: connector_playlist_id},
            metadata={
                "created_from_connector": connector,
                "original_connector_id": connector_playlist_id,
                "auto_created": True,
            }
        )
        
        # Save to database
        playlist_repo = uow.get_playlist_repository()
        saved_playlist = await playlist_repo.save_playlist(canonical_playlist)
        
        logger.info(f"Auto-created canonical playlist {saved_playlist.id} for {connector} playlist {connector_playlist_id}")
        
        return saved_playlist