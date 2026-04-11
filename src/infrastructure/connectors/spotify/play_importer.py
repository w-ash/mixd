"""Spotify-specific play importer implementing connector-only ingestion pattern.

MIGRATED from src/infrastructure/services/spotify_import_service.py to keep ALL Spotify
logic contained within the spotify connector directory. Contains sophisticated file
parsing, batch processing, and memory optimization logic.
"""

# pyright: reportAny=false
# Legitimate Any: **kwargs variadic dispatch, SpotifyPlayRecord raw data

from datetime import datetime
from pathlib import Path
from typing import override

from src.config import get_logger
from src.domain.entities import ConnectorTrackPlay, OperationResult
from src.domain.entities.progress import NullProgressEmitter, ProgressEmitter
from src.domain.repositories import PlayImporterProtocol
from src.domain.repositories.interfaces import UnitOfWorkProtocol
from src.infrastructure.connectors.spotify.personal_data import (
    SpotifyPlayRecord,
    parse_spotify_personal_data,
)
from src.infrastructure.services.base_play_importer import (
    BasePlayImporter,
    SpotifyImportParams,
)

logger = get_logger(__name__)


class SpotifyPlayImporter(BasePlayImporter[SpotifyPlayRecord], PlayImporterProtocol):
    """Spotify-specific play importer with sophisticated file processing and batch logic.

    MIGRATED from services directory to maintain clean architecture boundaries.
    Implements PlayImporterProtocol for use with generic PlayImportOrchestrator.
    Contains ALL Spotify-specific logic: file parsing, batch processing, memory optimization.
    """

    operation_name: str

    def __init__(self) -> None:
        """Initialize Spotify play importer for connector-only ingestion pattern."""
        # Initialize base class with None since we only do connector ingestion
        super().__init__(None)
        self.operation_name = "Spotify Connector Play Import"

        # Note: Batch processing handled by base class methods with event-driven progress
        # All processing uses the new progress system via ProgressEmitter protocol

    @override
    async def import_plays(
        self,
        uow: UnitOfWorkProtocol,
        progress_emitter: ProgressEmitter | None = None,
        **params: object,
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

        # Import directly using base class template method
        result = await self.import_data(
            file_path=Path(file_path),
            import_batch_id=typed_params.get("import_batch_id"),
            progress_emitter=progress_emitter,
            uow=uow,
        )

        # Get the connector plays using base class method
        connector_plays = self._get_stored_connector_plays()

        logger.info(
            "Spotify connector play ingestion complete",
            connector_plays_ingested=len(connector_plays),
            canonical_plays_created=0,  # Zero - we only do ingestion
        )

        return result, connector_plays

    @override
    async def _fetch_data(
        self,
        progress_emitter: ProgressEmitter | None = None,
        uow: UnitOfWorkProtocol | None = None,
        **kwargs: object,
    ) -> list[SpotifyPlayRecord]:
        """Fetch and parse Spotify JSON export file.

        MIGRATED sophisticated file parsing logic from original importer.
        """
        # Mark unused parameters for base class compatibility
        _ = uow
        if progress_emitter is None:
            progress_emitter = NullProgressEmitter()

        # Extract required parameters
        file_path_raw = kwargs.get("file_path")
        if not file_path_raw:
            raise ValueError("file_path is required for Spotify file imports")

        # Validate file exists and is readable
        file_path = (
            file_path_raw
            if isinstance(file_path_raw, Path)
            else Path(str(file_path_raw))
        )

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
        raw_data: list[SpotifyPlayRecord],
        batch_id: str,
        import_timestamp: datetime,
        progress_emitter: ProgressEmitter | None = None,
        uow: UnitOfWorkProtocol | None = None,
        **kwargs: object,
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

        # Process raw data directly into ConnectorTrackPlay objects using factory
        return [
            ConnectorTrackPlay.create_from_spotify_record(
                record,
                import_timestamp=import_timestamp,
                import_batch_id=batch_id,
            )
            for record in raw_data
        ]

    @override
    async def _save_data(
        self, data: list[ConnectorTrackPlay], uow: UnitOfWorkProtocol | None = None
    ) -> tuple[int, int]:
        """Save connector plays using base class method for DRY compliance."""
        if not uow:
            raise RuntimeError("UnitOfWork required for Spotify connector play storage")

        # Use base class method to eliminate duplication
        return await self._save_connector_plays_via_uow(data, uow)

    @override
    async def _handle_checkpoints(
        self,
        raw_data: list[SpotifyPlayRecord],
        uow: UnitOfWorkProtocol | None = None,
        **kwargs: object,
    ) -> None:
        """Update sync checkpoints to track import progress for incremental syncs.

        Spotify-specific implementation. Since Spotify imports are file-based rather
        than API-based incremental syncs, checkpoints are not applicable.
        """
        # Spotify imports are file-based, so checkpoints are not applicable
