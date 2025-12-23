"""Downloads listening history from LastFM API and Spotify data exports.

Imports play data from music services into local database with progress tracking,
error handling, and transaction management. Supports LastFM recent/incremental/full
history imports and Spotify JSON file processing.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from attrs import define, field

from src.application.utilities.batch_results import BatchResult
from src.config import get_logger
from src.domain.entities import OperationResult
from src.domain.entities.progress import (
    NullProgressEmitter,
    ProgressEmitter,
    ProgressOperation,
)
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

    Contains import statistics, timing data, and optional batch processing metadata
    for monitoring and debugging import operations.

    Attributes:
        operation_result: Core import statistics and error details.
        service: Music service that was imported from.
        mode: Import mode that was executed.
        execution_time_ms: Total time taken for import in milliseconds.
        total_batches: Number of processing batches used.
        batch_result: Optional detailed batch processing results from BatchProcessor.
        progress_operation: Optional progress tracking operation for real-time updates.
    """

    operation_result: OperationResult
    service: ServiceType
    mode: ImportMode
    execution_time_ms: int = 0

    # Batch processing metadata (for SQLite optimization)
    total_batches: int = 0

    # Optional integration with existing batch processing utilities
    batch_result: BatchResult | None = None
    progress_operation: ProgressOperation | None = None

    @property
    def success_rate(self) -> float:
        """Returns import success rate as percentage (0-100)."""
        # Extract success_rate from summary metrics
        for metric in self.operation_result.summary_metrics.metrics:
            if metric.name == "success_rate":
                return metric.value
        return 0.0


@define(slots=True)
class ImportTracksUseCase:
    """Downloads and stores listening history from music services.

    Orchestrates importing play data from LastFM API (recent/incremental/full history)
    or Spotify data export files into local database with transaction management.
    """

    async def execute(
        self,
        command: ImportTracksCommand,
        uow: UnitOfWorkProtocol,
        progress_emitter: ProgressEmitter | None = None,
    ) -> ImportTracksResult:
        """Downloads listening history from specified music service.

        Args:
            command: Import configuration with service, mode, and parameters.
            uow: Database transaction manager for atomic operations.
            progress_emitter: Optional progress emitter (defaults to null implementation)

        Returns:
            Import statistics including tracks imported, timing, and error details.

        Raises:
            ValueError: If service/mode combination is unsupported or params missing.
        """
        if progress_emitter is None:
            progress_emitter = NullProgressEmitter()

        import time

        start_time = time.time()

        with logger.contextualize(
            operation="import_tracks_use_case",
            service=command.service,
            mode=command.mode,
        ):
            try:
                # Delegate to appropriate import strategy
                operation_result = await self._execute_import(
                    command, uow, progress_emitter
                )

                execution_time_ms = int((time.time() - start_time) * 1000)

                # Extract imported count from summary metrics
                imported_count = self._get_metric_value(operation_result, "track_plays", 0)

                logger.info(
                    f"Successfully completed {command.service} {command.mode} import: "
                    f"{imported_count} tracks imported"
                )

                return ImportTracksResult(
                    operation_result=operation_result,
                    service=command.service,
                    mode=command.mode,
                    execution_time_ms=execution_time_ms,
                    total_batches=1,
                )

            except Exception as e:
                execution_time_ms = int((time.time() - start_time) * 1000)
                error_msg = f"{command.service} {command.mode} import failed: {e}"
                logger.error(error_msg)

                # Return failed result instead of raising
                failed_result = OperationResult(
                    operation_name=f"{command.service.title()} {command.mode.title()} Import",
                    execution_time=execution_time_ms / 1000.0,
                )
                failed_result.summary_metrics.add("errors", 1, "Errors", significance=1)
                failed_result.metadata["error"] = str(e)

                return ImportTracksResult(
                    operation_result=failed_result,
                    service=command.service,
                    mode=command.mode,
                    execution_time_ms=execution_time_ms,
                    total_batches=1,
                )

    async def _execute_import(
        self,
        command: ImportTracksCommand,
        uow: UnitOfWorkProtocol,
        progress_emitter: ProgressEmitter,
    ) -> OperationResult:
        """Routes to service-specific import handler based on command."""
        match command.service:
            case "lastfm":
                return await self._run_lastfm_import(command, uow, progress_emitter)
            case "spotify":
                return await self._run_spotify_import(command, uow, progress_emitter)
            case _:
                raise ValueError(f"Unknown service: {command.service}")

    async def _run_lastfm_import(
        self,
        command: ImportTracksCommand,
        uow: UnitOfWorkProtocol,
        progress_emitter: ProgressEmitter,
    ) -> OperationResult:
        """Routes to LastFM import mode handler (recent/incremental/full).

        Note: All modes now use the unified daily chunking approach in infrastructure.
        Mode differences are primarily application-layer concerns (confirmation,
        checkpoint reset, parameter mapping).
        """
        match command.mode:
            case "recent":
                return await self._run_lastfm_recent(command, uow, progress_emitter)
            case "incremental":
                return await self._run_lastfm_incremental(
                    command, uow, progress_emitter
                )
            case "full":
                return await self._run_lastfm_full_history(
                    command, uow, progress_emitter
                )
            case _:
                raise ValueError(f"LastFM service doesn't support mode: {command.mode}")

    async def _run_spotify_import(
        self,
        command: ImportTracksCommand,
        uow: UnitOfWorkProtocol,
        progress_emitter: ProgressEmitter,
    ) -> OperationResult:
        """Routes to Spotify import handler (file mode only)."""
        match command.mode:
            case "file":
                return await self._run_spotify_file(command, uow, progress_emitter)
            case _:
                raise ValueError(
                    f"Spotify service doesn't support mode: {command.mode}"
                )

    @staticmethod
    def _get_metric_value(
        result: OperationResult, metric_name: str, default: float = 0
    ) -> int | float:
        """Extract metric value from summary metrics by name."""
        for metric in result.summary_metrics.metrics:
            if metric.name == metric_name:
                return metric.value
        return default

    async def _create_play_import_orchestrator(self):
        """Create play import orchestrator for two-phase workflow.

        Returns:
            Configured PlayImportOrchestrator instance for coordinating ingestion and resolution.
        """
        from src.application.services.play_import_orchestrator import (
            PlayImportOrchestrator,
        )

        return PlayImportOrchestrator()

    async def _create_service_importer(self, service: str, uow: UnitOfWorkProtocol):
        """Create service-specific importer using infrastructure registry.

        CLEAN ARCHITECTURE: Application layer never mentions specific connectors.
        Uses infrastructure registry to map service names to implementations.

        Args:
            service: Generic service identifier (handled by infrastructure layer)
            uow: Database transaction manager providing repository access.

        Returns:
            Service-specific importer implementing PlayImporterProtocol
        """
        from src.infrastructure.services.play_import_registry import (
            get_play_import_registry,
        )

        registry = get_play_import_registry()
        return await registry.create_play_importer(service, uow)

    async def _run_lastfm_recent(
        self,
        command: ImportTracksCommand,
        uow: UnitOfWorkProtocol,
        progress_emitter: ProgressEmitter,
    ) -> OperationResult:
        """Downloads recent plays using two-phase workflow.

        Phase 1: Ingests raw play data as connector_plays
        Phase 2: Resolves connector_plays to canonical track_plays

        CLEAN ARCHITECTURE: No mention of specific connectors - uses generic service pattern.

        Args:
            command: Contains limit (default 1000).
            uow: Database transaction manager for atomic operations.

        Returns:
            Import statistics with number of plays downloaded and resolved to canonical tracks.
        """
        limit = command.limit or 1000

        # Create generic service importer and orchestrator
        importer = await self._create_service_importer(command.service, uow)
        orchestrator = await self._create_play_import_orchestrator()

        try:
            # Execute two-phase import: ingestion then resolution
            result = await orchestrator.import_plays_two_phase(
                importer=importer,
                uow=uow,
                progress_emitter=progress_emitter,
                limit=limit,  # Passed to ingestion phase
            )

            logger.info(
                f"Recent play two-phase import completed: {self._get_metric_value(result, 'track_plays')} track plays created"
            )
            return result

        except Exception as e:
            logger.error(f"Recent play import failed: {e}")
            raise

    async def _run_lastfm_incremental(
        self,
        command: ImportTracksCommand,
        uow: UnitOfWorkProtocol,
        progress_emitter: ProgressEmitter,
    ) -> OperationResult:
        """Downloads new plays since last sync using two-phase workflow.

        Phase 1: Ingests raw play data as connector_plays
        Phase 2: Resolves connector_plays to canonical track_plays

        Uses stored checkpoint to only fetch plays added since previous import.
        More efficient than full history import for regular syncing.

        CLEAN ARCHITECTURE: No mention of specific connectors - uses generic service pattern.

        Args:
            command: Contains user_id, from_date, to_date.
            uow: Database transaction manager for atomic operations.

        Returns:
            Import statistics with number of new plays resolved to canonical tracks.
        """
        # Create generic service importer and orchestrator
        importer = await self._create_service_importer(command.service, uow)
        orchestrator = await self._create_play_import_orchestrator()

        try:
            # Execute two-phase import: ingestion then resolution
            result = await orchestrator.import_plays_two_phase(
                importer=importer,
                uow=uow,
                progress_emitter=progress_emitter,
                username=command.user_id,
                from_date=command.from_date,
                to_date=command.to_date,
            )

            logger.info(
                f"Incremental two-phase import completed: {self._get_metric_value(result, 'track_plays')} track plays created"
            )
            return result

        except Exception as e:
            logger.error(f"Incremental import failed: {e}")
            raise

    async def _run_lastfm_full_history(
        self,
        command: ImportTracksCommand,
        uow: UnitOfWorkProtocol,
        progress_emitter: ProgressEmitter,
    ) -> OperationResult:
        """Downloads complete LastFM listening history using two-phase workflow.

        Phase 1: Ingests entire play history as connector_plays
        Phase 2: Resolves connector_plays to canonical track_plays

        Imports entire play history from account creation to present. Resets sync
        checkpoint and prompts for confirmation due to large API usage.

        Args:
            command: Contains user_id and confirm flags.
            uow: Database transaction manager for atomic operations.

        Returns:
            Import statistics with total plays resolved to canonical tracks or cancellation result.
        """
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
                result = OperationResult(
                    operation_name="Last.fm Full History Import",
                    execution_time=0.0,
                )
                result.metadata["cancelled"] = True
                result.summary_metrics.add("status", 0, Cancelled, significance=0)
                return result

        # Create generic service importer and orchestrator
        importer = await self._create_service_importer(command.service, uow)
        orchestrator = await self._create_play_import_orchestrator()

        try:
            # Execute two-phase import: ingestion then resolution
            # NOTE: Checkpoint reset logic moved to service-specific importers for clean architecture
            result = await orchestrator.import_plays_two_phase(
                importer=importer,
                uow=uow,
                progress_emitter=progress_emitter,
                limit=50000,  # Passed to ingestion phase
            )

            logger.info(
                f"Full history two-phase import completed: {self._get_metric_value(result, 'track_plays')} track plays created"
            )
            return result

        except Exception as e:
            logger.error(f"Full history import failed: {e}")
            raise

    async def _run_spotify_file(
        self,
        command: ImportTracksCommand,
        uow: UnitOfWorkProtocol,
        progress_emitter: ProgressEmitter,
    ) -> OperationResult:
        """Processes Spotify data export JSON file using two-phase workflow.

        Phase 1: Ingests raw Spotify export data as connector_plays
        Phase 2: Resolves connector_plays to canonical track_plays

        CLEAN ARCHITECTURE: No mention of specific connectors - uses generic service pattern.

        Args:
            command: Contains file_path to Spotify JSON export file.
            uow: Database transaction manager for atomic operations.

        Returns:
            Import statistics with number of plays resolved to canonical tracks.

        Raises:
            ValueError: If file_path is missing.
        """
        if not command.file_path:
            raise ValueError("file_path is required for Spotify file imports")

        # Create generic service importer and orchestrator
        importer = await self._create_service_importer(command.service, uow)
        orchestrator = await self._create_play_import_orchestrator()

        try:
            # Execute two-phase import: ingestion then resolution
            result = await orchestrator.import_plays_two_phase(
                importer=importer,
                uow=uow,
                progress_emitter=progress_emitter,
                file_path=command.file_path,
            )

            logger.info(
                f"File two-phase import completed: {self._get_metric_value(result, 'track_plays')} track plays created"
            )
            return result

        except Exception as e:
            logger.error(f"File import failed: {e}")
            raise


async def run_import(
    service: ServiceType,
    mode: ImportMode,
    limit: int | None = None,
    user_id: str | None = None,
    file_path: Path | None = None,
    confirm: bool = False,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    progress_emitter: ProgressEmitter | None = None,
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
        progress_emitter: Optional progress emitter (defaults to null implementation)
        **additional_options: Additional service-specific options.

    Returns:
        Import statistics including tracks imported and error details.

    Raises:
        ValueError: If service or mode combination is not supported.
    """
    if progress_emitter is None:
        progress_emitter = NullProgressEmitter()

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
        result = await use_case.execute(command, uow, progress_emitter)
        return result.operation_result
