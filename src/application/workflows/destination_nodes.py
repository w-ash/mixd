"""Workflow endpoints for creating and updating playlists across music platforms.

Handles playlist operations in workflow pipelines by routing requests to appropriate
use cases. Supports both local canonical playlists and external platform playlists
(Spotify, Apple Music, etc.) with automatic ID resolution and metadata templating.
"""

# pyright: reportAny=false

from collections.abc import Mapping
from typing import Any
from uuid import UUID

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
from src.domain.entities.shared import JsonValue
from src.domain.entities.track import TrackList

from .config_accessors import cfg_bool, cfg_str, cfg_str_or_none
from .node_context import NodeContext
from .protocols import NodeResult, WorkflowContext
from .template_utils import render_playlist_config_templates

logger = get_logger(__name__)


def _prepare_destination(
    context: dict[str, Any], config: Mapping[str, JsonValue]
) -> tuple[TrackList, dict[str, JsonValue], WorkflowContext]:
    """Extract tracklist, render templates, and get workflow context.

    Shared preamble for destination nodes that need all three.
    """
    ctx = NodeContext(context)
    tracklist = ctx.extract_tracklist()
    config = render_playlist_config_templates(config, len(tracklist.tracks))
    workflow_context = ctx.extract_workflow_context()
    return tracklist, config, workflow_context


async def _find_existing_playlist_by_name(
    workflow_context: WorkflowContext, playlist_name: str
) -> UUID | None:
    """Check if a canonical playlist with this name already exists.

    Returns the playlist ID if found, None otherwise. Used for idempotent
    create_playlist: re-running a workflow updates the existing playlist
    instead of creating duplicates.
    """
    from src.domain.repositories import UnitOfWorkProtocol

    user_id = workflow_context.user_id

    async def _search(uow: UnitOfWorkProtocol) -> UUID | None:
        repo = uow.get_playlist_repository()
        all_playlists = await repo.list_all_playlists(user_id=user_id)
        for p in all_playlists:
            if p.name == playlist_name:
                return p.id
        return None

    return await workflow_context.execute_service(_search)


async def create_playlist(
    context: dict[str, Any],
    config: Mapping[str, JsonValue],
) -> NodeResult:
    """Create new playlist from track list with optional platform sync.

    Idempotent: if a playlist with the same name already exists, updates it
    instead of creating a duplicate. This makes workflow re-runs safe.

    Args:
        context: Workflow execution context containing tracklist, use cases, and connectors.
        config: Configuration containing name, description, and optional connector.

    Returns:
        Dictionary with tracklist.

    Raises:
        ValueError: If required 'name' field missing from config.
    """
    tracklist, config, workflow_context = _prepare_destination(context, config)

    if context.get("dry_run"):
        logger.info(
            "Dry-run mode: skipping playlist create", node="destination.create_playlist"
        )
        return {"tracklist": tracklist}

    playlist_name = cfg_str(config, "name")
    if not playlist_name:
        raise ValueError("Missing required 'name' for create_playlist operation")

    ctx = NodeContext(context)

    # Idempotency: check if a playlist with this name already exists
    existing_id = await _find_existing_playlist_by_name(workflow_context, playlist_name)
    if existing_id is not None:
        logger.info(
            "Playlist already exists — updating instead of creating duplicate",
            playlist_name=playlist_name,
            existing_playlist_id=existing_id,
        )
        # Delegate to update_playlist with the existing ID
        update_config = {
            **config,
            "playlist_id": str(existing_id),
        }
        return await update_playlist(context, update_config)

    if connector := cfg_str_or_none(config, "connector"):
        await ctx.emit_phase_progress(
            "sync", "destination", f"Creating playlist on {connector}"
        )

        # Create on both canonical and connector
        command = CreateConnectorPlaylistCommand(
            user_id=workflow_context.user_id,
            tracklist=tracklist,
            playlist_name=playlist_name,
            connector=connector,
            playlist_description=cfg_str(config, "description", "Created by Mixd"),
            create_internal_playlist=True,
        )
        result = await workflow_context.execute_use_case(
            workflow_context.use_cases.get_create_connector_playlist_use_case, command
        )

        logger.info(
            "create_playlist complete",
            connector=connector,
            playlist_id=result.playlist.id,
            playlist_name=result.playlist.name,
            external_playlist_id=result.external_playlist_id,
            track_count=len(tracklist.tracks),
        )
        return {"tracklist": tracklist}
    else:
        # Create canonical only
        command = CreateCanonicalPlaylistCommand(
            user_id=workflow_context.user_id,
            name=playlist_name,
            tracklist=tracklist,
            description=cfg_str(config, "description", "Created by Mixd"),
        )
        result = await workflow_context.execute_use_case(
            workflow_context.use_cases.get_create_canonical_playlist_use_case, command
        )

        logger.info(
            "create_playlist complete",
            playlist_id=result.playlist.id,
            playlist_name=result.playlist.name,
            track_count=len(tracklist.tracks),
        )
        return {"tracklist": tracklist}


async def update_playlist(
    context: dict[str, Any],
    config: Mapping[str, JsonValue],
) -> NodeResult:
    """Update existing playlist with track replacement or appending.

    Interprets playlist_id based on connector presence:
    - Without connector: Updates local canonical playlist by ID
    - With connector: Updates platform playlist, syncs to local canonical

    Supports append mode (add tracks) or overwrite mode (replace all tracks
    while preserving creation timestamps).

    Args:
        context: Workflow execution context containing tracklist, use cases, and connectors.
        config: Must contain playlist_id; optionally connector, append flag,
               name and description updates.

    Returns:
        Dictionary with tracklist.

    Raises:
        ValueError: If required 'playlist_id' field missing from config.
    """
    tracklist, config, workflow_context = _prepare_destination(context, config)

    if context.get("dry_run"):
        logger.info(
            "Dry-run mode: skipping playlist update", node="destination.update_playlist"
        )
        return {"tracklist": tracklist}

    playlist_id = cfg_str(config, "playlist_id")
    if not playlist_id:
        raise ValueError("Missing required 'playlist_id' for update_playlist operation")

    append = cfg_bool(config, "append")

    ctx = NodeContext(context)

    if connector := cfg_str_or_none(config, "connector"):
        await ctx.emit_phase_progress(
            "sync", "destination", f"Syncing playlist to {connector}"
        )

        # playlist_id is connector ID - update connector with optimistic canonical sync
        command = UpdateConnectorPlaylistCommand(
            user_id=workflow_context.user_id,
            playlist_id=playlist_id,
            new_tracklist=tracklist,
            connector=connector,
            append_mode=append,
            playlist_name=cfg_str_or_none(config, "name"),
            playlist_description=cfg_str_or_none(config, "description"),
            preserve_timestamps=not append,  # Use preservation for overwrite
        )
        result = await workflow_context.execute_use_case(
            workflow_context.use_cases.get_update_connector_playlist_use_case, command
        )

        logger.info(
            "update_playlist complete",
            connector=connector,
            playlist_id=result.playlist_id,
            append_mode=append,
            track_count=len(tracklist.tracks),
            operations_performed=result.operations_performed,
            tracks_added=result.tracks_added,
            tracks_removed=result.tracks_removed,
            tracks_moved=result.tracks_moved,
        )
        playlist_changes = result.playlist_changes
    else:
        # playlist_id is canonical ID - update canonical only
        command = UpdateCanonicalPlaylistCommand(
            user_id=workflow_context.user_id,
            playlist_id=playlist_id,
            new_tracklist=tracklist,
            append_mode=append,
            playlist_name=cfg_str_or_none(config, "name"),
            playlist_description=cfg_str_or_none(config, "description"),
        )
        result = await workflow_context.execute_use_case(
            workflow_context.use_cases.get_update_canonical_playlist_use_case, command
        )

        logger.info(
            "update_playlist complete",
            playlist_id=result.playlist.id,
            playlist_name=result.playlist.name,
            append_mode=append,
            track_count=len(tracklist.tracks),
        )
        playlist_changes = result.playlist_changes

    if playlist_changes:
        return {
            "tracklist": tracklist,
            "node_details": {"playlist_changes": playlist_changes},
        }
    return {"tracklist": tracklist}
