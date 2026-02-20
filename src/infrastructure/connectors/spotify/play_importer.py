"""Spotify-specific play importer implementing connector-only ingestion pattern.

MIGRATED from src/infrastructure/services/spotify_import_service.py to keep ALL Spotify
logic contained within the spotify connector directory. Contains sophisticated file
parsing, batch processing, and memory optimization logic.
"""

# Removed: from collections.abc import Callable - no longer needed for progress callbacks
from datetime import datetime
from pathlib import Path
from typing import Any, override

from src.application.services.play_import_orchestrator import PlayImporterProtocol

# Removed: ImportBatchProcessor - dead code, never used in implementation
from src.config import get_logger
from src.domain.entities import ConnectorTrackPlay, OperationResult
from src.domain.entities.progress import NullProgressEmitter, ProgressEmitter
from src.domain.repositories.interfaces import UnitOfWorkProtocol
from src.infrastructure.connectors.spotify.personal_data import (
    parse_spotify_personal_data,
)
from src.infrastructure.services.base_play_importer import (
    BasePlayImporter,
    SpotifyImportParams,
)

logger = get_logger(__name__)


class SpotifyPlayImporter(BasePlayImporter, PlayImporterProtocol):
    """Spotify-specific play importer with sophisticated file processing and batch logic.

    MIGRATED from services directory to maintain clean architecture boundaries.
    Implements PlayImporterProtocol for use with generic PlayImportOrchestrator.
    Contains ALL Spotify-specific logic: file parsing, batch processing, memory optimization.
    """

    def __init__(self) -> None:
        """Initialize Spotify play importer for connector-only ingestion pattern."""
        # Initialize base class with None since we only do connector ingestion
        super().__init__(None)
        self.operation_name = "Spotify Connector Play Import"

        # Note: Batch processing handled by base class methods with event-driven progress
        # All processing uses the new progress system via ProgressEmitter protocol

    async def import_plays(
        self,
        uow: UnitOfWorkProtocol,
        progress_emitter: ProgressEmitter | None = None,
        **params: Any,
    ) -> tuple[OperationResult, list[ConnectorTrackPlay]]:
        """Import Spotify plays as connector_plays for later resolution.

        Implements PlayImporterProtocol interface for use with generic orchestrator.

        Args:
            uow: Unit of work for database operations
            **params: Spotify-specific parameters (file_path, batch_size, etc.)

        Returns:
            Tuple of (operation_result, connector_plays_list)
        """
        if progress_emitter is None:
            progress_emitter = NullProgressEmitter()

        # Extract common and Spotify-specific parameters using typed approach
        common_params, spotify_params = self._extract_common_params(**params)
        typed_params: SpotifyImportParams = {**common_params, **spotify_params}  # type: ignore[misc]

        file_path = typed_params.get("file_path")
        if not file_path:
            raise ValueError("file_path is required for Spotify imports")

        logger.info(
            "Starting Spotify connector play ingestion with sophisticated processing",
            file_path=str(file_path),
            batch_size=typed_params.get("batch_size"),
        )

        # Use migrated sophisticated import logic with typed parameters
        result = await self._import_from_file_migrated(
            file_path=Path(file_path),  # Convert to Path object
            import_batch_id=typed_params.get("import_batch_id"),
            progress_emitter=progress_emitter,
            uow=uow,  # Use the UnitOfWork passed directly to import_plays
        )

        # Get the connector plays using base class method
        connector_plays = self._get_stored_connector_plays()

        logger.info(
            "Spotify connector play ingestion complete",
            connector_plays_ingested=len(connector_plays),
            canonical_plays_created=0,  # Zero - we only do ingestion
        )

        return result, connector_plays

    # === MIGRATED SOPHISTICATED LOGIC FROM ORIGINAL IMPORTER ===

    async def _import_from_file_migrated(
        self,
        file_path: Path,
        import_batch_id: str | None = None,
        progress_emitter: ProgressEmitter | None = None,
        uow: Any | None = None,
    ) -> OperationResult:
        """Import play data from Spotify JSON export file.

        MIGRATED from original SpotifyImportService with sophisticated processing.
        """
        if progress_emitter is None:
            progress_emitter = NullProgressEmitter()

        return await self.import_data(
            file_path=file_path,
            import_batch_id=import_batch_id,
            progress_emitter=progress_emitter,
            uow=uow,
        )

    @override
    async def _fetch_data(
        self,
        progress_emitter: ProgressEmitter | None = None,
        uow: Any | None = None,
        **kwargs,
    ) -> list[Any]:
        """Fetch and parse Spotify JSON export file.

        MIGRATED sophisticated file parsing logic from original importer.
        """
        # Mark unused parameters for base class compatibility
        _ = uow
        if progress_emitter is None:
            progress_emitter = NullProgressEmitter()

        # Extract required parameters
        file_path = kwargs.get("file_path")
        if not file_path:
            raise ValueError("file_path is required for Spotify file imports")

        # Validate file exists and is readable
        if not isinstance(file_path, Path):
            file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(f"Spotify export file not found: {file_path}")

        if not file_path.is_file():
            raise ValueError(f"Path is not a file: {file_path}")

        # Note: Progress reporting now handled by event-driven progress system
        # File parsing progress: 10%

        logger.info(f"📁 Parsing Spotify export file: {file_path}")

        # Parse Spotify personal data file directly using sophisticated parser
        try:
            raw_records = parse_spotify_personal_data(file_path)
            logger.info(
                "Parsed Spotify export",
                file_path=str(file_path),
                count=len(raw_records),
            )
        except Exception as e:
            logger.error(
                "Failed to parse Spotify export file",
                file_path=str(file_path),
                error=str(e),
            )
            raise

        # Note: Progress reporting now handled by event-driven progress system
        # File parsing complete: parsed {len(raw_records)} records

        logger.info(f"Parsed {len(raw_records)} records from Spotify file: {file_path}")
        return raw_records

    @override
    async def _process_data(
        self,
        raw_data: list[Any],
        batch_id: str,
        import_timestamp: datetime,
        progress_emitter: ProgressEmitter | None = None,
        uow: Any | None = None,
        **kwargs,
    ) -> list[ConnectorTrackPlay]:
        """Process raw Spotify data into ConnectorTrackPlay objects.

        MIGRATED: Uses existing SpotifyPlayAdapter logic but stores as connector_plays.
        """
        # Mark unused parameters for base class compatibility
        _ = uow, kwargs
        if progress_emitter is None:
            progress_emitter = NullProgressEmitter()

        if not raw_data:
            return []

        # Process raw data directly into ConnectorTrackPlay objects
        connector_plays = []
        for record in raw_data:
            # Extract Spotify-specific data from SpotifyPlayRecord attributes
            connector_play = ConnectorTrackPlay(
                service="spotify",
                track_name=record.track_name,
                artist_name=record.artist_name,
                album_name=record.album_name,
                played_at=record.timestamp,
                ms_played=record.ms_played,
                service_metadata={
                    "track_uri": record.track_uri,
                    "platform": record.platform,
                    "country": record.country,
                    "reason_start": record.reason_start,
                    "reason_end": record.reason_end,
                    "shuffle": record.shuffle,
                    "skipped": record.skipped,
                    "offline": record.offline,
                    "incognito_mode": record.incognito_mode,
                },
                api_page=None,  # Not applicable for file imports
                raw_data={
                    "timestamp": record.timestamp.isoformat(),
                    "track_uri": record.track_uri,
                    "track_name": record.track_name,
                    "artist_name": record.artist_name,
                    "album_name": record.album_name,
                    "ms_played": record.ms_played,
                    "platform": record.platform,
                    "country": record.country,
                    "reason_start": record.reason_start,
                    "reason_end": record.reason_end,
                    "shuffle": record.shuffle,
                    "skipped": record.skipped,
                    "offline": record.offline,
                    "incognito_mode": record.incognito_mode,
                },
                import_timestamp=import_timestamp,  # Use the provided import_timestamp
                import_source="spotify_export",
                import_batch_id=batch_id,  # Use the provided batch_id
            )
            connector_plays.append(connector_play)

        return connector_plays

    @override
    async def _save_data(
        self, data: list[Any], uow: UnitOfWorkProtocol | None = None
    ) -> tuple[int, int]:
        """Save connector plays using base class method for DRY compliance."""
        if not uow:
            raise RuntimeError("UnitOfWork required for Spotify connector play storage")

        # Use base class method to eliminate duplication
        return await self._save_connector_plays_via_uow(data, uow)

    @override
    async def _handle_checkpoints(
        self, raw_data: list[Any], uow: UnitOfWorkProtocol | None = None, **kwargs
    ) -> None:
        """Update sync checkpoints to track import progress for incremental syncs.

        Spotify-specific implementation. Since Spotify imports are file-based rather
        than API-based incremental syncs, checkpoints are not applicable.
        """
        # Spotify imports are file-based, so checkpoints are not applicable
