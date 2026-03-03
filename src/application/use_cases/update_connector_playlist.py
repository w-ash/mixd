"""Synchronizes Spotify/Apple Music playlists with local track collections.

Calculates minimal track changes (add/remove/move) to update external service
playlists while preserving track timestamps and minimizing API calls.
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: use case results, OperationResult metadata, metric values

from datetime import UTC, datetime
from typing import Any, TypedDict

from attrs import define, field

from src.application.connector_protocols import PlaylistConnector
from src.application.use_cases._shared import (
    AppendOperationResult,
    build_api_execution_metadata,
    classify_connector_api_error,
    classify_database_error,
    count_operation_types,
    create_connector_playlist_items_from_tracks,
    resolve_playlist_connector,
)
from src.application.use_cases._shared.command_validators import (
    api_batch_size_validator,
    non_empty_string,
    positive_int_in_range,
    validate_tracklist_has_tracks,
)
from src.application.utilities.timing import ExecutionTimer
from src.config import get_logger
from src.domain.entities import ConnectorPlaylist, utc_now_factory
from src.domain.entities.playlist import ConnectorPlaylistItem, Playlist
from src.domain.entities.track import TrackList
from src.domain.playlist import PlaylistOperation, calculate_playlist_diff
from src.domain.playlist.execution_strategies import get_execution_strategy
from src.domain.repositories import UnitOfWorkProtocol
from src.domain.repositories.interfaces import ConnectorPlaylistRepositoryProtocol

logger = get_logger(__name__)


class _ConnectorApiResult(TypedDict):
    """Result from executing playlist operations against a connector API."""

    success: bool
    api_calls_made: int
    metadata: dict[str, Any]
    error: str | None
    partial_success: bool


@define(frozen=True, slots=True)
class UpdateConnectorPlaylistCommand:
    """Input parameters for updating a Spotify/Apple Music playlist.

    Contains the target playlist ID, desired track list, and update options
    like append-only mode vs full synchronization.
    """

    playlist_id: str = field(validator=non_empty_string)
    new_tracklist: TrackList = field(validator=validate_tracklist_has_tracks)
    connector: str = field(validator=non_empty_string)  # "spotify", "apple_music", etc.
    dry_run: bool = False
    append_mode: bool = False  # True=append, False=overwrite with preservation
    playlist_name: str | None = None  # Optional name update
    playlist_description: str | None = None  # Optional description update
    preserve_timestamps: bool = True  # Whether to use proper sequencing
    batch_size: int = field(
        default=100,
        validator=api_batch_size_validator("api.spotify_large_batch_size"),
    )
    max_api_calls: int = field(
        default=50,
        validator=positive_int_in_range(1, 1000),
    )
    metadata: dict[str, Any] = field(factory=dict)
    timestamp: datetime = field(factory=utc_now_factory)


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
            "success": not self.errors,
        }


@define(slots=True)
class UpdateConnectorPlaylistUseCase:
    """Synchronizes external music service playlists with local track data.

    Calculates minimal changes (remove→add→move operations) to update Spotify/Apple
    Music playlists efficiently. Preserves track timestamps and handles API batching.
    Updates local database after successful external API calls.
    """

    async def _validate_playlist_pre_execution(
        self, connector: PlaylistConnector, playlist_id: str
    ) -> None:
        """Validate playlist exists before executing operations.

        Args:
            connector: Connector instance
            playlist_id: External playlist ID to validate

        Raises:
            ValueError: If playlist cannot be accessed
        """
        try:
            playlist_details = await connector.get_playlist_details(playlist_id)
            logger.debug(
                "Pre-execution playlist validation passed",
                playlist_id=playlist_id,
                playlist_name=playlist_details.get("name"),
            )
        except Exception as e:
            logger.error(
                "Pre-execution playlist validation failed",
                playlist_id=playlist_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise ValueError(f"Cannot access playlist {playlist_id}: {e}") from e

    async def _validate_playlist_post_execution(
        self,
        connector: PlaylistConnector,
        playlist_id: str,
        snapshot_id: str | None,
        operations_count: int,
    ) -> tuple[bool, bool]:
        """Validate playlist state after executing operations.

        Args:
            connector: Connector instance
            playlist_id: External playlist ID
            snapshot_id: Snapshot ID from operation execution
            operations_count: Number of operations executed

        Returns:
            Tuple of (operation_success, partial_success)
        """
        operation_success = True
        partial_success = False

        if snapshot_id is None:
            logger.warning(
                "Operations completed but no snapshot_id returned",
                operations_count=operations_count,
            )
            operation_success = False
            partial_success = True

        # Additional validation: verify playlist state if possible
        try:
            _ = await connector.get_playlist_details(playlist_id)
            logger.debug(
                "Post-execution playlist validation completed",
                playlist_id=playlist_id,
                final_snapshot=snapshot_id,
            )
        except Exception as e:
            logger.warning(
                "Post-execution playlist validation failed",
                playlist_id=playlist_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            # Don't fail the operation for this, just log the warning

        return operation_success, partial_success

    async def _get_existing_connector_playlist(
        self,
        connector_repo: ConnectorPlaylistRepositoryProtocol,
        connector_name: str,
        playlist_id: str,
    ) -> ConnectorPlaylist | None:
        """Retrieve existing connector playlist record if it exists.

        Args:
            connector_repo: Connector playlist repository
            connector_name: Name of the connector
            playlist_id: External playlist ID

        Returns:
            Existing ConnectorPlaylist or None
        """
        try:
            existing = await connector_repo.get_by_connector_id(
                connector_name, playlist_id
            )
            if existing:
                logger.debug(
                    "Found existing connector playlist record",
                    existing_id=existing.id,
                    existing_items=len(existing.items) if existing.items else 0,
                )
        except Exception as e:
            logger.warning(
                "Failed to retrieve existing connector playlist record",
                error=str(e),
                error_type=type(e).__name__,
            )
            return None
        else:
            return existing

    def _build_connector_playlist_entity(
        self,
        current_playlist: Playlist,
        command: UpdateConnectorPlaylistCommand,
        updated_items: list[ConnectorPlaylistItem],
        enhanced_metadata: dict[str, Any],
        existing_id: int | None,
    ) -> ConnectorPlaylist:
        """Build ConnectorPlaylist entity for database update.

        Args:
            current_playlist: Current playlist state
            command: Update command with playlist info
            updated_items: Updated playlist items
            enhanced_metadata: Comprehensive metadata dict
            existing_id: ID of existing record if any

        Returns:
            ConnectorPlaylist ready for persistence
        """
        return ConnectorPlaylist(
            id=existing_id,
            connector_name=command.connector,
            connector_playlist_identifier=command.playlist_id,
            name=current_playlist.name,
            description=current_playlist.description,
            items=updated_items,
            raw_metadata=enhanced_metadata,
            last_updated=datetime.now(UTC),
        )

    async def _persist_connector_playlist_with_verification(
        self,
        connector_repo: ConnectorPlaylistRepositoryProtocol,
        playlist_entity: ConnectorPlaylist,
        command: UpdateConnectorPlaylistCommand,
        updated_items_count: int,
    ) -> None:
        """Persist connector playlist and verify the update.

        Args:
            connector_repo: Connector playlist repository
            playlist_entity: Playlist entity to persist
            command: Update command for logging
            updated_items_count: Number of items being persisted

        Raises:
            RuntimeError: If persistence or verification fails
        """
        try:
            await connector_repo.upsert_model(playlist_entity)
            logger.info(
                "Connector playlist database update completed successfully",
                connector=command.connector,
                playlist_id=command.playlist_id,
                items_count=updated_items_count,
                existing_record_updated=playlist_entity.id is not None,
            )

            # Post-update verification
            try:
                verification_record = await connector_repo.get_by_connector_id(
                    command.connector, command.playlist_id
                )
                if verification_record:
                    logger.debug(
                        "Database update verification successful",
                        record_id=verification_record.id,
                        record_items=len(verification_record.items)
                        if verification_record.items
                        else 0,
                        last_updated=verification_record.last_updated,
                    )
                else:
                    logger.error(
                        "Database update verification failed: record not found after update"
                    )
            except Exception as verification_error:
                logger.warning(
                    "Database update verification failed",
                    error=str(verification_error),
                    error_type=type(verification_error).__name__,
                )

        except Exception as db_error:
            logger.error(
                "Database update failed",
                connector=command.connector,
                playlist_id=command.playlist_id,
                error=str(db_error),
                error_type=type(db_error).__name__,
                items_attempted=updated_items_count,
            )
            raise

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
            ValueError: If command execution fails.
        """
        timer = ExecutionTimer()

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
                    append_result = await self._append_tracks_to_connector(
                        current_playlist, command, uow
                    )
                    api_calls_made = append_result.api_calls_made
                    external_metadata = append_result.metadata
                    operations_performed = append_result.operations_performed
                    tracks_added = append_result.tracks_added
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
                            execution_time_ms=timer.stop(),
                            confidence_score=diff.confidence_score,
                        )

                    # Step 3: Use unified execution strategy for API operations
                    api_strategy = get_execution_strategy("api")
                    execution_plan = api_strategy.plan_operations(diff)
                    sequenced_operations = execution_plan.operations

                    counts = count_operation_types(sequenced_operations)
                    logger.debug(
                        f"Planned {len(sequenced_operations)} operations for {command.connector}",
                        remove_ops=counts.removed,
                        add_ops=counts.added,
                        move_ops=counts.moved,
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

                result = UpdateConnectorPlaylistResult(
                    playlist_id=command.playlist_id,
                    connector=command.connector,
                    operations_performed=operations_performed,
                    api_calls_made=api_calls_made,
                    tracks_added=tracks_added,
                    tracks_removed=tracks_removed,
                    tracks_moved=tracks_moved,
                    execution_time_ms=timer.stop(),
                    confidence_score=confidence_score,
                    external_metadata=external_metadata,
                )

                logger.info(
                    "Connector playlist update completed",
                    playlist_id=command.playlist_id,
                    connector=command.connector,
                    operations_performed=operations_performed,
                    api_calls_made=api_calls_made,
                    execution_time_ms=timer.elapsed_ms,
                    dry_run=command.dry_run,
                )

            except Exception as e:
                logger.error(
                    "Connector playlist update failed",
                    error=str(e),
                    playlist_id=command.playlist_id,
                    connector=command.connector,
                )
                raise
            else:
                return result

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
        sequenced_operations: list[PlaylistOperation],
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

        # Count operations by type using shared utility
        counts = count_operation_types(sequenced_operations)

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
            add_ops=counts.added,
            remove_ops=counts.removed,
            move_ops=counts.moved,
            success=api_response["success"],
        )

        return (
            api_response["api_calls_made"],
            api_response["metadata"],
            len(sequenced_operations) if api_response["success"] else 0,
            counts.added if api_response["success"] else 0,
            counts.removed if api_response["success"] else 0,
            counts.moved if api_response["success"] else 0,
        )

    async def _execute_connector_api_operations(
        self,
        _current_playlist: Playlist,
        sequenced_operations: list[PlaylistOperation],
        command: UpdateConnectorPlaylistCommand,
        uow: UnitOfWorkProtocol,
    ) -> _ConnectorApiResult:
        """Executes playlist operations via connector with enhanced state validation."""
        # Pre-execution state validation
        if not sequenced_operations:
            logger.warning("No operations to execute")
            return {
                "success": True,
                "api_calls_made": 0,
                "metadata": {"operations_applied": 0},
                "error": None,
                "partial_success": False,
            }

        try:
            # Get connector and validate it supports playlist operations
            connector = resolve_playlist_connector(command.connector, uow)
            counts = count_operation_types(sequenced_operations)

            logger.info(
                "Executing differential operations on external playlist",
                connector=command.connector,
                playlist_id=command.playlist_id,
                operations_count=len(sequenced_operations),
                remove_ops=counts.removed,
                add_ops=counts.added,
                move_ops=counts.moved,
            )

            # Pre-execution validation: check playlist exists
            await self._validate_playlist_pre_execution(connector, command.playlist_id)

            # Execute operations with detailed tracking
            track_repo = uow.get_track_repository()
            final_snapshot_id = await connector.execute_playlist_operations(
                command.playlist_id, sequenced_operations, track_repo=track_repo
            )

            # Post-execution validation: verify operations actually applied
            (
                operation_success,
                partial_success,
            ) = await self._validate_playlist_post_execution(
                connector,
                command.playlist_id,
                final_snapshot_id,
                len(sequenced_operations),
            )

            # Build detailed response metadata using builder pattern
            external_metadata = build_api_execution_metadata(
                operations_count=len(sequenced_operations),
                snapshot_id=final_snapshot_id,
                tracks_added=counts.added if operation_success else 0,
                tracks_removed=counts.removed if operation_success else 0,
                tracks_moved=counts.moved if operation_success else 0,
                validation_passed=operation_success,
            )

            return _ConnectorApiResult(
                success=operation_success,
                api_calls_made=len(sequenced_operations),
                metadata=dict(external_metadata),
                error=None,
                partial_success=partial_success,
            )

        except Exception as e:
            # Classify error using pattern matching utility
            error_classification = classify_connector_api_error(e)

            logger.error(
                "External playlist update failed",
                connector=command.connector,
                playlist_id=command.playlist_id,
                error=str(e),
                **error_classification,
                operations_attempted=len(sequenced_operations),
            )

            return {
                "success": False,
                "api_calls_made": 0,
                "metadata": error_classification,
                "error": str(e),
                "partial_success": False,
            }

    async def _update_connector_playlist_optimistic(
        self,
        current_playlist: Playlist,
        applied_operations: list[PlaylistOperation],
        api_metadata: dict[str, Any],
        command: UpdateConnectorPlaylistCommand,
        uow: UnitOfWorkProtocol,
    ) -> None:
        """Updates local database after external API calls with enhanced validation."""
        # Pre-validation: check if we should even attempt the update
        operations_applied = api_metadata.get("operations_applied", 0)
        validation_passed = api_metadata.get("validation_passed", True)

        if operations_applied == 0 and applied_operations:
            logger.warning(
                "Skipping database update: no operations were successfully applied",
                connector=command.connector,
                playlist_id=command.playlist_id,
                requested_operations=len(applied_operations),
                applied_operations=operations_applied,
            )
            return

        if not validation_passed:
            logger.warning(
                "Proceeding with database update despite validation issues",
                connector=command.connector,
                playlist_id=command.playlist_id,
                validation_passed=validation_passed,
            )

        try:
            connector_repo = uow.get_connector_playlist_repository()

            # Validate repository is available
            if not connector_repo:
                raise RuntimeError("Connector playlist repository not available")  # noqa: TRY301

            # Create playlist items from final desired state
            updated_items = self._create_playlist_items_from_tracklist(command)

            # Validate created items
            if not updated_items and command.new_tracklist.tracks:
                logger.warning(
                    "No playlist items created despite tracks in command",
                    command_tracks=len(command.new_tracklist.tracks),
                    connector=command.connector,
                )

            # Get existing connector playlist for ID continuity
            existing = await self._get_existing_connector_playlist(
                connector_repo, command.connector, command.playlist_id
            )

            # Build comprehensive metadata including state validation
            enhanced_metadata = {
                **api_metadata,
                "database_update_timestamp": datetime.now(UTC).isoformat(),
                "requested_operations": len(applied_operations),
                "items_created": len(updated_items),
                "existing_record_found": existing is not None,
                "state_consistency_check": {
                    "requested_tracks": len(command.new_tracklist.tracks),
                    "created_items": len(updated_items),
                    "operations_requested": len(applied_operations),
                    "operations_applied": operations_applied,
                },
            }

            # Create updated connector playlist with validation
            updated_connector_playlist = self._build_connector_playlist_entity(
                current_playlist,
                command,
                updated_items,
                enhanced_metadata,
                existing.id if existing else None,
            )

            # Validate the playlist before saving
            if not updated_connector_playlist.connector_name:
                raise ValueError("Connector name cannot be empty")  # noqa: TRY301
            if not updated_connector_playlist.connector_playlist_identifier:
                raise ValueError("Connector playlist identifier cannot be empty")  # noqa: TRY301

            # Persist with verification
            await self._persist_connector_playlist_with_verification(
                connector_repo,
                updated_connector_playlist,
                command,
                len(updated_items),
            )

        except Exception as e:
            # Classify database error using utility
            db_error_classification = classify_database_error(e)

            logger.error(
                "Failed to update connector_playlist table after external API success",
                connector=command.connector,
                playlist_id=command.playlist_id,
                error=str(e),
                **db_error_classification,
                operations_applied=operations_applied,
                api_metadata_snapshot=api_metadata.get("snapshot_id"),
            )

            # Re-raise to maintain error propagation for critical database failures
            raise RuntimeError(
                f"Database consistency error: API operations succeeded but database update failed: {e}"
            ) from e

    def _create_playlist_items_from_tracklist(
        self, command: UpdateConnectorPlaylistCommand
    ) -> list[ConnectorPlaylistItem]:
        """Creates playlist items from target track list using factory."""
        return create_connector_playlist_items_from_tracks(
            tracks=command.new_tracklist.tracks,
            connector_name=command.connector,
        )

    async def _append_tracks_to_connector(
        self,
        current_playlist: Playlist,
        command: UpdateConnectorPlaylistCommand,
        uow: UnitOfWorkProtocol,
    ) -> AppendOperationResult:
        """Adds new tracks to end of external playlist without removing existing ones.

        Filters out duplicate tracks and appends only new ones to preserve
        existing playlist content and user-added tracks.

        Args:
            current_playlist: Current playlist state.
            command: Update command with new tracks and configuration.
            uow: Database access manager.

        Returns:
            AppendOperationResult with operation counts and metadata.
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
            return AppendOperationResult(
                api_calls_made=0,
                metadata={},
                operations_performed=0,
                tracks_added=0,
            )

        logger.info(
            f"Appending {len(new_tracks)} new tracks to {command.connector} playlist"
        )

        if command.dry_run:
            # For dry run, estimate API calls without executing
            estimated_api_calls = (len(new_tracks) + 99) // 100
            return AppendOperationResult(
                api_calls_made=estimated_api_calls,
                metadata={},
                operations_performed=len(new_tracks),
                tracks_added=len(new_tracks),
            )

        # Execute append operation via connector
        connector_instance = resolve_playlist_connector(command.connector, uow)

        # Append tracks to external playlist
        api_metadata = await connector_instance.append_tracks_to_playlist(
            command.playlist_id, new_tracks
        )

        # Update connector_playlist table optimistically
        await self._update_connector_playlist_optimistic(
            current_playlist, [], api_metadata, command, uow
        )

        return AppendOperationResult(
            api_calls_made=1,  # Typically one API call for batch append
            metadata=api_metadata,
            operations_performed=len(new_tracks),
            tracks_added=len(new_tracks),
        )

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

        connector_instance = resolve_playlist_connector(command.connector, uow)

        metadata_updates: dict[str, str] = {}
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
        connector_instance = resolve_playlist_connector(connector, uow)

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
            entries=[],  # Will be populated separately if needed
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
