"""Application service for backing up playlists from external connectors.

Provides high-level functions for the CLI layer to backup playlists without
exposing infrastructure concerns like database sessions or connector management.
"""

from src.application.services.connector_playlist_sync_service import (
    ConnectorPlaylistSyncService,
)
from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistCommand,
    CreateCanonicalPlaylistResult,
    CreateCanonicalPlaylistUseCase,
)
from src.application.use_cases.read_canonical_playlist import (
    ReadCanonicalPlaylistCommand,
    ReadCanonicalPlaylistUseCase,
)
from src.application.use_cases.update_canonical_playlist import (
    UpdateCanonicalPlaylistCommand,
    UpdateCanonicalPlaylistResult,
    UpdateCanonicalPlaylistUseCase,
)
from src.config import get_logger
from src.domain.entities.track import TrackList
from src.domain.repositories import UnitOfWorkProtocol
from src.infrastructure.connectors import (
    discover_connectors,  # Phase 5: move to injected ConnectorRegistry
)

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

    # Discover available connectors (Phase 5: replace with injected ConnectorRegistry)
    connectors = discover_connectors()

    if connector_name not in connectors:
        available = ", ".join(connectors.keys())
        raise ValueError(
            f"Unknown connector '{connector_name}'. Available: {available}"
        )

    async def _backup(
        uow: UnitOfWorkProtocol,
    ) -> CreateCanonicalPlaylistResult | UpdateCanonicalPlaylistResult:
        # Step 1: Sync connector playlist (fetch + store in database)
        sync_service = ConnectorPlaylistSyncService()
        connector_playlist = await sync_service.sync_connector_playlist(
            connector_name, playlist_id, uow
        )

        if not connector_playlist or not connector_playlist.items:
            raise ValueError(f"Playlist not found or empty: {playlist_id}")

        # Step 2: Check if playlist already exists locally
        existing_playlist = None
        try:
            read_use_case = ReadCanonicalPlaylistUseCase()
            read_command = ReadCanonicalPlaylistCommand(
                playlist_id=playlist_id, connector=connector_name
            )
            result = await read_use_case.execute(read_command, uow)
            existing_playlist = result.playlist
            if existing_playlist:
                logger.info(
                    "Found existing local playlist",
                    local_id=existing_playlist.id,
                    name=existing_playlist.name,
                )
        except ValueError:
            logger.info("No existing local playlist found - will create new one")

        # Step 3: Create or update playlist with ConnectorPlaylist as typed field
        if existing_playlist:
            update_use_case = UpdateCanonicalPlaylistUseCase()
            update_command = UpdateCanonicalPlaylistCommand(
                playlist_id=str(existing_playlist.id),
                new_tracklist=TrackList(),
                connector_playlist=connector_playlist,
                playlist_name=connector_playlist.name,
                playlist_description=connector_playlist.description
                or f"Updated from {connector_name}",
            )
            result = await update_use_case.execute(update_command, uow)

            logger.info(
                "Updated existing playlist",
                playlist_id=result.playlist.id,
                operations=result.operations_performed,
                tracks_added=result.tracks_added,
                tracks_removed=result.tracks_removed,
            )
            return result
        else:
            create_use_case = CreateCanonicalPlaylistUseCase()
            create_command = CreateCanonicalPlaylistCommand(
                name=connector_playlist.name,
                tracklist=TrackList(),
                connector_playlist=connector_playlist,
                connector_name=connector_name,
                connector_id=playlist_id,
                description=connector_playlist.description
                or f"Imported from {connector_name}",
            )
            result = await create_use_case.execute(create_command, uow)

            logger.info(
                "Created new playlist",
                playlist_id=result.playlist.id,
                name=result.playlist.name,
                tracks_created=result.tracks_created,
            )
            return result

    return await execute_use_case(_backup)
