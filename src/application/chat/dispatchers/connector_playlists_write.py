"""Write tool: ``manage_connector_playlist`` — refresh the cached connector
playlist snapshot for a batch of external playlists.

Single operation (``refresh``) and non-destructive: it re-fetches external
playlist state into the ``DBConnectorPlaylist`` cache without ever creating a
canonical Playlist or PlaylistLink. Two-phase like every write tool —
``handle_manage_connector_playlist`` *proposes* (stores a pending action
carrying the exact commit parameters plus a human-readable ``changes`` list);
``exec_manage_connector_playlist`` reconstructs the Command from
``action.details`` at confirm time and runs the same use case the web UI calls
(identical RLS scoping). No ``severity`` — a cache refresh mutates nothing the
user can lose.

Connector playlist identifiers are opaque external ids the user pointed at, so
they are committed verbatim (never wrapped as ``<user_data>`` display text).
"""

from collections.abc import Mapping

from src.application.chat.dispatchers._common import (
    commit,
    opt_bool,
    propose_action,
    require_str,
    require_str_list,
)
from src.application.chat.pending_actions import PendingAction
from src.application.chat.protocols import ToolContext
from src.application.use_cases.refresh_connector_playlists import (
    RefreshConnectorPlaylistsCommand,
    RefreshConnectorPlaylistsResult,
    RefreshConnectorPlaylistsUseCase,
)
from src.domain.entities.shared import ConnectorPlaylistIdentifier, JsonDict, JsonValue
from src.domain.exceptions import ToolExecutionError

MANAGE_CONNECTOR_PLAYLIST_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": ["refresh"],
            "description": (
                "The connector-playlist action to perform. 'refresh' re-fetches "
                "the given external playlists into the local cache (need "
                "connector + identifiers)."
            ),
        },
        "connector": {
            "type": "string",
            "description": "Connector name, e.g. 'spotify'.",
        },
        "identifiers": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "External connector playlist identifiers (IDs/URIs/URLs) to "
                "refresh. Look these up first; never guess them."
            ),
        },
        "force": {
            "type": "boolean",
            "description": (
                "Bypass the snapshot-fresh short-circuit and always re-fetch. "
                "Defaults to false."
            ),
        },
    },
    "required": ["operation", "connector", "identifiers"],
    "additionalProperties": False,
}


def _plural(count: int) -> str:
    return "" if count == 1 else "s"


async def handle_manage_connector_playlist(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    """Propose a connector-playlist cache refresh — nothing fetches until confirm.

    Validates the inputs up front (missing/mistyped fields raise
    ``ToolExecutionError`` naming what is wrong so the model self-corrects in the
    same turn) and stores the exact commit parameters in ``details``. Refresh is
    non-destructive, so no ``severity``/``warning`` is attached.
    """
    # Only 'refresh' is defined; require_str keeps the discriminator honest.
    operation = require_str(tool_input, "operation")
    if operation != "refresh":
        raise ToolExecutionError(f"operation must be 'refresh' (got {operation!r})")

    connector = require_str(tool_input, "connector")
    identifiers = require_str_list(tool_input, "identifiers")
    force = opt_bool(tool_input, "force", default=False)

    count = len(identifiers)
    description = f"Refresh {count} cached {connector} playlist{_plural(count)}"
    details: JsonDict = {
        "operation": operation,
        "connector": connector,
        "identifiers": list(identifiers),
        "force": force,
        "changes": [
            f"Re-fetch {count} {connector} playlist{_plural(count)} into the "
            f"local cache" + (" (force)" if force else "")
        ],
    }
    return propose_action(
        ctx, "manage_connector_playlist", tool_input, description, details
    )


def _project_result(result: RefreshConnectorPlaylistsResult) -> JsonDict:
    """Compact, metric-only view of the refresh outcome for the model."""
    return {
        "succeeded": len(result.succeeded),
        "skipped_unchanged": len(result.skipped_unchanged),
        "failed": [
            {
                "identifier": str(failure.connector_playlist_identifier),
                "message": failure.message,
            }
            for failure in result.failed
        ],
    }


async def exec_manage_connector_playlist(
    action: PendingAction, user_id: str
) -> JsonValue:
    """Commit the proposed refresh via ``RefreshConnectorPlaylistsUseCase``.

    Reconstructs the Command from ``action.details`` and runs the same use case
    the web UI calls. A connector that became unavailable, or a validation
    failure, surfaces as an actionable error instead of a raw failure.
    """
    details = action.details
    connector = str(details["connector"])
    raw_identifiers = details["identifiers"]
    identifiers = (
        [ConnectorPlaylistIdentifier(str(i)) for i in raw_identifiers]
        if isinstance(raw_identifiers, list)
        else []
    )
    force = bool(details.get("force", False))

    command = RefreshConnectorPlaylistsCommand(
        user_id=user_id,
        connector_name=connector,
        connector_playlist_identifiers=identifiers,
        force=force,
    )
    result = await commit(
        lambda uow: RefreshConnectorPlaylistsUseCase().execute(command, uow),
        user_id,
        not_found=(
            "The connector or one of the playlists could not be found — it may "
            "have been removed since this refresh was proposed. Re-check and try "
            "again."
        ),
        invalid_prefix="The refresh failed validation at confirm time",
    )

    return {
        "status": "confirmed",
        "operation": "refresh",
        "description": action.description,
        "result": _project_result(result),
    }


SPECS: list[dict[str, object]] = [
    {
        "name": "manage_connector_playlist",
        "description": (
            "Call this to propose refreshing the local cache of external connector playlists. "
            "Pick operation 'refresh' and pass connector + identifiers (the "
            "external playlist IDs to re-fetch); pass force=true to bypass the "
            "freshness short-circuit. This never creates a canonical playlist or "
            "a sync link — it only updates cached snapshots. It is a proposal: "
            "nothing fetches until the user confirms on the card this returns. "
            "Look up real connector playlist identifiers first; never guess them."
        ),
        "input_schema": MANAGE_CONNECTOR_PLAYLIST_INPUT_SCHEMA,
        "dispatch": handle_manage_connector_playlist,
        "use_cases": ("RefreshConnectorPlaylistsUseCase",),
        "kind": "write",
        "executor": exec_manage_connector_playlist,
    },
]
