"""Import music data from streaming services and local database.

Functions for retrieving playlists and tracks from multiple sources:
- playlist_source: Import playlists from Spotify/LastFM or read saved playlists
- source_liked_tracks: Get user's favorited tracks with filtering and sorting
- source_played_tracks: Get listening history with time windows and sorting

All functions return standardized track data for playlist creation and analysis.
"""

from typing import Any

from src.application.services.connector_playlist_sync_service import (
    ConnectorPlaylistSyncService,
)
from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistCommand,
)
from src.application.use_cases.get_liked_tracks import (
    GetLikedTracksCommand,
    GetLikedTracksUseCase,
)
from src.application.use_cases.get_played_tracks import (
    GetPlayedTracksCommand,
    GetPlayedTracksUseCase,
)
from src.application.use_cases.read_canonical_playlist import (
    ReadCanonicalPlaylistCommand,
)
from src.application.use_cases.update_canonical_playlist import (
    UpdateCanonicalPlaylistCommand,
)
from src.config import get_logger
from src.domain.entities.track import Track, TrackList

from .node_context import NodeContext
from .protocols import NodeResult

logger = get_logger(__name__)


def _build_source_tracklist(
    tracks: list[Track], playlist_name: str, source: str, source_id: str
) -> TrackList:
    """Build a TrackList with track_sources metadata from playlist result.

    Pure helper that eliminates the repeated pattern of building track source
    maps and wrapping them in a TrackList with standard metadata.
    """
    track_source_map = {
        track.id: {
            "playlist_name": playlist_name,
            "source": source,
            "source_id": source_id,
        }
        for track in tracks
        if track.id is not None
    }
    return TrackList(
        tracks=tracks,
        metadata={
            "track_sources": track_source_map,
            "operation": "playlist_source",
        },
    )


async def playlist_source(
    context: dict[str, Any], config: dict[str, Any]
) -> NodeResult:
    """Import playlist from streaming service or retrieve saved playlist.

    Fetches playlists from Spotify/LastFM and saves them locally, or reads previously
    saved playlists. When importing from services, automatically updates existing
    local copies or creates new ones.

    Args:
        context: Workflow execution context containing use cases and connectors.
        config: Configuration with required 'playlist_id' and optional 'connector'.
            If no connector specified, reads from local database.

    Returns:
        Dict containing:
            - tracklist: TrackList with track source metadata
            - playlist_id: Internal playlist ID
            - playlist_name: Playlist display name
            - source: Service name ('spotify', 'lastfm') or 'canonical'
            - track_count: Number of tracks retrieved
            - action: 'created', 'updated', or omitted for reads

    Raises:
        ValueError: If playlist_id is missing from config.
    """
    playlist_id = config.get("playlist_id")
    if not playlist_id:
        raise ValueError("Missing required config parameter: playlist_id")

    connector = config.get("connector")

    ctx = NodeContext(context)
    workflow_context = ctx.extract_workflow_context()
    ctx.extract_use_cases()

    if not connector:
        # Direct canonical playlist read
        logger.info(f"Reading canonical playlist: {playlist_id}")

        read_command = ReadCanonicalPlaylistCommand(playlist_id=playlist_id)
        result = await workflow_context.execute_use_case(
            workflow_context.use_cases.get_read_canonical_playlist_use_case,
            read_command,
        )

        tracklist_with_source = _build_source_tracklist(
            result.playlist.tracks, result.playlist.name, "canonical", playlist_id
        )

        logger.info(
            "playlist_source complete",
            source="canonical",
            playlist_id=result.playlist.id,
            playlist_name=result.playlist.name,
            track_count=len(result.playlist.tracks),
        )
        return {"tracklist": tracklist_with_source}

    else:
        # Connector-based playlist with upsert logic
        logger.info(f"Fetching {connector} playlist: {playlist_id}")

        # Step 1: Sync connector playlist (fetch + store in database)
        from src.domain.entities.playlist import ConnectorPlaylist
        from src.domain.repositories import UnitOfWorkProtocol

        sync_service = ConnectorPlaylistSyncService()

        async def _sync(uow: UnitOfWorkProtocol) -> ConnectorPlaylist:
            return await sync_service.sync_connector_playlist(
                connector, playlist_id, uow
            )

        try:
            connector_playlist = await workflow_context.execute_service(_sync)
        except Exception as e:
            logger.opt(exception=True).error(
                f"Source node failed: cannot fetch {connector} playlist {playlist_id} — stopping workflow",
                connector=connector,
                playlist_id=playlist_id,
                error_type=type(e).__name__,
            )
            raise

        if not connector_playlist or not connector_playlist.items:
            logger.warning(f"Playlist empty or not found: {playlist_id}")
            return {"tracklist": TrackList()}

        # Step 2: Check if local playlist already exists for this service playlist
        read_command = ReadCanonicalPlaylistCommand(
            playlist_id=playlist_id, connector=connector
        )
        result = await workflow_context.execute_use_case(
            workflow_context.use_cases.get_read_canonical_playlist_use_case,
            read_command,
        )
        existing_playlist = result.playlist

        if existing_playlist:
            logger.info(
                f"Found existing canonical playlist {existing_playlist.id} for {connector}:{playlist_id}"
            )
        else:
            logger.debug(f"No existing playlist found for {connector}:{playlist_id}")

        # Step 3: Pass ConnectorPlaylist as a typed command field
        # The use case layer handles track processing from the ConnectorPlaylist

        logger.info(
            f"Processing ConnectorPlaylist with {len(connector_playlist.items)} items from {connector}"
        )

        # Step 4: Process playlist (update existing or create new)

        if existing_playlist:
            # Update existing canonical playlist
            logger.info(f"Updating existing canonical playlist {existing_playlist.id}")
            update_command = UpdateCanonicalPlaylistCommand(
                playlist_id=str(existing_playlist.id),
                new_tracklist=TrackList(),
                connector_playlist=connector_playlist,
                playlist_name=connector_playlist.name,
                playlist_description=connector_playlist.description
                or f"Updated from {connector}",
            )

            result = await workflow_context.execute_use_case(
                workflow_context.use_cases.get_update_canonical_playlist_use_case,
                update_command,
            )

            tracklist_with_source = _build_source_tracklist(
                result.playlist.tracks, result.playlist.name, connector, playlist_id
            )

            logger.info(
                "playlist_source complete",
                action="updated",
                source=connector,
                playlist_id=result.playlist.id,
                playlist_name=result.playlist.name,
                track_count=len(result.playlist.tracks),
            )
            return {"tracklist": tracklist_with_source}

        else:
            # Create new canonical playlist
            logger.info(f"Creating new canonical playlist from {connector}")
            create_command = CreateCanonicalPlaylistCommand(
                name=connector_playlist.name,
                tracklist=TrackList(),
                connector_playlist=connector_playlist,
                connector_name=connector,
                connector_id=playlist_id,
                description=connector_playlist.description
                or f"Imported from {connector}",
            )

            result = await workflow_context.execute_use_case(
                workflow_context.use_cases.get_create_canonical_playlist_use_case,
                create_command,
            )

            tracklist_with_source = _build_source_tracklist(
                result.playlist.tracks, result.playlist.name, connector, playlist_id
            )

            logger.info(
                "playlist_source complete",
                action="created",
                source=connector,
                playlist_id=result.playlist.id,
                playlist_name=result.playlist.name,
                track_count=len(result.playlist.tracks),
            )
            return {"tracklist": tracklist_with_source}


# === User Music Library Access ===


async def source_liked_tracks(
    context: dict[str, Any], config: dict[str, Any]
) -> NodeResult:
    """Get user's favorited tracks with filtering and sorting options.

    Retrieves tracks the user has liked/favorited across music services.
    Useful for creating personal best-of playlists and analyzing music taste.

    Args:
        context: Workflow execution context.
        config: Optional parameters:
            - limit (int): Max tracks to return (default 10000, max 10000).
            - connector_filter (str): Filter by service ("spotify", "lastfm", etc.).
            - sort_by (str): Sort method ("liked_at_desc", "liked_at_asc",
                "title_asc", "random").

    Returns:
        Dict with 'tracklist' containing favorited tracks and metadata.
    """
    # Extract config with defaults
    limit = min(config.get("limit", 10000), 10000)  # Enforce performance limit
    connector_filter = config.get("connector_filter")
    sort_by = config.get(
        "sort_by", "liked_at_desc"
    )  # Default to most recent likes first

    # Create command for use case
    command = GetLikedTracksCommand(
        limit=limit,
        connector_filter=connector_filter,
        sort_by=sort_by,
    )

    # Get workflow context and execute use case
    ctx = NodeContext(context)
    workflow_context = ctx.extract_workflow_context()

    # Execute business logic in use case
    use_case = GetLikedTracksUseCase()
    result = await workflow_context.execute_use_case(lambda: use_case, command)

    logger.info(
        "source_liked_tracks complete",
        track_count=len(result.tracklist.tracks),
        connector_filter=connector_filter,
        sort_by=sort_by,
        execution_time_ms=result.execution_time_ms,
    )
    return {"tracklist": result.tracklist}


async def source_played_tracks(
    context: dict[str, Any], config: dict[str, Any]
) -> NodeResult:
    """Get user's listening history with time window and sorting options.

    Retrieves tracks the user has played across music services. Useful for
    creating discovery playlists based on recent listening or analyzing
    long-term music trends.

    Args:
        context: Workflow execution context.
        config: Optional parameters:
            - limit (int): Max tracks to return (default 10000, max 10000).
            - days_back (int): Time window in days (e.g., 90 for last 3 months).
            - connector_filter (str): Filter by service ("spotify", "lastfm", etc.).
            - sort_by (str): Sort method ("played_at_desc", "total_plays_desc", etc.).

    Returns:
        Dict with 'tracklist' containing played tracks and metadata.
    """
    # Extract config with defaults
    limit = min(config.get("limit", 10000), 10000)  # Enforce performance limit
    days_back = config.get("days_back")
    connector_filter = config.get("connector_filter")
    sort_by = config.get(
        "sort_by", "played_at_desc"
    )  # Default to most recent plays first

    # Create command for use case
    command = GetPlayedTracksCommand(
        limit=limit,
        days_back=days_back,
        connector_filter=connector_filter,
        sort_by=sort_by,
    )

    # Get workflow context and execute use case
    ctx = NodeContext(context)
    workflow_context = ctx.extract_workflow_context()

    # Execute business logic in use case
    use_case = GetPlayedTracksUseCase()
    result = await workflow_context.execute_use_case(lambda: use_case, command)

    logger.info(
        "source_played_tracks complete",
        track_count=len(result.tracklist.tracks),
        days_back=days_back,
        connector_filter=connector_filter,
        sort_by=sort_by,
        execution_time_ms=result.execution_time_ms,
    )
    return {"tracklist": result.tracklist}


# Track format conversion and database operations handled by use cases
