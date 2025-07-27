"""Import orchestrator service providing unified interface for all import operations.

Handles business logic, validation, progress reporting, and error handling
for importing tracks from external services. Provides a unified interface
for different import modes and service types.
"""

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
    """Command for track import operations.

    Encapsulates all context needed for importing tracks from external services
    with proper validation and service-specific configuration.
    """

    service: ServiceType
    mode: ImportMode

    # Service-specific parameters
    limit: int | None = None  # For lastfm recent/full imports
    resolve_tracks: bool = False  # Whether to resolve track identities
    user_id: str | None = None  # For lastfm incremental/full imports
    file_path: Path | None = None  # For spotify file imports
    confirm: bool = False  # For destructive operations like full history

    # Additional options for extensibility
    additional_options: dict[str, Any] = field(factory=dict)

    def __attrs_post_init__(self) -> None:
        """Validate command parameters based on service and mode."""
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
    """Result of track import operation.

    Contains imported tracks and operation metadata for monitoring
    and downstream processing.
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
        """Number of tracks successfully imported."""
        return self.operation_result.imported_count or 0

    @property
    def success_rate(self) -> float:
        """Success rate as percentage."""
        return self.operation_result.success_rate or 0.0


@define(slots=True)
class ImportTracksUseCase:
    """Use case for importing tracks from external services.

    Implements Clean Architecture with UnitOfWork pattern for proper
    transaction boundaries. Designed for SQLite optimization with
    batch processing to avoid lock issues on large imports.

    Architectural principles:
    - No constructor dependencies (pure domain layer)
    - Explicit transaction control through UnitOfWork parameter
    - SQLite-friendly batch processing (50-100 tracks per transaction)
    - Unified interface for all import services and modes

    Supported Services:
    - LastFM: recent, incremental, full history imports
    - Spotify: JSON file imports
    """

    async def execute(
        self, command: ImportTracksCommand, uow: UnitOfWorkProtocol
    ) -> ImportTracksResult:
        """Execute track import operation.

        Args:
            command: Rich command with operation context and configuration.
            uow: UnitOfWork for transaction management and repository access.

        Returns:
            Result containing import statistics and operation metadata.

        Raises:
            ValueError: For invalid service/mode combinations or missing parameters
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
                operation_result = await self._execute_import(command)

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

    async def _execute_import(self, command: ImportTracksCommand) -> OperationResult:
        """Execute the appropriate import strategy based on service and mode."""
        match command.service:
            case "lastfm":
                return await self._run_lastfm_import(command)
            case "spotify":
                return await self._run_spotify_import(command)
            case _:
                raise ValueError(f"Unknown service: {command.service}")

    async def _run_lastfm_import(self, command: ImportTracksCommand) -> OperationResult:
        """Handle LastFM import operations."""
        match command.mode:
            case "recent":
                return await self._run_lastfm_recent(command)
            case "incremental":
                return await self._run_lastfm_incremental(command)
            case "full":
                return await self._run_lastfm_full_history(command)
            case _:
                raise ValueError(f"LastFM service doesn't support mode: {command.mode}")

    async def _run_spotify_import(
        self, command: ImportTracksCommand
    ) -> OperationResult:
        """Handle Spotify import operations."""
        match command.mode:
            case "file":
                return await self._run_spotify_file(command)
            case _:
                raise ValueError(
                    f"Spotify service doesn't support mode: {command.mode}"
                )

    async def _run_lastfm_recent(self, command: ImportTracksCommand) -> OperationResult:
        """Run LastFM recent plays import with UnitOfWork pattern.

        Implements SQLite-optimized batch processing with proper transaction boundaries
        to prevent lock issues during large imports.
        """
        limit = command.limit or 1000
        resolve_tracks = command.resolve_tracks

        from src.infrastructure.connectors.lastfm import LastFMConnector
        from src.infrastructure.persistence.database import get_session
        from src.infrastructure.persistence.repositories.factories import (
            get_unit_of_work,
        )
        from src.infrastructure.services.lastfm_import import LastfmImportService

        # Use session-per-operation pattern with UnitOfWork
        async with get_session() as session:
            uow = get_unit_of_work(session)

            async with uow:
                # Get repositories from UnitOfWork for Clean Architecture compliance
                plays_repo = uow.get_plays_repository()
                checkpoint_repo = uow.get_checkpoint_repository()
                connector_repo = uow.get_connector_repository()
                track_repo = uow.get_track_repository()

                # Create import service with UoW-provided repositories
                lastfm_service = LastfmImportService(
                    plays_repository=plays_repo,
                    checkpoint_repository=checkpoint_repo,
                    connector_repository=connector_repo,
                    track_repository=track_repo,
                    lastfm_connector=LastFMConnector(),
                )

                try:
                    # Execute import with explicit transaction control
                    if resolve_tracks:
                        result = (
                            await lastfm_service.import_recent_plays_with_resolution(
                                limit=limit
                            )
                        )
                    else:
                        result = await lastfm_service.import_recent_plays(limit=limit)

                    # Explicit commit after successful import
                    await uow.commit()

                    logger.info(
                        f"LastFM recent import completed: {result.imported_count} plays imported"
                    )
                    return result

                except Exception as e:
                    # Explicit rollback on error - UoW context manager will also handle this
                    await uow.rollback()
                    logger.error(f"LastFM recent import failed: {e}")
                    raise

    async def _run_lastfm_incremental(
        self, command: ImportTracksCommand
    ) -> OperationResult:
        """Run LastFM incremental import with UnitOfWork pattern.

        Implements SQLite-optimized incremental import with checkpoint management
        and proper transaction boundaries.
        """
        user_id = command.user_id
        resolve_tracks = command.resolve_tracks

        from src.infrastructure.connectors.lastfm import LastFMConnector
        from src.infrastructure.persistence.database import get_session
        from src.infrastructure.persistence.repositories.factories import (
            get_unit_of_work,
        )
        from src.infrastructure.services.lastfm_import import LastfmImportService

        # Use session-per-operation pattern with UnitOfWork
        async with get_session() as session:
            uow = get_unit_of_work(session)

            async with uow:
                # Get repositories from UnitOfWork for Clean Architecture compliance
                plays_repo = uow.get_plays_repository()
                checkpoint_repo = uow.get_checkpoint_repository()
                connector_repo = uow.get_connector_repository()
                track_repo = uow.get_track_repository()

                # Create import service with UoW-provided repositories
                lastfm_service = LastfmImportService(
                    plays_repository=plays_repo,
                    checkpoint_repository=checkpoint_repo,
                    connector_repository=connector_repo,
                    track_repository=track_repo,
                    lastfm_connector=LastFMConnector(),
                )

                try:
                    # Execute incremental import with explicit transaction control
                    result = await lastfm_service.import_incremental_plays(
                        user_id=user_id, resolve_tracks=resolve_tracks
                    )

                    # Explicit commit after successful import
                    await uow.commit()

                    logger.info(
                        f"LastFM incremental import completed: {result.imported_count} plays imported"
                    )
                    return result

                except Exception as e:
                    # Explicit rollback on error - UoW context manager will also handle this
                    await uow.rollback()
                    logger.error(f"LastFM incremental import failed: {e}")
                    raise

    async def _run_lastfm_full_history(
        self, command: ImportTracksCommand
    ) -> OperationResult:
        """Run LastFM full history import with UnitOfWork pattern.

        Implements SQLite-optimized full history import with checkpoint reset
        and proper transaction boundaries for large imports.
        """
        user_id = command.user_id
        resolve_tracks = command.resolve_tracks
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
                    skipped_count=0,
                    error_count=0,
                )

        from src.infrastructure.connectors.lastfm import LastFMConnector
        from src.infrastructure.persistence.database import get_session
        from src.infrastructure.persistence.repositories.factories import (
            get_unit_of_work,
        )
        from src.infrastructure.services.lastfm_import import LastfmImportService

        # Use session-per-operation pattern with UnitOfWork
        async with get_session() as session:
            uow = get_unit_of_work(session)

            async with uow:
                # Get repositories from UnitOfWork for Clean Architecture compliance
                plays_repo = uow.get_plays_repository()
                checkpoint_repo = uow.get_checkpoint_repository()
                connector_repo = uow.get_connector_repository()
                track_repo = uow.get_track_repository()

                # Create import service with UoW-provided repositories
                lastfm_service = LastfmImportService(
                    plays_repository=plays_repo,
                    checkpoint_repository=checkpoint_repo,
                    connector_repository=connector_repo,
                    track_repository=track_repo,
                    lastfm_connector=LastFMConnector(),
                )

                try:
                    # Reset checkpoint before full import
                    username = (
                        user_id or lastfm_service.lastfm_connector.lastfm_username
                    )
                    if username:
                        await self._reset_lastfm_checkpoint_uow(username, uow)

                    # Execute full history import with explicit transaction control
                    # Use large limit for full history (API will stop when no more data)
                    if resolve_tracks:
                        result = (
                            await lastfm_service.import_recent_plays_with_resolution(
                                limit=50000
                            )
                        )
                    else:
                        result = await lastfm_service.import_recent_plays(limit=50000)

                    # Explicit commit after successful import
                    await uow.commit()

                    logger.info(
                        f"LastFM full history import completed: {result.imported_count} plays imported"
                    )
                    return result

                except Exception as e:
                    # Explicit rollback on error - UoW context manager will also handle this
                    await uow.rollback()
                    logger.error(f"LastFM full history import failed: {e}")
                    raise

    async def _run_spotify_file(self, command: ImportTracksCommand) -> OperationResult:
        """Run Spotify file import with UnitOfWork pattern.

        Implements SQLite-optimized file import with proper transaction boundaries
        for processing large JSON files.
        """
        if not command.file_path:
            raise ValueError("file_path is required for Spotify file imports")

        file_path = command.file_path

        # File validation
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        if not file_path.is_file():
            raise ValueError(f"Path is not a file: {file_path}")

        from src.infrastructure.persistence.database import get_session
        from src.infrastructure.persistence.repositories.factories import (
            get_unit_of_work,
        )
        from src.infrastructure.services.spotify_import import SpotifyImportService

        # Use session-per-operation pattern with UnitOfWork
        async with get_session() as session:
            uow = get_unit_of_work(session)

            async with uow:
                # Get repositories from UnitOfWork for Clean Architecture compliance
                plays_repo = uow.get_plays_repository()
                connector_repo = uow.get_connector_repository()

                # Create import service with UoW-provided repositories
                spotify_service = SpotifyImportService(
                    plays_repository=plays_repo,
                    connector_repository=connector_repo,
                )

                try:
                    # Execute file import with explicit transaction control
                    result = await spotify_service.import_from_file(file_path)

                    # Explicit commit after successful import
                    await uow.commit()

                    logger.info(
                        f"Spotify file import completed: {result.imported_count} plays imported from {file_path}"
                    )
                    return result

                except Exception as e:
                    # Explicit rollback on error - UoW context manager will also handle this
                    await uow.rollback()
                    logger.error(f"Spotify file import failed: {e}")
                    raise

    async def _reset_lastfm_checkpoint_uow(
        self, username: str, uow: UnitOfWorkProtocol
    ) -> None:
        """Reset Last.fm checkpoint using UnitOfWork pattern."""
        from src.domain.entities import SyncCheckpoint

        # Create a new checkpoint with no timestamp (forces full import)
        checkpoint = SyncCheckpoint(
            user_id=username, service="lastfm", entity_type="plays", last_timestamp=None
        )

        # Use UnitOfWork's checkpoint repository
        checkpoint_repo = uow.get_checkpoint_repository()
        await checkpoint_repo.save_sync_checkpoint(checkpoint)

        logger.info(f"Reset Last.fm checkpoint for user {username} via UoW")


async def run_import(
    service: ServiceType,
    mode: ImportMode,
    limit: int | None = None,
    resolve_tracks: bool = False,
    user_id: str | None = None,
    file_path: Path | None = None,
    confirm: bool = False,
    **additional_options,
) -> OperationResult:
    """Application layer interface for import operations.

    Provides a clean boundary between CLI and use case layers by handling
    command object creation, use case instantiation, and session management.
    This follows Clean Architecture patterns for interface layers.

    Args:
        service: Import service type ('lastfm' or 'spotify')
        mode: Import mode ('recent', 'incremental', 'full', 'file')
        limit: Maximum number of items to import
        resolve_tracks: Whether to resolve track identities
        user_id: User ID for the import operation
        file_path: File path for file-based imports
        confirm: Whether to confirm before importing
        **additional_options: Additional service-specific options

    Returns:
        OperationResult with import statistics and status

    Raises:
        ValueError: If service or mode combination is not supported
    """
    from src.infrastructure.persistence.database.db_connection import get_session
    from src.infrastructure.persistence.repositories.factories import get_unit_of_work

    async with get_session() as session:
        uow = get_unit_of_work(session)
        command = ImportTracksCommand(
            service=service,
            mode=mode,
            limit=limit,
            resolve_tracks=resolve_tracks,
            user_id=user_id,
            file_path=file_path,
            confirm=confirm,
            additional_options=additional_options,
        )

        use_case = ImportTracksUseCase()
        result = await use_case.execute(command, uow)
        return result.operation_result
