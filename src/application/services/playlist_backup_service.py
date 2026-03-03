"""Application service for backing up playlists from external connectors.

Provides high-level functions for the CLI layer to backup playlists without
exposing infrastructure concerns like database sessions or connector management.
"""

from src.application.services.connector_playlist_sync_service import (
    sync_connector_playlist,
)
from src.application.services.playlist_upsert import upsert_canonical_playlist
from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistResult,
)
from src.application.use_cases.update_canonical_playlist import (
    UpdateCanonicalPlaylistResult,
)
from src.config import get_logger
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


async def run_playlist_backup(
    connector_name: str, playlist_id: str
) -> CreateCanonicalPlaylistResult | UpdateCanonicalPlaylistResult:
    """Backup a playlist from a connector service to the local database.

    Args:
        connector_name: Name of the connector (e.g., 'spotify')
        playlist_id: Playlist ID from the connector service

    Returns:
        Result object containing the backed up playlist and operation metrics

    Raises:
        ValueError: If connector is unknown or playlist not found
        Exception: For other backup failures
    """
    from src.application.runner import execute_use_case

    logger.info(
        "Starting playlist backup",
        connector=connector_name,
        playlist_id=playlist_id,
    )

    async def _backup(
        uow: UnitOfWorkProtocol,
    ) -> CreateCanonicalPlaylistResult | UpdateCanonicalPlaylistResult:
        # Validate connector exists via UoW's service connector provider
        connector_provider = uow.get_service_connector_provider()
        connector_provider.get_connector(connector_name)

        # Step 1: Sync connector playlist (fetch + store in database)
        connector_playlist = await sync_connector_playlist(
            connector_name, playlist_id, uow
        )

        if not connector_playlist or not connector_playlist.items:
            raise ValueError(f"Playlist not found or empty: {playlist_id}")

        # Step 2: Create or update canonical playlist from connector data
        return await upsert_canonical_playlist(
            connector_playlist, connector_name, playlist_id, uow
        )

    return await execute_use_case(_backup)
