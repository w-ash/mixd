"""``query_playlist_links`` — the read tool over playlist-link use cases.

A thin adapter that either lists the connector links for a canonical playlist or
previews what a link's next sync would change (read-only, no side effects). The
preview carries a ``confirm_token`` verbatim so the write-side sync tool can
echo it back and reject a plan that changed since preview.
"""

from collections.abc import Mapping

from src.application.chat.dispatchers._common import (
    iso,
    opt_choice,
    opt_str,
    require_uuid,
    user_text,
)
from src.application.chat.protocols import ToolContext
from src.application.runner import execute_use_case
from src.application.use_cases.list_playlist_links import (
    ListPlaylistLinksCommand,
    ListPlaylistLinksUseCase,
)
from src.application.use_cases.preview_playlist_sync import (
    PreviewPlaylistSyncCommand,
    PreviewPlaylistSyncUseCase,
)
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection
from src.domain.entities.shared import JsonDict, JsonValue

QUERY_PLAYLIST_LINKS_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "mode": {
            "type": "string",
            "enum": ["list", "preview_sync"],
            "description": (
                "'list' (default) returns every connector link for a canonical "
                "playlist_id. 'preview_sync' computes what one link's next sync "
                "would add/remove, read-only, and returns a confirm_token."
            ),
        },
        "playlist_id": {
            "type": "string",
            "description": (
                "Required for mode='list': the canonical playlist id whose "
                "connector links to list."
            ),
        },
        "link_id": {
            "type": "string",
            "description": (
                "Required for mode='preview_sync': the link id (from a 'list' "
                "call) to preview a sync for."
            ),
        },
        "direction_override": {
            "type": "string",
            "enum": ["push", "pull"],
            "description": (
                "Optional for mode='preview_sync': preview a specific direction "
                "instead of the link's configured one. 'push' sends canonical to "
                "the connector; 'pull' brings the connector into canonical."
            ),
        },
    },
    "additionalProperties": False,
}


def _project_link(link: PlaylistLink) -> JsonDict:
    """Compact model-facing view of a connector link — ids raw, name marked."""
    return {
        "link_id": str(link.id),
        "connector_name": link.connector_name,
        "connector_playlist_name": user_text(link.connector_playlist_name),
        "sync_direction": link.sync_direction.value,
        "sync_status": link.sync_status.value,
        "last_synced": iso(link.last_synced),
        "last_sync_tracks_added": link.last_sync_tracks_added,
        "last_sync_tracks_removed": link.last_sync_tracks_removed,
    }


async def _list_links(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    playlist_id = require_uuid(tool_input, "playlist_id")
    command = ListPlaylistLinksCommand(user_id=ctx.user_id, playlist_id=playlist_id)
    result = await execute_use_case(
        lambda uow: ListPlaylistLinksUseCase().execute(command, uow),
        user_id=ctx.user_id,
    )
    return {
        "mode": "list",
        "playlist_id": str(playlist_id),
        "links": [_project_link(link) for link in result.links],
    }


async def _preview_sync(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    link_id = require_uuid(tool_input, "link_id")
    raw_direction = opt_str(tool_input, "direction_override")
    direction_override: SyncDirection | None = None
    if raw_direction is not None:
        direction_override = SyncDirection(
            opt_choice(tool_input, "direction_override", ("push", "pull"), "push")
        )
    command = PreviewPlaylistSyncCommand(
        user_id=ctx.user_id,
        link_id=link_id,
        direction_override=direction_override,
    )
    result = await execute_use_case(
        lambda uow: PreviewPlaylistSyncUseCase().execute(command, uow),
        user_id=ctx.user_id,
    )
    return {
        "mode": "preview_sync",
        "link_id": str(link_id),
        "tracks_to_add": result.tracks_to_add,
        "tracks_to_remove": result.tracks_to_remove,
        "tracks_unchanged": result.tracks_unchanged,
        "direction": result.direction.value,
        "connector_name": user_text(result.connector_name),
        "playlist_name": user_text(result.playlist_name),
        "safety_flagged": result.safety_flagged,
        "safety_message": result.safety_message,
        "safety_removals": result.safety_removals,
        "safety_total": result.safety_total,
        "safety_remaining": result.safety_remaining,
        # Carried verbatim: the write-side sync tool echoes it back so a plan
        # that changed since this preview is rejected.
        "confirm_token": result.confirm_token,
    }


async def handle_query_playlist_links(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    """List a playlist's connector links, or preview one link's next sync.

    mode='list' requires a canonical playlist_id and returns its links.
    mode='preview_sync' requires a link_id and returns the read-only diff plus
    a confirm_token the sync write tool carries.
    """
    mode = opt_choice(tool_input, "mode", ("list", "preview_sync"), "list")
    if mode == "preview_sync":
        return await _preview_sync(tool_input, ctx)
    return await _list_links(tool_input, ctx)


SPECS: list[dict[str, object]] = [
    {
        "name": "query_playlist_links",
        "description": (
            "Call this to see how a canonical playlist connects to external "
            "services, or to preview a sync before running it. mode='list' with "
            "a playlist_id returns each connector link (direction, status, last "
            "sync). mode='preview_sync' with a link_id returns, read-only, how "
            "many tracks the next sync would add/remove and a confirm_token the "
            "sync tool needs — nothing changes until then."
        ),
        "input_schema": QUERY_PLAYLIST_LINKS_INPUT_SCHEMA,
        "dispatch": handle_query_playlist_links,
        "use_cases": (
            "ListPlaylistLinksUseCase",
            "PreviewPlaylistSyncUseCase",
        ),
        "kind": "read",
    },
]
