"""Downloads listening history from LastFM API and Spotify data exports.

Imports play data from music services into local database with progress tracking,
error handling, and transaction management. Supports LastFM recent/incremental/full
history imports and Spotify JSON file processing.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from attrs import define, field

from src.config import get_logger
from src.domain.entities import OperationResult
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)

ServiceType = Literal["lastfm", "spotify"]
ImportMode = Literal["recent", "incremental", "full", "file"]


@define(frozen=True, slots=True)
class ImportTracksCommand:
    """Configuration for importing tracks from music services.

    Validates service/mode combinations and ensures required parameters are present.
    Supports LastFM API imports (recent/incremental/full) and Spotify file imports.

    Attributes:
        service: Music service to import from ('lastfm' or 'spotify').
        mode: Import type ('recent', 'incremental', 'full', 'file').
        limit: Maximum tracks to import (LastFM only).
        user_id: LastFM username for user-specific imports.
        file_path: Path to Spotify data export JSON file.
        confirm: Whether user confirmed destructive operations.
        from_date: Start date for date range filtering (incremental mode only).
        to_date: End date for date range filtering (incremental mode only).
        additional_options: Extra service-specific parameters.

    Raises:
        ValueError: If service/mode combination is invalid or required params missing.
    """

    service: ServiceType
    mode: ImportMode

    # Service-specific parameters
    limit: int | None = None  # For lastfm recent/full imports
    user_id: str | None = None  # For lastfm incremental/full imports
    file_path: Path | None = None  # For spotify file imports
    confirm: bool = False  # For destructive operations like full history
    from_date: datetime | None = None  # Start date for date range filtering
    to_date: datetime | None = None  # End date for date range filtering

    # Additional options for extensibility
    additional_options: dict[str, Any] = field(factory=dict)

    def __attrs_post_init__(self) -> None:
        """Validates service and mode compatibility.

        Raises:
            ValueError: If LastFM uses file mode, Spotify uses non-file mode,
                or Spotify file mode missing file_path.
        """
        if self.service == "lastfm":
            if self.mode == "file":
                raise ValueError("LastFM service doesn't support file mode")
        elif self.service == "spotify":
            if self.mode != "file":
                raise ValueError(
                    f"Spotify service only supports file mode, got: {self.mode}"
                )
            if not self.file_path:
                raise ValueError("file_path is required for Spotify file imports")


@define(frozen=True, slots=True)
class ImportTracksResult:
    """Result from track import operation with performance metrics.

    Contains import statistics, timing data, and batch processing metadata
    for monitoring and debugging import operations.

    Attributes:
        operation_result: Core import statistics and error details.
        service: Music service that was imported from.
        mode: Import mode that was executed.
        execution_time_ms: Total time taken for import in milliseconds.
        total_batches: Number of processing batches used.
        successful_batches: Number of batches that completed successfully.
        failed_batches: Number of batches that failed.
    """

    operation_result: OperationResult
    service: ServiceType
    mode: ImportMode
    execution_time_ms: int = 0

    # Batch processing metadata (for SQLite optimization)
    total_batches: int = 0
    successful_batches: int = 0
    failed_batches: int = 0

    @property
    def tracks_imported(self) -> int:
        """Returns number of tracks successfully imported."""
        return self.operation_result.imported_count or 0

    @property
    def success_rate(self) -> float:
        """Returns import success rate as percentage (0-100)."""
        return self.operation_result.success_rate or 0.0


@define(slots=True)
class ImportTracksUseCase:
    """Downloads and stores listening history from music services.

    Orchestrates importing play data from LastFM API (recent/incremental/full history)
    or Spotify data export files into local database with transaction management.
    """

    async def execute(
        self, command: ImportTracksCommand, uow: UnitOfWorkProtocol
    ) -> ImportTracksResult:
        """Downloads listening history from specified music service.

        Args:
            command: Import configuration with service, mode, and parameters.
            uow: Database transaction manager for atomic operations.

        Returns:
            Import statistics including tracks imported, timing, and error details.

        Raises:
            ValueError: If service/mode combination is unsupported or params missing.
        """
        import time

        start_time = time.time()

        with logger.contextualize(
            operation="import_tracks_use_case",
            service=command.service,
            mode=command.mode,
        ):
            logger.info(f"Starting {command.service} {command.mode} import")

            try:
                # Delegate to appropriate import strategy
                operation_result = await self._execute_import(command, uow)

                execution_time_ms = int((time.time() - start_time) * 1000)

                logger.info(
                    f"Successfully completed {command.service} {command.mode} import: "
                    f"{operation_result.imported_count} tracks imported"
                )

                return ImportTracksResult(
                    operation_result=operation_result,
                    service=command.service,
                    mode=command.mode,
                    execution_time_ms=execution_time_ms,
                    total_batches=1,
                    successful_batches=1,
                    failed_batches=0,
                )

            except Exception as e:
                execution_time_ms = int((time.time() - start_time) * 1000)
                error_msg = f"{command.service} {command.mode} import failed: {e}"
                logger.error(error_msg)

                # Return failed result instead of raising
                failed_result = OperationResult(
                    operation_name=f"{command.service.title()} {command.mode.title()} Import",
                    imported_count=0,
                    error_count=1,
                    execution_time=execution_time_ms / 1000.0,
                    play_metrics={"error": str(e)},
                )

                return ImportTracksResult(
                    operation_result=failed_result,
                    service=command.service,
                    mode=command.mode,
                    execution_time_ms=execution_time_ms,
                    total_batches=1,
                    successful_batches=0,
                    failed_batches=1,
                )

    async def _execute_import(
        self, command: ImportTracksCommand, uow: UnitOfWorkProtocol
    ) -> OperationResult:
        """Routes to service-specific import handler based on command."""
        match command.service:
            case "lastfm":
                return await self._run_lastfm_import(command, uow)
            case "spotify":
                return await self._run_spotify_import(command, uow)
            case _:
                raise ValueError(f"Unknown service: {command.service}")

    async def _run_lastfm_import(
        self, command: ImportTracksCommand, uow: UnitOfWorkProtocol
    ) -> OperationResult:
        """Routes to LastFM import mode handler (recent/incremental/full).
        
        Note: All modes now use the unified daily chunking approach in infrastructure.
        Mode differences are primarily application-layer concerns (confirmation, 
        checkpoint reset, parameter mapping).
        """
        match command.mode:
            case "recent":
                return await self._run_lastfm_recent(command, uow)
            case "incremental":
                return await self._run_lastfm_incremental(command, uow)
            case "full":
                return await self._run_lastfm_full_history(command, uow)
            case _:
                raise ValueError(f"LastFM service doesn't support mode: {command.mode}")

    async def _run_spotify_import(
        self, command: ImportTracksCommand, uow: UnitOfWorkProtocol
    ) -> OperationResult:
        """Routes to Spotify import handler (file mode only)."""
        match command.mode:
            case "file":
                return await self._run_spotify_file(command, uow)
            case _:
                raise ValueError(
                    f"Spotify service doesn't support mode: {command.mode}"
                )

    async def _create_lastfm_service(self, uow: UnitOfWorkProtocol):
        """Create LastFM service with repositories from provided UnitOfWork.

        Args:
            uow: Database transaction manager providing repository access.

        Returns:
            Configured LastfmPlayImporter instance ready for use.
        """
        from src.infrastructure.connectors.lastfm import LastFMConnector
        from src.infrastructure.services.lastfm_play_importer import LastfmPlayImporter

        return LastfmPlayImporter(
            plays_repository=uow.get_plays_repository(),
            checkpoint_repository=uow.get_checkpoint_repository(),
            connector_repository=uow.get_connector_repository(),
            track_repository=uow.get_track_repository(),
            lastfm_connector=LastFMConnector(),
        )

    async def _create_spotify_service(self, uow: UnitOfWorkProtocol):
        """Create Spotify service with repositories from provided UnitOfWork.

        Args:
            uow: Database transaction manager providing repository access.

        Returns:
            Configured SpotifyImportService instance ready for use.
        """
        from src.infrastructure.services.spotify_import_service import (
            SpotifyImportService,
        )

        return SpotifyImportService(
            plays_repository=uow.get_plays_repository(),
            track_repository=uow.get_track_repository(),
            connector_repository=uow.get_connector_repository(),
        )

    async def _run_lastfm_recent(
        self, command: ImportTracksCommand, uow: UnitOfWorkProtocol
    ) -> OperationResult:
        """Downloads recent plays from LastFM API.

        Fetches the most recent listening history up to specified limit.
        All tracks are automatically resolved and linked to canonical tracks.

        Args:
            command: Contains limit (default 1000).
            uow: Database transaction manager for atomic operations.

        Returns:
            Import statistics with number of plays downloaded and stored.
        """
        limit = command.limit or 1000

        # Create import service using provided UnitOfWork
        lastfm_service = await self._create_lastfm_service(uow)

        try:
            # Execute unified import (limit is ignored by unified infrastructure)
            result = await lastfm_service.import_plays(
                limit=limit,  # Legacy parameter - ignored by unified approach 
                uow=uow
            )

            logger.info(
                f"LastFM recent import completed: {result.imported_count} plays imported"
            )
            return result

        except Exception as e:
            logger.error(f"LastFM recent import failed: {e}")
            raise

    async def _run_lastfm_incremental(
        self, command: ImportTracksCommand, uow: UnitOfWorkProtocol
    ) -> OperationResult:
        """Downloads new plays from LastFM API since last sync.

        Uses stored checkpoint to only fetch plays added since previous import.
        More efficient than full history import for regular syncing.
        All tracks are automatically resolved and linked to canonical tracks.

        Args:
            command: Contains user_id.
            uow: Database transaction manager for atomic operations.

        Returns:
            Import statistics with number of new plays downloaded.
        """
        user_id = command.user_id

        # Create import service using provided UnitOfWork
        lastfm_service = await self._create_lastfm_service(uow)

        try:
            # Execute unified import with date range parameters
            result = await lastfm_service.import_plays(
                username=user_id,
                from_date=command.from_date,
                to_date=command.to_date,
                uow=uow,
            )

            logger.info(
                f"LastFM incremental import completed: {result.imported_count} plays imported"
            )
            return result

        except Exception as e:
            logger.error(f"LastFM incremental import failed: {e}")
            raise

    async def _run_lastfm_full_history(
        self, command: ImportTracksCommand, uow: UnitOfWorkProtocol
    ) -> OperationResult:
        """Downloads complete LastFM listening history.

        Imports entire play history from account creation to present. Resets sync
        checkpoint and prompts for confirmation due to large API usage.
        All tracks are automatically resolved and linked to canonical tracks.

        Args:
            command: Contains user_id and confirm flags.
            uow: Database transaction manager for atomic operations.

        Returns:
            Import statistics with total plays downloaded or cancellation result.
        """
        user_id = command.user_id
        confirm = command.confirm

        # Confirmation logic - return early if not confirmed
        if not confirm:
            from rich.console import Console
            import typer

            console = Console()
            console.print("[yellow]⚠️  Full History Import Warning[/yellow]")
            console.print("This will:")
            console.print("• Import your entire Last.fm play history")
            console.print("• Reset any existing sync checkpoint")
            console.print("• Make many API calls (may take 10+ minutes)")

            proceed = typer.confirm("Do you want to proceed?")
            if not proceed:
                console.print("[dim]Full history import cancelled[/dim]")
                # Return a cancelled result instead of raising Exit
                return OperationResult(
                    operation_name="Last.fm Full History Import",
                    plays_processed=0,
                    play_metrics={
                        "cancelled": True,
                    },
                    # Unified count fields
                    imported_count=0,
                    filtered_count=0,
                    duplicate_count=0,
                    error_count=0,
                )

        # Create import service using provided UnitOfWork
        lastfm_service = await self._create_lastfm_service(uow)

        try:
            # Reset checkpoint before full import
            username = user_id or lastfm_service.lastfm_connector.lastfm_username
            if username:
                await self._reset_lastfm_checkpoint_uow(username, uow)

            # Execute unified import for full history (limit ignored by unified approach)
            # Full history logic: checkpoint was reset above, so incremental will start from beginning
            result = await lastfm_service.import_plays(
                limit=50000,  # Legacy parameter - ignored by unified approach
                uow=uow
            )

            logger.info(
                f"LastFM full history import completed: {result.imported_count} plays imported"
            )
            return result

        except Exception as e:
            logger.error(f"LastFM full history import failed: {e}")
            raise

    async def _run_spotify_file(
        self, command: ImportTracksCommand, uow: UnitOfWorkProtocol
    ) -> OperationResult:
        """Processes Spotify data export JSON file using clean architecture pattern.

        Delegates to SpotifyImportService in infrastructure layer following the same
        pattern as LastFM imports for consistent architecture and maintainability.

        Args:
            command: Contains file_path to Spotify JSON export file.
            uow: Database transaction manager for atomic operations.

        Returns:
            Import statistics with number of plays processed from file.

        Raises:
            ValueError: If file_path is missing.
        """
        if not command.file_path:
            raise ValueError("file_path is required for Spotify file imports")

        # Create Spotify service using provided UnitOfWork - same pattern as LastFM
        spotify_service = await self._create_spotify_service(uow)

        # Delegate to infrastructure service - clean architecture compliance
        return await spotify_service.import_from_file(
            file_path=command.file_path, uow=uow
        )

    async def _reset_lastfm_checkpoint_uow(
        self, username: str, uow: UnitOfWorkProtocol
    ) -> None:
        """Clears LastFM sync checkpoint to force full history import.

        Args:
            username: LastFM username to reset checkpoint for.
            uow: Database transaction manager.
        """
        from src.domain.entities import SyncCheckpoint

        # Create a new checkpoint with no timestamp (forces full import)
        checkpoint = SyncCheckpoint(
            user_id=username, service="lastfm", entity_type="plays", last_timestamp=None
        )

        # Use transaction manager's checkpoint repository
        checkpoint_repo = uow.get_checkpoint_repository()
        await checkpoint_repo.save_sync_checkpoint(checkpoint)

        logger.info(f"Reset Last.fm checkpoint for user {username} via UoW")


async def run_import(
    service: ServiceType,
    mode: ImportMode,
    limit: int | None = None,
    user_id: str | None = None,
    file_path: Path | None = None,
    confirm: bool = False,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    **additional_options,
) -> OperationResult:
    """Downloads listening history from music services.

    Convenience function that creates import command, manages database session,
    and executes the import operation. Used by CLI commands.

    Args:
        service: Import service type ('lastfm' or 'spotify').
        mode: Import mode ('recent', 'incremental', 'full', 'file').
        limit: Maximum number of items to import.
        user_id: User ID for the import operation.
        file_path: File path for file-based imports.
        confirm: Whether to confirm before importing.
        from_date: Start date for date range filtering (incremental mode only).
        to_date: End date for date range filtering (incremental mode only).
        **additional_options: Additional service-specific options.

    Returns:
        Import statistics including tracks imported and error details.

    Raises:
        ValueError: If service or mode combination is not supported.
    """
    from src.infrastructure.persistence.database.db_connection import get_session
    from src.infrastructure.persistence.repositories.factories import get_unit_of_work

    async with get_session() as session:
        uow = get_unit_of_work(session)
        command = ImportTracksCommand(
            service=service,
            mode=mode,
            limit=limit,
            user_id=user_id,
            file_path=file_path,
            confirm=confirm,
            from_date=from_date,
            to_date=to_date,
            additional_options=additional_options,
        )

        use_case = ImportTracksUseCase()
        result = await use_case.execute(command, uow)
        return result.operation_result
