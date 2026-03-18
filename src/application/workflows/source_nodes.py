"""Import music data from streaming services and local database.

Functions for retrieving playlists and tracks from multiple sources:
- playlist_source: Import playlists from Spotify/LastFM or read saved playlists
- source_liked_tracks: Get user's favorited tracks with filtering and sorting
- source_played_tracks: Get listening history with time windows and sorting

All functions return standardized track data for playlist creation and analysis.
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: Prefect context dicts

from typing import Any

from src.application.services.connector_playlist_sync_service import (
    sync_connector_playlist,
)
from src.application.services.playlist_upsert import upsert_canonical_playlist
from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistResult,
)
from src.application.use_cases.get_liked_tracks import GetLikedTracksCommand
from src.application.use_cases.get_played_tracks import GetPlayedTracksCommand
from src.application.use_cases.read_canonical_playlist import (
    ReadCanonicalPlaylistCommand,
)
from src.config import get_logger
from src.config.constants import BusinessLimits
from src.domain.entities.playlist import ConnectorPlaylist
from src.domain.entities.track import Track, TrackList
from src.domain.repositories import UnitOfWorkProtocol

from .node_context import NodeContext
from .protocols import NodeResult

logger = get_logger(__name__)


def _extract_library_config(
    config: dict[str, Any], default_sort: str
) -> tuple[int, str | None, str]:
    """Extract shared config for library source nodes (liked/played).

    When no limit is specified in config, uses DEFAULT_LIBRARY_QUERY_LIMIT.
    User-specified limits pass through without clamping — the command
    validator enforces the upper bound (1M sanity guard).
    """
    limit = (
        int(config["limit"])
        if "limit" in config
        else BusinessLimits.DEFAULT_LIBRARY_QUERY_LIMIT
    )
    connector_filter: str | None = config.get("connector_filter")
    sort_by = str(config.get("sort_by", default_sort))
    return limit, connector_filter, sort_by


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
        Dict containing 'tracklist': TrackList with track source metadata.

    Raises:
        ValueError: If playlist_id is missing from config.
    """
    playlist_id = config.get("playlist_id")
    if not playlist_id:
        raise ValueError("Missing required config parameter: playlist_id")

    connector = config.get("connector")

    ctx = NodeContext(context)
    workflow_context = ctx.extract_workflow_context()

    if not connector:
        # Direct canonical playlist read
        logger.info(f"Reading canonical playlist: {playlist_id}")

        read_command = ReadCanonicalPlaylistCommand(playlist_id=playlist_id)
        result = await workflow_context.execute_use_case(
            workflow_context.use_cases.get_read_canonical_playlist_use_case,
            read_command,
        )

        if result.playlist is None:
            raise ValueError(f"Canonical playlist not found: {playlist_id}")

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
        # Connector-based playlist: sync + upsert in a single atomic transaction
        logger.info(f"Fetching {connector} playlist: {playlist_id}")

        await ctx.emit_phase_progress(
            "fetch", "source", f"Fetching playlist from {connector}"
        )

        async def _sync_and_upsert(uow: UnitOfWorkProtocol):
            connector_playlist: ConnectorPlaylist = await sync_connector_playlist(
                connector, playlist_id, uow
            )
            if not connector_playlist.items:
                return None

            result = await upsert_canonical_playlist(
                connector_playlist,
                connector,
                playlist_id,
                uow,
                metric_config=workflow_context.metric_config,
            )
            action = (
                "created"
                if isinstance(result, CreateCanonicalPlaylistResult)
                else "updated"
            )
            return result, action

        try:
            outcome = await workflow_context.execute_service(_sync_and_upsert)
        except Exception as e:
            logger.opt(exception=True).error(
                f"Source node failed: cannot fetch {connector} playlist {playlist_id} — stopping workflow",
                connector=connector,
                playlist_id=playlist_id,
                error_type=type(e).__name__,
            )
            raise

        if outcome is None:
            logger.warning(f"Playlist empty or not found: {playlist_id}")
            return {"tracklist": TrackList()}

        result, action = outcome
        playlist = result.playlist
        tracklist_with_source = _build_source_tracklist(
            playlist.tracks, playlist.name, connector, playlist_id
        )

        logger.info(
            "playlist_source complete",
            action=action,
            source=connector,
            playlist_id=playlist.id,
            playlist_name=playlist.name,
            track_count=len(playlist.tracks),
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
            - limit (int): Max tracks to return (default: DEFAULT_LIBRARY_QUERY_LIMIT).
            - connector_filter (str): Filter by service ("spotify", "lastfm", etc.).
            - sort_by (str): Sort method ("liked_at_desc", "liked_at_asc",
                "title_asc", "random").

    Returns:
        Dict with 'tracklist' containing favorited tracks and metadata.
    """
    limit, connector_filter, sort_by = _extract_library_config(config, "liked_at_desc")

    # Create command for use case
    command = GetLikedTracksCommand(
        limit=limit,
        connector_filter=connector_filter,
        sort_by=sort_by,
    )

    # Get workflow context and execute use case
    ctx = NodeContext(context)
    workflow_context = ctx.extract_workflow_context()

    await ctx.emit_phase_progress("query", "source", "Querying liked tracks")

    # Execute business logic in use case
    result = await workflow_context.execute_use_case(
        workflow_context.use_cases.get_liked_tracks_use_case, command
    )

    if result.total_available > len(result.tracklist.tracks):
        logger.warning(
            "Source limit applied — increase 'limit' config to include all tracks",
            returned=len(result.tracklist.tracks),
            total_available=result.total_available,
        )

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
            - limit (int): Max tracks to return (default: DEFAULT_LIBRARY_QUERY_LIMIT).
            - days_back (int): Time window in days (e.g., 90 for last 3 months).
            - connector_filter (str): Filter by service ("spotify", "lastfm", etc.).
            - sort_by (str): Sort method ("played_at_desc", "total_plays_desc", etc.).

    Returns:
        Dict with 'tracklist' containing played tracks and metadata.
    """
    limit, connector_filter, sort_by = _extract_library_config(config, "played_at_desc")
    days_back: int | None = config.get("days_back")

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

    await ctx.emit_phase_progress("query", "source", "Querying play history")

    # Execute business logic in use case
    result = await workflow_context.execute_use_case(
        workflow_context.use_cases.get_played_tracks_use_case, command
    )

    if result.total_available > len(result.tracklist.tracks):
        logger.warning(
            "Source limit applied — increase 'limit' config to include all tracks",
            returned=len(result.tracklist.tracks),
            total_available=result.total_available,
        )

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
