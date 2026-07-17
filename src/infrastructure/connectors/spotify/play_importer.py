"""Spotify-specific play importer implementing connector-only ingestion.

Contains all Spotify import logic: personal-data export file parsing and
conversion to connector plays for deferred resolution.
"""

from datetime import datetime
from typing import override

from src.config import get_logger
from src.domain.entities import ConnectorTrackPlay, OperationResult
from src.domain.entities.progress import ProgressEmitter
from src.domain.repositories.play import (
    PlayImporterProtocol,
    PlayImportParams,
    SpotifyImportParams,
)
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.connectors.spotify.personal_data import (
    SpotifyPlayRecord,
    parse_spotify_personal_data,
)
from src.infrastructure.services.base_play_importer import BasePlayImporter

logger = get_logger(__name__)


class SpotifyPlayImporter(
    BasePlayImporter[SpotifyPlayRecord, SpotifyImportParams], PlayImporterProtocol
):
    """Spotify play importer for personal-data export files.

    Implements PlayImporterProtocol for use with the generic
    PlayImportOrchestrator. Ingests connector plays only; canonical resolution
    is the resolver's job (two-phase import).
    """

    operation_name: str

    def __init__(self) -> None:
        """Initialize Spotify play importer for connector-only ingestion."""
        self.operation_name = "Spotify Connector Play Import"

    @override
    async def import_plays(
        self,
        uow: UnitOfWorkProtocol,
        params: PlayImportParams,
        *,
        user_id: str | None = None,
        progress_emitter: ProgressEmitter | None = None,
    ) -> tuple[OperationResult, list[ConnectorTrackPlay]]:
        """Import Spotify plays as connector_plays for later resolution.

        Args:
            uow: Unit of work for database operations
            params: Spotify import selectors (file path, batch size)
            user_id: The mixd user the ledger rows belong to (no per-user
                account resolution for file imports, but tenancy stamping)
            progress_emitter: Optional progress emitter

        Returns:
            Tuple of (operation_result, connector_plays_list)
        """
        if not isinstance(params, SpotifyImportParams):
            raise TypeError(
                f"SpotifyPlayImporter requires SpotifyImportParams, got {type(params).__name__}"
            )

        logger.info(
            "Starting Spotify connector play ingestion with sophisticated processing",
            file_path=str(params.file_path),
            batch_size=params.batch_size,
        )

        result, connector_plays = await self.import_data(
            params,
            uow=uow,
            user_id=user_id,
            progress_emitter=progress_emitter,
        )

        logger.info(
            "Spotify connector play ingestion complete",
            connector_plays_ingested=len(connector_plays),
            canonical_plays_created=0,  # Zero - we only do ingestion
        )

        return result, connector_plays

    @override
    async def _fetch_data(
        self,
        params: SpotifyImportParams,
        *,
        uow: UnitOfWorkProtocol,
        progress_emitter: ProgressEmitter | None = None,
        operation_id: str | None = None,
    ) -> list[SpotifyPlayRecord]:
        """Fetch and parse a Spotify JSON export file."""
        _ = uow, progress_emitter, operation_id
        file_path = params.file_path

        if not file_path.exists():
            raise FileNotFoundError(f"Spotify export file not found: {file_path}")

        if not file_path.is_file():
            raise ValueError(f"Path is not a file: {file_path}")

        logger.info(f"📁 Parsing Spotify export file: {file_path}")

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

        logger.info(f"Parsed {len(raw_records)} records from Spotify file: {file_path}")
        return raw_records

    @override
    async def _process_data(
        self,
        raw_data: list[SpotifyPlayRecord],
        *,
        batch_id: str,
        import_timestamp: datetime,
    ) -> list[ConnectorTrackPlay]:
        """Process raw Spotify records into ConnectorTrackPlay objects."""
        return [
            ConnectorTrackPlay.create_from_spotify_record(
                record,
                import_timestamp=import_timestamp,
                import_batch_id=batch_id,
            )
            for record in raw_data
        ]

    @override
    async def _handle_checkpoints(
        self,
        raw_data: list[SpotifyPlayRecord],
        params: SpotifyImportParams,
        uow: UnitOfWorkProtocol,
    ) -> None:
        """No-op: Spotify imports are file-based, so checkpoints don't apply."""
