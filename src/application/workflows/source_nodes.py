"""Source nodes for Narada workflows.

All nodes follow the batch-first design principle, processing data in bulk
and leveraging optimized bulk operations for maximum efficiency.
"""

from typing import Any

from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistCommand,
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

# Infrastructure imports removed for Clean Architecture compliance

logger = get_logger(__name__)


async def playlist_source(context: dict, config: dict) -> dict[str, Any]:
    """Fetch playlist from any connector or canonical source using agnostic ID resolution.

    Config parameters:
        playlist_id (str): Required - playlist identifier
        connector (str): Optional - if specified, playlist_id is connector ID;
                        if omitted, playlist_id is canonical ID

    Smart ID resolution:
    - No connector: playlist_id is canonical ID, read directly from database
    - With connector: playlist_id is connector ID, check for existing canonical mapping
      - If exists: update existing canonical playlist
      - If not exists: create new canonical playlist
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

        # Step 4: Check if canonical playlist already exists for this connector playlist
        # Use ReadCanonicalPlaylistUseCase for Clean Architecture compliance
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
    """Convert ConnectorTrack to domain Track entity.

    Args:
        connector_track: ConnectorTrack from Spotify API

    Returns:
        Domain Track entity
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


# Complex helper functions removed - source nodes are now lightweight orchestration
# Connector metadata persistence is handled by use cases using connector repository
