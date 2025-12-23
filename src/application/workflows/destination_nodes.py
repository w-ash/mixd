"""Workflow endpoints for creating and updating playlists across music platforms.

Handles playlist operations in workflow pipelines by routing requests to appropriate
use cases. Supports both local canonical playlists and external platform playlists
(Spotify, Apple Music, etc.) with automatic ID resolution and metadata templating.
"""

from __future__ import annotations

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
    """Create new playlist from track list with optional platform sync.

    Creates local canonical playlist first, then optionally creates matching
    playlist on external platform (Spotify, Apple Music) if connector specified.

    Args:
        tracklist: Collection of tracks to add to playlist.
        config: Configuration containing name, description, and optional connector.
        context: Workflow execution context for database and platform access.

    Returns:
        Dictionary with playlist details, track count, and platform sync results.

    Raises:
        ValueError: If required 'name' field missing from config.
    """
    # Render any template strings in config
    config = render_playlist_config_templates(config, len(tracklist.tracks))

    playlist_name = config.get("name")
    if not playlist_name:
        raise ValueError("Missing required 'name' for create_playlist operation")

    ctx = NodeContext(context)
    workflow_context = ctx.extract_workflow_context()

    if connector := config.get("connector"):
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
    """Update existing playlist with track replacement or appending.

    Interprets playlist_id based on connector presence:
    - Without connector: Updates local canonical playlist by ID
    - With connector: Updates platform playlist, syncs to local canonical

    Supports append mode (add tracks) or overwrite mode (replace all tracks
    while preserving creation timestamps).

    Args:
        tracklist: New tracks to add or replace existing tracks.
        config: Must contain playlist_id; optionally connector, append flag,
               name and description updates.
        context: Workflow execution context for database and platform access.

    Returns:
        Dictionary with operation details, track counts, and sync results.

    Raises:
        ValueError: If required 'playlist_id' field missing from config.
    """
    # Render any template strings in config
    config = render_playlist_config_templates(config, len(tracklist.tracks))

    playlist_id = config.get("playlist_id")
    if not playlist_id:
        raise ValueError("Missing required 'playlist_id' for update_playlist operation")

    ctx = NodeContext(context)
    workflow_context = ctx.extract_workflow_context()

    append = config.get("append", False)

    if connector := config.get("connector"):
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
