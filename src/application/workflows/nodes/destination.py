"""Workflow endpoints for creating and updating playlists across music platforms.

Handles playlist operations in workflow pipelines by routing requests to appropriate
use cases. Supports both local canonical playlists and external platform playlists
(Spotify, Apple Music, etc.) with automatic ID resolution and metadata templating.
"""

from collections.abc import Mapping

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
from src.application.workflows.protocols import NodeResult, WorkflowContext
from src.config import get_logger
from src.domain.entities.shared import JsonValue
from src.domain.entities.track import TrackList
from src.domain.exceptions import EmptyOverwriteError

from .config_accessors import (
    cfg_bool,
    cfg_str,
    cfg_str_or_none,
    require_canonical_playlist_uuid,
    require_connector_playlist_identifier,
)
from .execution_context import NodeContext
from .template_utils import render_playlist_config_templates

logger = get_logger(__name__)


def _prepare_destination(
    context: dict[str, object], config: Mapping[str, JsonValue]
) -> tuple[TrackList, dict[str, JsonValue], WorkflowContext]:
    """Extract tracklist, render templates, and get workflow context.

    Shared preamble for destination nodes that need all three.
    """
    ctx = NodeContext(context)
    tracklist = ctx.extract_tracklist()
    config = render_playlist_config_templates(config, len(tracklist.tracks))
    workflow_context = ctx.extract_workflow_context()
    return tracklist, config, workflow_context


async def create_playlist(
    context: dict[str, object],
    config: Mapping[str, JsonValue],
) -> NodeResult:
    """Create new playlist from track list with optional platform sync.

    Always creates a fresh playlist. There is no name-based dedup — names
    are not a stable identity for connector-paired playlists (date templates
    differ across runs, names collide, names get renamed). If a workflow
    needs to keep updating the *same* connector playlist across runs, use
    ``destination.update_playlist`` with an explicit
    ``connector_playlist_identifier`` instead — that's the natural-identity
    lookup, against ``playlist_mappings``.

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

    track_count = len(tracklist.tracks)

    if connector := cfg_str_or_none(config, "connector"):
        await ctx.emit_phase_progress(
            "sync",
            "destination",
            f"Creating playlist on {connector} with {track_count} tracks",
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

        await ctx.emit_phase_progress(
            "sync",
            "destination",
            f"Saved playlist '{result.playlist.name}' to {connector}",
        )

        logger.info(
            "create_playlist complete",
            connector=connector,
            playlist_id=result.playlist.id,
            playlist_name=result.playlist.name,
            external_playlist_id=result.external_playlist_id,
            track_count=track_count,
        )
        return {"tracklist": tracklist}

    # Canonical-only path — no connector roundtrip, just a DB insert.
    await ctx.emit_phase_progress(
        "sync",
        "destination",
        f"Creating canonical playlist with {track_count} tracks",
    )
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
    context: dict[str, object],
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

    append = cfg_bool(config, "append")

    # Guard against wiping the user's playlist when the pipeline produced no
    # tracks (e.g. an enrichment outage degraded, then a metric filter dropped
    # everything). Only overwrite mode is destructive — append of 0 is a no-op.
    # Placed after the dry-run early-return so a preview can still show 0 tracks.
    if not append and not tracklist.tracks:
        raise EmptyOverwriteError(
            cfg_str_or_none(config, "playlist_id")
            or cfg_str_or_none(config, "name")
            or "playlist"
        )

    ctx = NodeContext(context)

    if connector := cfg_str_or_none(config, "connector"):
        connector_playlist_identifier = require_connector_playlist_identifier(
            config, node="destination.update_playlist", connector=connector
        )
        await ctx.emit_phase_progress(
            "sync", "destination", f"Syncing playlist to {connector}"
        )

        command = UpdateConnectorPlaylistCommand(
            user_id=workflow_context.user_id,
            connector_playlist_identifier=connector_playlist_identifier,
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
            connector_playlist_identifier=result.connector_playlist_identifier,
            append_mode=append,
            track_count=len(tracklist.tracks),
            operations_performed=result.operations_performed,
            tracks_added=result.tracks_added,
            tracks_removed=result.tracks_removed,
            tracks_moved=result.tracks_moved,
        )
        playlist_changes = result.playlist_changes
    else:
        playlist_id = require_canonical_playlist_uuid(
            config, node="destination.update_playlist"
        )

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
