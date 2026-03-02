"""Service for syncing connector playlists between external services and local database.

Provides shared functionality for fetching playlists from connectors (Spotify, Last.fm, etc.)
and ensuring they are properly stored/updated in the local database before further processing.
This ensures both backup workflows and source nodes work with fresh, correctly positioned data.
"""

from src.config import get_logger
from src.domain.entities.playlist import ConnectorPlaylist
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


async def sync_connector_playlist(
    connector_name: str,
    playlist_id: str,
    uow: UnitOfWorkProtocol,
) -> ConnectorPlaylist:
    """Fetch playlist from connector and ensure it's stored in database.

    This function handles the complete sync process:
    1. Fetch fresh playlist data from external connector
    2. Store/update the connector playlist in database
    3. Return the synced playlist for further processing

    Args:
        connector_name: Name of the connector (e.g., 'spotify', 'lastfm')
        playlist_id: External playlist ID from the connector service
        uow: Database transaction manager and repository access

    Returns:
        ConnectorPlaylist that has been synced with the database

    Raises:
        ValueError: If connector is unknown or playlist not found
        Exception: For other sync failures
    """
    logger.info(
        f"Syncing connector playlist: {connector_name}:{playlist_id}",
        connector=connector_name,
        playlist_id=playlist_id,
    )

    # Get connector instance to fetch fresh data
    connector_provider = uow.get_service_connector_provider()
    connector_instance = connector_provider.get_connector(connector_name)

    # Step 1: Fetch fresh playlist data from external service
    connector_playlist = await connector_instance.get_playlist(playlist_id)

    if not connector_playlist:
        raise ValueError(f"Playlist not found on {connector_name}: {playlist_id}")

    logger.info(
        f"Fetched playlist from {connector_name}",
        name=connector_playlist.name,
        track_count=len(connector_playlist.items),
    )

    # Step 2: Store/update the connector playlist in database
    # This ensures positions are properly calculated and stored
    connector_playlist_repo = uow.get_connector_playlist_repository()
    _ = await connector_playlist_repo.upsert_model(connector_playlist)

    # Step 3: Retrieve the stored connector playlist from database
    # This ensures we use the database-persisted version as the single source of truth
    stored_playlist = await connector_playlist_repo.get_by_connector_id(
        connector_name, playlist_id
    )

    if not stored_playlist:
        raise ValueError(
            f"Failed to retrieve stored connector playlist: {connector_name}:{playlist_id}"
        )

    logger.info(
        "Synced connector playlist to database",
        connector=connector_name,
        playlist_id=playlist_id,
        db_id=stored_playlist.id,
        track_count=len(stored_playlist.items),
    )

    return stored_playlist
