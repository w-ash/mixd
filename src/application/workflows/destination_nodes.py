"""
Simplified destination node implementations for workflow pipelines.

Destination nodes provide clean, intuitive interfaces for playlist operations:
- create_playlist: Always creates canonical, optionally creates on connector
- update_playlist: Smart ID resolution with append/overwrite and metadata updates

These nodes are thin wrappers that delegate business logic to use cases,
maintaining clear separation of concerns and DRY principles.
"""

from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistCommand,
)
from src.application.use_cases.create_connector_playlist import (
    CreateConnectorPlaylistCommand,
)
from src.application.use_cases.update_canonical_playlist import (
    UpdateCanonicalPlaylistCommand,
)
from src.application.use_cases.update_connector_playlist import (
    UpdateConnectorPlaylistCommand,
)
from src.config import get_logger
from src.domain.entities.track import TrackList

from .node_context import NodeContext
from .template_utils import render_playlist_config_templates

logger = get_logger(__name__)


async def create_playlist(
    tracklist: TrackList,
    config: dict,
    context: dict,
) -> dict:
    """Create a new playlist with optional connector sync.

    Always creates canonical playlist, optionally creates on connector.

    Config:
        name (str): Required playlist name
        description (str): Optional playlist description
        connector (str): Optional connector ("spotify", "apple_music", etc.)
    """
    # Render any template strings in config
    config = render_playlist_config_templates(config, len(tracklist.tracks))

    playlist_name = config.get("name")
    if not playlist_name:
        raise ValueError("Missing required 'name' for create_playlist operation")

    ctx = NodeContext(context)
    workflow_context = ctx.extract_workflow_context()

    connector = config.get("connector")
    if connector:
        # Create on both canonical and connector
        command = CreateConnectorPlaylistCommand(
            tracklist=tracklist,
            playlist_name=playlist_name,
            connector=connector,
            playlist_description=config.get("description", "Created by Narada"),
            create_internal_playlist=True,
        )
        result = await workflow_context.execute_use_case(
            workflow_context.use_cases.get_create_connector_playlist_use_case, command
        )

        return {
            "operation": "create_playlist",
            "playlist": result.playlist,
            "playlist_name": result.playlist.name,
            "playlist_id": result.playlist.id,
            "connector": connector,
            "external_playlist_id": result.external_playlist_id,
            "tracklist": tracklist,
            "track_count": len(tracklist.tracks),
        }
    else:
        # Create canonical only
        command = CreateCanonicalPlaylistCommand(
            name=playlist_name,
            tracklist=tracklist,
            description=config.get("description", "Created by Narada"),
        )
        result = await workflow_context.execute_use_case(
            workflow_context.use_cases.get_create_canonical_playlist_use_case, command
        )

        return {
            "operation": "create_playlist",
            "playlist": result.playlist,
            "playlist_name": result.playlist.name,
            "playlist_id": result.playlist.id,
            "tracklist": tracklist,
            "track_count": len(tracklist.tracks),
        }


async def update_playlist(
    tracklist: TrackList,
    config: dict,
    context: dict,
) -> dict:
    """Update existing playlist with smart ID resolution.

    Smart ID resolution:
    - No connector: playlist_id is canonical ID, update canonical only
    - With connector: playlist_id is connector ID, find/create canonical, update both

    Config:
        playlist_id (str): Required - canonical OR connector playlist ID
        connector (str): Optional - determines ID interpretation
        append (bool): True=append tracks, False=overwrite with preservation
        name (str): Optional - update playlist name
        description (str): Optional - update playlist description
    """
    # Render any template strings in config
    config = render_playlist_config_templates(config, len(tracklist.tracks))

    playlist_id = config.get("playlist_id")
    if not playlist_id:
        raise ValueError("Missing required 'playlist_id' for update_playlist operation")

    ctx = NodeContext(context)
    workflow_context = ctx.extract_workflow_context()

    connector = config.get("connector")
    append = config.get("append", False)

    if connector:
        # playlist_id is connector ID - update connector with optimistic canonical sync
        command = UpdateConnectorPlaylistCommand(
            playlist_id=playlist_id,
            new_tracklist=tracklist,
            connector=connector,
            append_mode=append,
            playlist_name=config.get("name"),  # Optional metadata update
            playlist_description=config.get("description"),  # Optional metadata update
            preserve_timestamps=not append,  # Use preservation for overwrite
        )
        result = await workflow_context.execute_use_case(
            workflow_context.use_cases.get_update_connector_playlist_use_case, command
        )

        return {
            "operation": "update_playlist",
            "connector": connector,
            "playlist_id": result.playlist_id,
            "append_mode": append,
            "tracklist": tracklist,
            "track_count": len(tracklist.tracks),
            "operations_performed": result.operations_performed,
            "tracks_added": result.tracks_added,
            "tracks_removed": result.tracks_removed,
            "tracks_moved": result.tracks_moved,
        }
    else:
        # playlist_id is canonical ID - update canonical only
        command = UpdateCanonicalPlaylistCommand(
            playlist_id=playlist_id,
            new_tracklist=tracklist,
            append_mode=append,
            playlist_name=config.get("name"),  # Optional metadata update
            playlist_description=config.get("description"),  # Optional metadata update
        )
        result = await workflow_context.execute_use_case(
            workflow_context.use_cases.get_update_canonical_playlist_use_case, command
        )

        return {
            "operation": "update_playlist",
            "playlist": result.playlist,
            "playlist_name": result.playlist.name,
            "playlist_id": result.playlist.id,
            "append_mode": append,
            "tracklist": tracklist,
            "track_count": len(tracklist.tracks),
            "operations_performed": result.operations_performed,
            "tracks_added": result.tracks_added,
            "tracks_removed": result.tracks_removed,
            "tracks_moved": result.tracks_moved,
        }


# Export simplified destination handler map
DESTINATION_HANDLERS = {
    "create_playlist": create_playlist,
    "update_playlist": update_playlist,
}
