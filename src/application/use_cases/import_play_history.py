"""Downloads listening history from the Last.fm and Spotify APIs and data exports.

Imports play data from music services into local database with progress tracking,
error handling, and transaction management. Supports Last.fm recent/incremental/full
history imports, Spotify JSON export processing, and Spotify recently-played API
polling (v0.10.1).
"""

from datetime import datetime
from pathlib import Path
from typing import Literal

from attrs import define, field

from src.application.utilities.timing import ExecutionTimer
from src.config import get_logger
from src.config.constants import BusinessLimits
from src.config.logging import logging_context
from src.domain.entities import OperationResult
from src.domain.entities.progress import NullProgressEmitter, ProgressEmitter
from src.domain.exceptions import (
    AppleMusicAuthRequiredError,
    LastfmAuthRequiredError,
    SpotifyAuthRequiredError,
    SpotifyQuotaExhaustedError,
)
from src.domain.repositories.play import (
    RECENTLY_PLAYED_PAGE_LIMIT,
    AppleRecentImportParams,
    ImportKind,
    LastfmImportParams,
    PlayImporterProtocol,
    PlayImportParams,
    SpotifyImportParams,
    SpotifyRecentImportParams,
)
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)

ServiceType = Literal["lastfm", "spotify", "apple"]
ImportMode = Literal["recent", "incremental", "full", "file"]


@define(frozen=True, slots=True)
class ImportTracksCommand:
    """Configuration for importing tracks from music services.

    Validates service/mode combinations and ensures required parameters are present.
    Supports LastFM API imports (recent/incremental/full) and Spotify file imports.

    Attributes:
        user_id: Authenticated user's ID (for data scoping).
        service: Music service to import from ('lastfm' or 'spotify').
        mode: Import type ('recent', 'incremental', 'full', 'file').
        limit: Maximum tracks to import (LastFM only).
        username: LastFM username for user-specific imports.
        file_path: Path to Spotify data export JSON file.
        confirm: Whether user confirmed destructive operations.
        from_date: Start date for date range filtering (incremental mode only).
        to_date: End date for date range filtering (incremental mode only).
        additional_options: Extra service-specific parameters.

    Raises:
        ValueError: If service/mode combination is invalid or required params missing.
    """

    user_id: str
    service: ServiceType
    mode: ImportMode

    # Service-specific parameters
    limit: int | None = None  # For lastfm recent/full imports
    username: str | None = None  # For lastfm incremental/full imports
    file_path: Path | None = None  # For spotify file imports
    confirm: bool = False  # For destructive operations like full history
    from_date: datetime | None = None  # Start date for date range filtering
    to_date: datetime | None = None  # End date for date range filtering

    # Additional options for extensibility
    additional_options: dict[str, object] = field(factory=dict)

    def __attrs_post_init__(self) -> None:
        """Validates service and mode compatibility.

        Spotify accepts ``file`` (GDPR export upload) plus ``recent`` and
        ``incremental`` (recently-played API, v0.10.1). It does NOT accept
        ``full``: that endpoint retains only the trailing ~50 plays, so there is
        no "whole history" to ask for — earlier plays arrive via Last.fm or the
        next export.

        Raises:
            ValueError: If LastFM uses file mode, Spotify uses full mode, a
                Spotify file import has no file_path, or a Spotify API import
                was handed one.
        """
        if self.service == "lastfm":
            if self.mode == "file":
                raise ValueError("LastFM service doesn't support file mode")
        elif self.service == "spotify":
            if self.mode == "full":
                raise ValueError(
                    "Spotify doesn't support full mode: the recently-played API "
                    "retains only the trailing ~50 plays. Use mode='recent' for "
                    "live plays, or mode='file' with a data export for history."
                )
            if self.mode == "file" and not self.file_path:
                raise ValueError("file_path is required for Spotify file imports")
            if self.mode != "file" and self.file_path:
                raise ValueError(
                    f"file_path is not valid for Spotify {self.mode} imports "
                    f"(the API is the source, not a file)"
                )
        elif self.service == "apple":
            # Only the recently-played poll exists: Apple offers no export
            # file, and the feed retains only a trailing window, so there is
            # no "whole history" to ask for.
            if self.mode not in ("recent", "incremental"):
                raise ValueError(
                    f"Apple Music doesn't support mode: {self.mode}. The "
                    f"recently-played API is the only source — use "
                    f"mode='recent' or mode='incremental'."
                )
            if self.file_path:
                raise ValueError(
                    "file_path is not valid for Apple Music imports "
                    "(the API is the source, not a file)"
                )


@define(frozen=True, slots=True)
class ImportTracksResult:
    """Result from track import operation with performance metrics.

    Contains import statistics and timing data for monitoring and debugging
    import operations.

    Attributes:
        operation_result: Core import statistics and error details.
        service: Music service that was imported from.
        mode: Import mode that was executed.
        execution_time_ms: Total time taken for import in milliseconds.
    """

    operation_result: OperationResult
    service: ServiceType
    mode: ImportMode
    execution_time_ms: int = 0

    @property
    def success_rate(self) -> float:
        """Import success rate as a percentage (0-100)."""
        return float(self.operation_result.summary_metrics.get("success_rate"))


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

        timer = ExecutionTimer()

        with logging_context(
            operation="import_tracks_use_case",
            service=command.service,
            mode=command.mode,
        ):
            try:
                # Delegate to appropriate import strategy
                operation_result = await self._execute_import(
                    command, uow, progress_emitter
                )

                self._log_outcome(command, operation_result)

                return ImportTracksResult(
                    operation_result=operation_result,
                    service=command.service,
                    mode=command.mode,
                    execution_time_ms=timer.stop(),
                )

            except (
                AppleMusicAuthRequiredError,
                LastfmAuthRequiredError,
                SpotifyAuthRequiredError,
                SpotifyQuotaExhaustedError,
            ):
                # Connector-not-connected is a clean precondition, not a soft
                # failure — let it propagate so the SSE seam emits a terminal
                # error (and the 409 middleware handler maps it for sync callers).
                # Converting it to an is_failure result would bury the connect hint.
                # Quota exhaustion (PDR-003) propagates the same way: its 503
                # handler and message are the only honest surface for it.
                raise

            except Exception as e:
                error_msg = f"{command.service} {command.mode} import failed: {e}"
                logger.error(error_msg)

                execution_time_ms = timer.stop()

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
                )

    @staticmethod
    def _log_outcome(
        command: ImportTracksCommand, operation_result: OperationResult
    ) -> None:
        """Log the import outcome honestly.

        The base importer converts pipeline exceptions into a soft-failure
        result without raising, so reaching this point is NOT proof of
        success — logging it as such sent log readers chasing a phantom
        (the v0.10.2.2 diagnosis).
        """
        if operation_result.is_failure:
            logger.error(
                f"{command.service} {command.mode} import failed: "
                f"{operation_result.failure_message}"
            )
        else:
            imported_count = operation_result.summary_metrics.get("track_plays")
            logger.info(
                f"Successfully completed {command.service} {command.mode} "
                f"import: {imported_count} tracks imported"
            )

    async def _execute_import(
        self,
        command: ImportTracksCommand,
        uow: UnitOfWorkProtocol,
        progress_emitter: ProgressEmitter,
    ) -> OperationResult:
        """Applies per-branch guards, builds import params, runs the two-phase import.

        Every branch ends in the same two-phase workflow (``_run_two_phase``);
        only the guards and the params object differ per service and mode.
        """
        match (command.service, command.mode):
            case ("lastfm", "recent"):
                params: PlayImportParams = LastfmImportParams(
                    limit=command.limit or 1000
                )
            case ("lastfm", "incremental"):
                params = LastfmImportParams(
                    username=command.username,
                    from_date=command.from_date,
                    to_date=command.to_date,
                )
            case ("lastfm", "full"):
                # Confirmation UI is the caller's responsibility: an
                # unconfirmed full history import is cancelled, not run.
                if not command.confirm:
                    cancelled = OperationResult(
                        operation_name="Last.fm Full History Import",
                        execution_time=0.0,
                    )
                    cancelled.metadata["cancelled"] = True
                    cancelled.summary_metrics.add(
                        "status", 0, "Cancelled", significance=0
                    )
                    return cancelled
                params = LastfmImportParams(limit=50000)
            case ("lastfm", _):
                raise ValueError(f"LastFM service doesn't support mode: {command.mode}")
            case ("spotify", "file"):
                if not command.file_path:
                    raise ValueError("file_path is required for Spotify file imports")
                return await self._run_two_phase(
                    command,
                    uow,
                    progress_emitter,
                    params=SpotifyImportParams(file_path=Path(command.file_path)),
                    kind="file",
                )
            case ("spotify", "recent" | "incremental"):
                # Deliberately one branch: the stored cursor makes every poll
                # incremental, so "recent" and "incremental" cannot differ here.
                # Both are accepted so each caller can use the word that fits
                # (the UI says recent, a scheduled sync says incremental).
                #
                # Clamped on both ends: a caller asking for more than the
                # endpoint retains cannot get it, and a zero/negative limit
                # would otherwise reach Spotify verbatim and come back as an
                # opaque transport failure.
                requested = command.limit or RECENTLY_PLAYED_PAGE_LIMIT
                limit = min(max(requested, 1), RECENTLY_PLAYED_PAGE_LIMIT)
                params = SpotifyRecentImportParams(
                    limit=limit,
                    force=bool(command.additional_options.get("force")),
                )
            case ("spotify", _):
                raise ValueError(
                    f"Spotify service doesn't support mode: {command.mode}"
                )
            case ("apple", _):
                # Only recent/incremental reach here (the command validator
                # rejects the rest) and they are identical: the stored window
                # fingerprint makes every poll incremental. No limit either —
                # the endpoint's page size is fixed and the importer's
                # prefix-diff, not a count, bounds the ingest.
                params = AppleRecentImportParams(
                    force=bool(command.additional_options.get("force"))
                )

        return await self._run_two_phase(command, uow, progress_emitter, params=params)

    async def _create_play_import_orchestrator(self, uow: UnitOfWorkProtocol):
        """Create play import orchestrator for two-phase workflow.

        Returns:
            Configured PlayImportOrchestrator instance for coordinating ingestion and resolution.
        """
        from src.application.services.play_import_orchestrator import (
            PlayImportOrchestrator,
        )

        provider = uow.get_play_import_provider()
        return PlayImportOrchestrator(resolver_factory=provider.create_play_resolver)

    async def _create_service_importer(
        self,
        service: str,
        uow: UnitOfWorkProtocol,
        kind: ImportKind = "api",
    ) -> PlayImporterProtocol:
        """Create service-specific importer via the UoW's import provider.

        The application layer never mentions specific connectors: the provider
        (``PlayImportProvider``) maps service names to implementations, and
        both its return types are domain protocols.

        Args:
            service: Generic service identifier (resolved by the provider)
            uow: Database transaction manager providing repository access.
            kind: Where the data comes from — a live API read or an uploaded
                export file. One service can offer both (Spotify does).

        Returns:
            Service-specific importer implementing PlayImporterProtocol
        """
        return await uow.get_play_import_provider().create_play_importer(
            service, kind, uow
        )

    async def _run_two_phase(
        self,
        command: ImportTracksCommand,
        uow: UnitOfWorkProtocol,
        progress_emitter: ProgressEmitter,
        *,
        params: PlayImportParams,
        kind: ImportKind = "api",
    ) -> OperationResult:
        """Runs the two-phase import shared by every service and mode.

        Phase 1 ingests raw plays as connector_plays; phase 2 resolves them to
        canonical track_plays. The application layer names no connector: the
        service string and ``kind`` select the importer through the provider.

        Args:
            command: Import configuration (service, mode, user).
            uow: Database transaction manager for atomic operations.
            progress_emitter: Emitter for real-time progress updates.
            params: Importer-specific frozen import selectors.
            kind: Live API read or uploaded export file.

        Returns:
            Import statistics with plays ingested and resolved to canonical tracks.

        Raises:
            Exception: Any importer or resolver failure, logged then re-raised.
        """
        importer = await self._create_service_importer(command.service, uow, kind)
        orchestrator = await self._create_play_import_orchestrator(uow)

        try:
            result = await orchestrator.import_plays_two_phase(
                importer=importer,
                uow=uow,
                user_id=command.user_id,
                progress_emitter=progress_emitter,
                params=params,
            )

            logger.info(
                "Two-phase import completed",
                service=command.service,
                mode=command.mode,
                track_plays=result.summary_metrics.get("track_plays"),
            )

        except Exception as e:
            logger.error(
                "Two-phase import failed",
                service=command.service,
                mode=command.mode,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True,
            )
            raise
        else:
            return result


async def run_import(
    user_id: str,
    service: ServiceType,
    mode: ImportMode,
    limit: int | None = None,
    username: str | None = None,
    file_path: Path | None = None,
    confirm: bool = False,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    progress_emitter: ProgressEmitter | None = None,
    **additional_options: object,
) -> OperationResult:
    """Downloads listening history from music services.

    Convenience function that creates import command, manages database session,
    and executes the import operation. Used by CLI commands.

    Args:
        user_id: Authenticated user's ID (for data scoping).
        service: Import service type ('lastfm' or 'spotify').
        mode: Import mode ('recent', 'incremental', 'full', 'file').
        limit: Maximum number of items to import.
        username: LastFM username for user-specific imports.
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

    from src.application.runner import execute_use_case

    command = ImportTracksCommand(
        user_id=user_id,
        service=service,
        mode=mode,
        limit=limit,
        username=username,
        file_path=file_path,
        confirm=confirm,
        from_date=from_date,
        to_date=to_date,
        additional_options=additional_options,
    )

    result = await execute_use_case(
        lambda uow: ImportTracksUseCase().execute(command, uow, progress_emitter),
        user_id=user_id,
        statement_timeout=BusinessLimits.BULK_IMPORT_STATEMENT_TIMEOUT,
    )
    return result.operation_result
