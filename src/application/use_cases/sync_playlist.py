"""Synchronizes playlist track changes across local database and external services.

Updates both the internal database (canonical) and external platforms like Spotify
with new track lists, handling failures gracefully and providing detailed operation metrics.
"""

from datetime import UTC, datetime
from typing import Any

from attrs import define, field

from src.config import get_logger
from src.domain.entities.playlist import Playlist
from src.domain.entities.track import TrackList
from src.domain.repositories import UnitOfWorkProtocol

from .update_canonical_playlist import (
    UpdateCanonicalPlaylistCommand,
    UpdateCanonicalPlaylistResult,
    UpdateCanonicalPlaylistUseCase,
)
from .update_connector_playlist import (
    UpdateConnectorPlaylistCommand,
    UpdateConnectorPlaylistResult,
    UpdateConnectorPlaylistUseCase,
)

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class SyncPlaylistCommand:
    """Parameters for updating a playlist across local database and external services.

    Args:
        playlist_id: Unique identifier for the playlist to update.
        new_tracklist: Complete list of tracks to set as the playlist content.
        target_connectors: External services to update (defaults to ["spotify"]).
        update_canonical: Whether to update the local database.
        update_connectors: Whether to update external services.
        dry_run: If True, simulate changes without making actual updates.
        preserve_timestamps: Whether to maintain track ordering for external services.
        batch_size: Maximum tracks per API request (max 100 for Spotify).
        max_api_calls: Maximum API requests allowed per external service.
        metadata: Additional context data for the operation.
        timestamp: When this command was created.
    """

    playlist_id: str
    new_tracklist: TrackList
    target_connectors: list[str] = field(
        factory=lambda: ["spotify"]
    )  # Default to Spotify
    update_canonical: bool = True  # Whether to update internal database
    update_connectors: bool = True  # Whether to update external services
    dry_run: bool = False
    preserve_timestamps: bool = True  # Whether to use proper sequencing for connectors
    batch_size: int = 100  # API batch size limit
    max_api_calls: int = 50  # Maximum API calls allowed per connector
    metadata: dict[str, Any] = field(factory=dict)
    timestamp: datetime = field(factory=lambda: datetime.now(UTC))

    def validate(self) -> bool:
        """Validates command parameters meet business requirements.

        Returns:
            True if all validation rules pass, False otherwise.
        """
        if not self.playlist_id:
            return False

        if not self.new_tracklist.tracks:
            return False

        if not self.update_canonical and not self.update_connectors:
            return False

        if self.update_connectors and not self.target_connectors:
            return False

        if self.batch_size > 100:  # Spotify API limit
            return False

        return not self.max_api_calls < 1


@define(frozen=True, slots=True)
class SyncPlaylistResult:
    """Detailed results from updating a playlist across local database and external services.

    Args:
        playlist: Final state of the playlist after all updates.
        canonical_result: Results from updating the local database (None if skipped).
        connector_results: Results from each external service, keyed by service name.
        total_operations_performed: Sum of all database and API operations.
        total_api_calls_made: Total external API requests across all services.
        execution_time_ms: How long the entire operation took in milliseconds.
        errors: List of error messages from failed operations.
        warnings: List of warning messages from partial failures.
    """

    playlist: Playlist
    canonical_result: UpdateCanonicalPlaylistResult | None = None
    connector_results: dict[str, UpdateConnectorPlaylistResult] = field(factory=dict)
    total_operations_performed: int = 0
    total_api_calls_made: int = 0
    execution_time_ms: int = 0
    errors: list[str] = field(factory=list)
    warnings: list[str] = field(factory=list)

    @property
    def operation_summary(self) -> dict[str, Any]:
        """Creates a structured summary of all sync operations for logging and monitoring."""
        canonical_summary = (
            self.canonical_result.operation_summary if self.canonical_result else {}
        )
        connector_summaries = {
            connector: result.operation_summary
            for connector, result in self.connector_results.items()
        }

        return {
            "playlist_id": self.playlist.id,
            "playlist_name": self.playlist.name,
            "canonical": canonical_summary,
            "connectors": connector_summaries,
            "total_operations": self.total_operations_performed,
            "total_api_calls": self.total_api_calls_made,
            "execution_time_ms": self.execution_time_ms,
            "success": len(self.errors) == 0,
            "warnings_count": len(self.warnings),
        }


@define(slots=True)
class SyncPlaylistUseCase:
    """Updates playlist tracks in both local database and external services like Spotify.

    Coordinates playlist updates across multiple systems, handling partial failures
    gracefully and providing detailed metrics for monitoring. Updates the local
    database first, then external services, collecting results from each operation.

    Args:
        canonical_use_case: Handles updates to the local database.
        connector_use_case: Handles updates to external services.
    """

    canonical_use_case: UpdateCanonicalPlaylistUseCase = field(
        factory=UpdateCanonicalPlaylistUseCase
    )
    connector_use_case: UpdateConnectorPlaylistUseCase = field(
        factory=UpdateConnectorPlaylistUseCase
    )

    async def execute(
        self, command: SyncPlaylistCommand, uow: UnitOfWorkProtocol
    ) -> SyncPlaylistResult:
        """Updates playlist tracks in local database and external services.

        Args:
            command: Parameters specifying what to update and how.
            uow: Database transaction manager and repository access.

        Returns:
            Detailed results including operation counts, timing, and any errors.

        Raises:
            ValueError: If command validation fails.
        """
        if not command.validate():
            raise ValueError("Invalid command: failed business rule validation")

        start_time = datetime.now(UTC)

        logger.info(
            "Starting playlist synchronization",
            playlist_id=command.playlist_id,
            target_connectors=command.target_connectors,
            update_canonical=command.update_canonical,
            update_connectors=command.update_connectors,
            track_count=len(command.new_tracklist.tracks),
            dry_run=command.dry_run,
        )

        canonical_result = None
        connector_results = {}
        warnings = []
        errors = []
        final_playlist = None

        try:
            # Step 1: Update canonical playlist first (if requested)
            if command.update_canonical:
                logger.info("Updating canonical playlist")
                canonical_command = UpdateCanonicalPlaylistCommand(
                    playlist_id=command.playlist_id,
                    new_tracklist=command.new_tracklist,
                    dry_run=command.dry_run,
                    metadata=command.metadata,
                )

                canonical_result = await self.canonical_use_case.execute(
                    canonical_command, uow
                )
                final_playlist = canonical_result.playlist

                logger.info(
                    "Canonical playlist update completed",
                    operations=canonical_result.operations_performed,
                    execution_time_ms=canonical_result.execution_time_ms,
                )

            # Step 2: Update connector playlists (if requested)
            if command.update_connectors:
                for connector in command.target_connectors:
                    logger.info(f"Updating {connector} playlist")

                    try:
                        connector_command = UpdateConnectorPlaylistCommand(
                            playlist_id=command.playlist_id,
                            new_tracklist=command.new_tracklist,
                            connector=connector,
                            dry_run=command.dry_run,
                            preserve_timestamps=command.preserve_timestamps,
                            batch_size=command.batch_size,
                            max_api_calls=command.max_api_calls,
                            metadata=command.metadata,
                        )

                        connector_result = await self.connector_use_case.execute(
                            connector_command, uow
                        )
                        connector_results[connector] = connector_result

                        logger.info(
                            f"{connector} playlist update completed",
                            operations=connector_result.operations_performed,
                            api_calls=connector_result.api_calls_made,
                            execution_time_ms=connector_result.execution_time_ms,
                        )

                    except Exception as e:
                        error_msg = f"Failed to update {connector} playlist: {e!s}"
                        errors.append(error_msg)
                        logger.error(
                            error_msg,
                            connector=connector,
                            playlist_id=command.playlist_id,
                        )

                        # Continue with other connectors instead of failing completely
                        continue

            # Step 3: Get final playlist state if we didn't update canonical
            if final_playlist is None:
                # Need to get the current playlist state
                async with uow:
                    playlist_repo = uow.get_playlist_repository()
                    try:
                        final_playlist = await playlist_repo.get_playlist_by_id(
                            int(command.playlist_id)
                        )
                    except ValueError:
                        final_playlist = await playlist_repo.get_playlist_by_connector(
                            "spotify", command.playlist_id, raise_if_not_found=True
                        )
                        if final_playlist is None:
                            raise ValueError(
                                f"Playlist {command.playlist_id} not found"
                            ) from None

            # Step 4: Calculate comprehensive metrics
            total_operations = 0
            total_api_calls = 0

            if canonical_result:
                total_operations += canonical_result.operations_performed

            for connector_result in connector_results.values():
                total_operations += connector_result.operations_performed
                total_api_calls += connector_result.api_calls_made

            execution_time = int(
                (datetime.now(UTC) - start_time).total_seconds() * 1000
            )

            # Step 5: Generate warnings for partial failures
            if errors and connector_results:
                warnings.append(
                    f"Some connector updates failed but others succeeded. "
                    f"Successfully updated: {list(connector_results.keys())}"
                )

            result = SyncPlaylistResult(
                playlist=final_playlist,
                canonical_result=canonical_result,
                connector_results=connector_results,
                total_operations_performed=total_operations,
                total_api_calls_made=total_api_calls,
                execution_time_ms=execution_time,
                errors=errors,
                warnings=warnings,
            )

            logger.info(
                "Playlist synchronization completed",
                playlist_id=command.playlist_id,
                canonical_operations=canonical_result.operations_performed
                if canonical_result
                else 0,
                connector_results_count=len(connector_results),
                total_operations=total_operations,
                total_api_calls=total_api_calls,
                execution_time_ms=execution_time,
                errors_count=len(errors),
                warnings_count=len(warnings),
            )

            return result

        except Exception as e:
            logger.error(
                "Playlist synchronization failed",
                error=str(e),
                playlist_id=command.playlist_id,
                target_connectors=command.target_connectors,
            )
            raise
