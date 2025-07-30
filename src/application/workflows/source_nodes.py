"""Import music data from streaming services and local database.

Functions for retrieving playlists and tracks from multiple sources:
- playlist_source: Import playlists from Spotify/LastFM or read saved playlists
- source_liked_tracks: Get user's favorited tracks with filtering and sorting
- source_played_tracks: Get listening history with time windows and sorting

All functions return standardized track data for playlist creation and analysis.
"""

from typing import Any

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
from src.domain.entities.track import ConnectorTrack, Track, TrackList

from .node_context import NodeContext

logger = get_logger(__name__)


async def playlist_source(context: dict, config: dict) -> dict[str, Any]:
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

        # Create tracklist with source information in metadata
        track_source_map = {
            track.id: {
                "playlist_name": result.playlist.name,
                "source": "canonical",
                "source_id": playlist_id,
            }
            for track in result.playlist.tracks
            if track.id is not None
        }

        tracklist_with_source = TrackList(
            tracks=result.playlist.tracks,
            metadata={
                "track_sources": track_source_map,
                "operation": "playlist_source",
            },
        )

        return {
            "tracklist": tracklist_with_source,
            "playlist_id": result.playlist.id,
            "playlist_name": result.playlist.name,
            "source": "canonical",
            "source_id": playlist_id,
            "operation": "playlist_source",
            "track_count": len(result.playlist.tracks),
        }

    else:
        # Connector-based playlist with upsert logic
        logger.info(f"Fetching {connector} playlist: {playlist_id}")

        # Get connector instance
        connector_instance = ctx.get_connector(connector)

        # Step 1: Fetch playlist from connector using generic method
        connector_playlist = await connector_instance.get_playlist(playlist_id)

        if not connector_playlist or not connector_playlist.items:
            logger.warning(f"Playlist empty or not found: {playlist_id}")
            return {
                "tracklist": TrackList(),
                "playlist_id": None,
                "playlist_name": connector_playlist.name
                if connector_playlist
                else "Unknown",
                "source": connector,
                "source_id": playlist_id,
                "operation": "playlist_source",
                "track_count": 0,
            }

        # Step 2: Get tracks with bulk operations
        track_ids = connector_playlist.track_ids
        logger.info(f"Fetching {len(track_ids)} tracks in bulk from {connector}")

        track_data_map = await connector_instance.get_tracks_by_ids(track_ids)

        # Step 3: Convert to domain models using connector's conversion method
        domain_tracks = [
            _convert_connector_track_to_domain(
                connector_instance.convert_track_to_connector(track_data)
            )
            for track_data in track_data_map.values()
        ]

        logger.info(f"Retrieved {len(domain_tracks)}/{len(track_ids)} tracks in bulk")

        # Create tracklist for use case
        tracklist = TrackList(tracks=domain_tracks)

        # Step 4: Check if local playlist already exists for this service playlist
        # Read from database to avoid duplicates
        existing_playlist = None

        try:
            read_command = ReadCanonicalPlaylistCommand(
                playlist_id=playlist_id, connector=connector
            )
            result = await workflow_context.execute_use_case(
                workflow_context.use_cases.get_read_canonical_playlist_use_case,
                read_command,
            )
            existing_playlist = result.playlist
            logger.info(
                f"Found existing canonical playlist {existing_playlist.id} for {connector}:{playlist_id}"
            )
        except ValueError as e:
            logger.debug(
                f"No existing playlist found for {connector}:{playlist_id}: {e}"
            )
            existing_playlist = None

        if existing_playlist:
            # Update existing canonical playlist
            logger.info(f"Updating existing canonical playlist {existing_playlist.id}")
            update_command = UpdateCanonicalPlaylistCommand(
                playlist_id=str(existing_playlist.id),
                new_tracklist=tracklist,
                playlist_name=connector_playlist.name,
                playlist_description=connector_playlist.description
                or f"Updated from {connector}",
            )

            result = await workflow_context.execute_use_case(
                workflow_context.use_cases.get_update_canonical_playlist_use_case,
                update_command,
            )

            # Create tracklist with source information in metadata
            track_source_map = {
                track.id: {
                    "playlist_name": result.playlist.name,
                    "source": connector,
                    "source_id": playlist_id,
                }
                for track in result.playlist.tracks
                if track.id is not None
            }

            tracklist_with_source = TrackList(
                tracks=result.playlist.tracks,
                metadata={
                    "track_sources": track_source_map,
                    "operation": "playlist_source",
                },
            )

            return {
                "tracklist": tracklist_with_source,
                "playlist_id": result.playlist.id,
                "playlist_name": result.playlist.name,
                "source": connector,
                "source_id": playlist_id,
                "operation": "playlist_source",
                "track_count": len(result.playlist.tracks),
                "action": "updated",
            }

        else:
            # Create new canonical playlist
            logger.info(f"Creating new canonical playlist from {connector}")
            create_command = CreateCanonicalPlaylistCommand(
                name=connector_playlist.name,
                tracklist=tracklist,
                description=connector_playlist.description
                or f"Imported from {connector}",
                metadata={"connector": connector, "connector_id": playlist_id},
            )

            result = await workflow_context.execute_use_case(
                workflow_context.use_cases.get_create_canonical_playlist_use_case,
                create_command,
            )

            # Connector mapping is created automatically by CreateCanonicalPlaylistUseCase
            # when metadata contains connector and connector_id information

            # Create tracklist with source information in metadata
            track_source_map = {
                track.id: {
                    "playlist_name": result.playlist.name,
                    "source": connector,
                    "source_id": playlist_id,
                }
                for track in result.playlist.tracks
                if track.id is not None
            }

            tracklist_with_source = TrackList(
                tracks=result.playlist.tracks,
                metadata={
                    "track_sources": track_source_map,
                    "operation": "playlist_source",
                },
            )

            return {
                "tracklist": tracklist_with_source,
                "playlist_id": result.playlist.id,
                "playlist_name": result.playlist.name,
                "source": connector,
                "source_id": playlist_id,
                "operation": "playlist_source",
                "track_count": len(result.playlist.tracks),
                "action": "created",
            }


def _convert_connector_track_to_domain(connector_track: ConnectorTrack) -> Track:
    """Convert service-specific track to standardized Track format.

    Args:
        connector_track: Track data from music service.

    Returns:
        Track with service metadata preserved in connector_metadata field.
    """
    return Track(
        title=connector_track.title,
        artists=connector_track.artists,
        album=connector_track.album,
        duration_ms=connector_track.duration_ms,
        release_date=connector_track.release_date,
        isrc=connector_track.isrc,
        connector_track_ids={
            connector_track.connector_name: connector_track.connector_track_id
        },
        connector_metadata={
            connector_track.connector_name: {
                "popularity": getattr(connector_track, "popularity", None),
                "preview_url": getattr(connector_track, "preview_url", None),
            }
        },
    )


# === User Music Library Access ===


async def source_liked_tracks(context: dict, config: dict) -> dict[str, Any]:
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
    result = await workflow_context.execute_use_case(
        lambda uow: use_case.execute(command, uow)
    )

    # Return standardized result for workflow composition
    return {
        "tracklist": result.tracklist,
        "operation": "source_liked_tracks",
        "track_count": len(result.tracklist.tracks),
        "connector_filter": connector_filter,
        "sort_by": sort_by,
        "execution_time_ms": result.execution_time_ms,
    }


async def source_played_tracks(context: dict, config: dict) -> dict[str, Any]:
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
    result = await workflow_context.execute_use_case(
        lambda uow: use_case.execute(command, uow)
    )

    # Return standardized result for workflow composition
    return {
        "tracklist": result.tracklist,
        "operation": "source_played_tracks",
        "track_count": len(result.tracklist.tracks),
        "days_back": days_back,
        "connector_filter": connector_filter,
        "sort_by": sort_by,
        "execution_time_ms": result.execution_time_ms,
    }


# Track format conversion and database operations handled by use cases
