"""Long-running operation tools (Epic 3) — propose half only.

These five tools start background operations (imports, playlist sync, bulk
assignment apply, workflow runs). Launching an operation is interface-layer work
(``launch_sse_operation`` needs the operation registry, progress broker, and
background dispatch), so these tools carry no application-layer ``executor``;
they are ``launches_operation`` writes. Confirmation runs them through the
``OperationLauncher`` the FastAPI chat route injects, which reads the ``details``
this module stores, calls the matching interface launcher, and returns the
``{operation_id, run_id}`` handle the chat panel subscribes to over the existing
``/operations/{id}/progress`` SSE.

The ``details`` contract each propose stores (consumed by the interface launcher):

- ``run_workflow`` — ``{operation, workflow_id}``
- ``import_connector_playlists`` — ``{operation, connector_name, identifiers, sync_direction, force}``
- ``apply_playlist_assignments`` — ``{operation, connector_name?, assignment_ids?}``
- ``sync_playlist_link`` — ``{operation, link_id, direction_override?, confirm_token?}``
- ``import_data`` — ``{operation, source, limit?, username?, force?}``
"""

from collections.abc import Mapping

from src.application.chat.dispatchers._common import (
    opt_bool,
    opt_choice,
    opt_int,
    opt_str,
    propose_action,
    require_choice,
    require_str,
    require_str_list,
    require_uuid,
)
from src.application.chat.protocols import ToolContext
from src.domain.entities.shared import JsonDict, JsonValue
from src.domain.exceptions import ToolExecutionError

# SyncDirection supports pull/push only. spotify_history import is excluded from
# chat: its route ingests an uploaded GDPR export file, which chat can't supply
# (ImportTracksUseCase stays covered via lastfm_history).
_SYNC_DIRECTIONS = ("pull", "push")
_IMPORT_SOURCES = ("lastfm_history", "spotify_likes")

# --- run_workflow -----------------------------------------------------------

RUN_WORKFLOW_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "workflow_id": {
            "type": "string",
            "description": "UUID of the saved workflow to run (from list_user_workflows).",
        },
    },
    "required": ["workflow_id"],
    "additionalProperties": False,
}


async def handle_run_workflow(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    workflow_id = require_uuid(tool_input, "workflow_id")
    details: JsonDict = {
        "operation": "run_workflow",
        "workflow_id": str(workflow_id),
        "changes": [f"Run workflow {workflow_id} now"],
    }
    return propose_action(
        ctx, "run_workflow", tool_input, f"Run workflow {workflow_id}", details
    )


# --- import_connector_playlists ---------------------------------------------

IMPORT_CONNECTOR_PLAYLISTS_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "connector": {
            "type": "string",
            "description": "Connector name, e.g. 'spotify'.",
        },
        "identifiers": {
            "type": "array",
            "items": {"type": "string"},
            "description": "External connector playlist identifiers to import as canonical.",
        },
        "sync_direction": {
            "type": "string",
            "enum": list(_SYNC_DIRECTIONS),
            "description": "Sync direction for the created link (default pull).",
        },
        "force": {
            "type": "boolean",
            "description": "Re-import even if unchanged since last import.",
        },
    },
    "required": ["connector", "identifiers"],
    "additionalProperties": False,
}


async def handle_import_connector_playlists(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    connector = require_str(tool_input, "connector")
    identifiers = require_str_list(tool_input, "identifiers")
    direction = opt_choice(tool_input, "sync_direction", _SYNC_DIRECTIONS, "pull")
    details: JsonDict = {
        "operation": "import_connector_playlists",
        "connector_name": connector,
        "identifiers": identifiers,
        "sync_direction": direction,
        "force": opt_bool(tool_input, "force", default=False),
        "changes": [
            f"Import {len(identifiers)} {connector} playlist(s) as canonical playlists"
        ],
    }
    description = f"Import {len(identifiers)} {connector} playlist(s)"
    return propose_action(
        ctx, "import_connector_playlists", tool_input, description, details
    )


# --- apply_playlist_assignments ---------------------------------------------

APPLY_PLAYLIST_ASSIGNMENTS_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "connector": {
            "type": "string",
            "description": "Connector name whose assignments to apply (default spotify).",
        },
        "assignment_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Specific assignment UUIDs to apply; omit to apply all.",
        },
    },
    "additionalProperties": False,
}


async def handle_apply_playlist_assignments(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    connector = opt_str(tool_input, "connector")
    raw_ids = tool_input.get("assignment_ids")
    assignment_ids = (
        require_str_list(tool_input, "assignment_ids") if raw_ids is not None else None
    )
    scope = (
        f"{len(assignment_ids)} assignment(s)" if assignment_ids else "all assignments"
    )
    details: JsonDict = {
        "operation": "apply_playlist_assignments",
        "connector_name": connector,
        "assignment_ids": assignment_ids,
        "changes": [f"Apply {scope} to populate playlists"],
    }
    return propose_action(
        ctx,
        "apply_playlist_assignments",
        tool_input,
        f"Apply {scope}",
        details,
    )


# --- sync_playlist_link -----------------------------------------------------

SYNC_PLAYLIST_LINK_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "link_id": {
            "type": "string",
            "description": "UUID of the playlist link to sync (from query_playlist_links).",
        },
        "direction_override": {
            "type": "string",
            "enum": list(_SYNC_DIRECTIONS),
            "description": "Override the link's configured sync direction for this run.",
        },
        "confirm_token": {
            "type": "string",
            "description": (
                "The confirm_token from a query_playlist_links preview_sync when "
                "the sync is flagged as removing many tracks. Pass it to proceed."
            ),
        },
    },
    "required": ["link_id"],
    "additionalProperties": False,
}


async def handle_sync_playlist_link(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    link_id = require_uuid(tool_input, "link_id")
    # Omitted -> None (use the link's configured direction); present -> validated
    # against the allowed set, raising an actionable error on a bad value.
    direction = (
        require_choice(tool_input, "direction_override", _SYNC_DIRECTIONS)
        if tool_input.get("direction_override") is not None
        else None
    )
    details: JsonDict = {
        "operation": "sync_playlist_link",
        "link_id": str(link_id),
        "direction_override": direction,
        "confirm_token": opt_str(tool_input, "confirm_token"),
        "changes": [f"Sync playlist link {link_id}"],
    }
    return propose_action(
        ctx, "sync_playlist_link", tool_input, f"Sync playlist link {link_id}", details
    )


# --- import_data ------------------------------------------------------------

IMPORT_DATA_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "source": {
            "type": "string",
            "enum": list(_IMPORT_SOURCES),
            "description": (
                "What to import: 'lastfm_history' (Last.fm play history) or "
                "'spotify_likes' (liked tracks)."
            ),
        },
        "username": {
            "type": "string",
            "description": "Last.fm username (required for lastfm_history).",
        },
        "limit": {
            "type": "integer",
            "description": "Cap the number of items imported (1-50000). Omit for all.",
        },
        "force": {
            "type": "boolean",
            "description": "Re-import from the beginning rather than resuming a checkpoint.",
        },
    },
    "required": ["source"],
    "additionalProperties": False,
}


async def handle_import_data(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    source = require_choice(tool_input, "source", _IMPORT_SOURCES)
    username = opt_str(tool_input, "username")
    if source == "lastfm_history" and not username:
        raise ToolExecutionError("'username' is required to import Last.fm history")
    # 0 is the "no cap" sentinel -> None (import all); a real cap is 1-50000.
    limit = opt_int(tool_input, "limit", default=0, minimum=1, maximum=50000)
    details: JsonDict = {
        "operation": "import_data",
        "source": source,
        "username": username,
        "limit": limit or None,
        "force": opt_bool(tool_input, "force", default=False),
        "changes": [f"Import {source.replace('_', ' ')}"],
    }
    return propose_action(
        ctx, "import_data", tool_input, f"Import {source.replace('_', ' ')}", details
    )


SPECS: list[dict[str, object]] = [
    {
        "name": "run_workflow",
        "description": (
            "Call this to run one of the user's saved workflows now — pass its "
            "workflow_id. It starts a background run and returns live progress; "
            "confirm before it launches. Use query_workflow_history afterward to "
            "check the result."
        ),
        "input_schema": RUN_WORKFLOW_INPUT_SCHEMA,
        "dispatch": handle_run_workflow,
        "use_cases": ("RunWorkflowUseCase",),
        "kind": "write",
        "launches_operation": True,
    },
    {
        "name": "import_connector_playlists",
        "description": (
            "Call this to import external connector playlists (e.g. Spotify) into "
            "Mixd as canonical playlists — pass the connector and the playlist "
            "identifiers from query_playlists. It runs in the background with "
            "progress; confirm before it launches."
        ),
        "input_schema": IMPORT_CONNECTOR_PLAYLISTS_INPUT_SCHEMA,
        "dispatch": handle_import_connector_playlists,
        "use_cases": ("ImportConnectorPlaylistsAsCanonicalUseCase",),
        "kind": "write",
        "launches_operation": True,
    },
    {
        "name": "apply_playlist_assignments",
        "description": (
            "Call this to apply tag/preference assignment rules across the "
            "library, populating playlists in bulk — omit assignment_ids to apply "
            "all, or pass specific ones. It runs in the background with progress; "
            "confirm before it launches."
        ),
        "input_schema": APPLY_PLAYLIST_ASSIGNMENTS_INPUT_SCHEMA,
        "dispatch": handle_apply_playlist_assignments,
        "use_cases": ("ApplyPlaylistAssignmentsUseCase",),
        "kind": "write",
        "launches_operation": True,
    },
    {
        "name": "sync_playlist_link",
        "description": (
            "Call this to run a playlist sync link now — pass its link_id from "
            "query_playlist_links. When a sync would remove many tracks it needs "
            "the confirm_token from a preview_sync first. It runs in the "
            "background with progress; confirm before it launches."
        ),
        "input_schema": SYNC_PLAYLIST_LINK_INPUT_SCHEMA,
        "dispatch": handle_sync_playlist_link,
        "use_cases": ("SyncPlaylistLinkUseCase",),
        "kind": "write",
        "launches_operation": True,
    },
    {
        "name": "import_data",
        "description": (
            "Call this to import listening data from a connector — Last.fm play "
            "history or Spotify likes. Pass the source (and a username for "
            "Last.fm). It runs in the background with progress; confirm before "
            "it launches."
        ),
        "input_schema": IMPORT_DATA_INPUT_SCHEMA,
        "dispatch": handle_import_data,
        "use_cases": ("ImportTracksUseCase", "ImportSpotifyLikesUseCase"),
        "kind": "write",
        "launches_operation": True,
    },
]
