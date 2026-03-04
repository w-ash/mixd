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

    Note:
        This function writes to the database but does NOT commit the transaction.
        The caller owns the transaction boundary and must ensure commit/rollback.
        This allows callers to compose multiple operations in a single atomic unit
        (e.g., sync connector playlist + upsert canonical playlist).

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

    # Get typed connector instance for playlist operations
    from src.application.use_cases._shared.connector_resolver import (
        resolve_playlist_connector,
    )

    connector_instance = resolve_playlist_connector(connector_name, uow)

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
    # upsert_model returns the persisted entity with .id and .items populated
    connector_playlist_repo = uow.get_connector_playlist_repository()
    stored_playlist = await connector_playlist_repo.upsert_model(connector_playlist)

    logger.info(
        "Synced connector playlist to database",
        connector=connector_name,
        playlist_id=playlist_id,
        db_id=stored_playlist.id,
        track_count=len(stored_playlist.items),
    )

    return stored_playlist
