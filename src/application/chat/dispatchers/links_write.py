"""Write tool: ``manage_playlist_link`` — two-phase-confirmed sync-link mutations.

A PlaylistLink binds a canonical Mixd playlist to an external connector playlist
with an explicit sync direction. Three operations: 'create' (link a canonical
playlist to an external one), 'update' (change an existing link's direction),
'delete' (remove the link). Propose/commit like every write tool —
``handle_manage_playlist_link`` validates and stores a pending action carrying
the exact commit parameters; ``exec_manage_playlist_link`` reconstructs the
use-case Command from ``action.details`` at confirm time and runs the same use
case the web UI calls (identical RLS scoping).

Delete is *moderate* (not destructive): it removes only the sync link — the
playlist and cached connector data stay intact — so it carries a plain
``warning`` but no destructive ``severity``.

The connector playlist identifier is an opaque external id the user pointed at,
committed verbatim (never wrapped as ``<user_data>`` display text).
"""

from collections.abc import Mapping
from uuid import UUID

from src.application.chat.dispatchers._common import (
    commit,
    opt_choice,
    propose_action,
    require_choice,
    require_str,
    require_uuid,
)
from src.application.chat.pending_actions import PendingAction
from src.application.chat.protocols import ToolContext
from src.application.use_cases.create_playlist_link import (
    CreatePlaylistLinkCommand,
    CreatePlaylistLinkUseCase,
)
from src.application.use_cases.delete_playlist_link import (
    DeletePlaylistLinkCommand,
    DeletePlaylistLinkUseCase,
)
from src.application.use_cases.update_playlist_link import (
    UpdatePlaylistLinkCommand,
    UpdatePlaylistLinkUseCase,
)
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection
from src.domain.entities.shared import ConnectorPlaylistIdentifier, JsonDict, JsonValue
from src.domain.exceptions import ToolExecutionError

_OPERATIONS = ("create", "update", "delete")
_DIRECTIONS = ("pull", "push")


MANAGE_PLAYLIST_LINK_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": list(_OPERATIONS),
            "description": (
                "The link mutation to perform. 'create': link a canonical "
                "playlist to an external one (need playlist_id + connector + "
                "identifier, optional direction). 'update': change an existing "
                "link's direction (need link_id + direction). 'delete': remove a "
                "link (need link_id)."
            ),
        },
        "playlist_id": {
            "type": "string",
            "description": "create: UUID of the canonical Mixd playlist.",
        },
        "connector": {
            "type": "string",
            "description": "create: connector name, e.g. 'spotify'.",
        },
        "identifier": {
            "type": "string",
            "description": (
                "create: external connector playlist identifier (ID/URI/URL)."
            ),
        },
        "link_id": {
            "type": "string",
            "description": "update/delete: UUID of the existing link.",
        },
        "direction": {
            "type": "string",
            "enum": list(_DIRECTIONS),
            "description": (
                "Sync direction. 'pull': external is truth (external → "
                "canonical). 'push': canonical is truth (canonical → external). "
                "create defaults to 'pull'; update requires it."
            ),
        },
    },
    "required": ["operation"],
    "additionalProperties": False,
}


def _to_sync_direction(value: str) -> SyncDirection:
    """Coerce a tool ``direction`` string to the domain enum (no cast)."""
    match value:
        case "pull":
            return SyncDirection.PULL
        case "push":
            return SyncDirection.PUSH
        case _:
            raise ToolExecutionError(
                f"direction must be one of: pull, push (got {value!r})"
            )


async def handle_manage_playlist_link(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    """Propose a link mutation — nothing persists until the user confirms.

    Validates the operation's required inputs (missing/mistyped fields raise
    ``ToolExecutionError`` naming what is wrong so the model self-corrects in the
    same turn) and stores the exact commit parameters in ``details``.
    """
    operation = require_choice(tool_input, "operation", _OPERATIONS)

    if operation == "create":
        playlist_id = require_uuid(tool_input, "playlist_id")
        connector = require_str(tool_input, "connector")
        identifier = require_str(tool_input, "identifier")
        direction = opt_choice(tool_input, "direction", _DIRECTIONS, "pull")
        description = f"Link playlist {playlist_id} to {connector} ({direction})"
        details: JsonDict = {
            "operation": operation,
            "playlist_id": str(playlist_id),
            "connector": connector,
            "identifier": identifier,
            "direction": direction,
            "changes": [
                f"Create a {direction} sync link from playlist {playlist_id} to "
                f"{connector} playlist {identifier}"
            ],
        }
    elif operation == "update":
        link_id = require_uuid(tool_input, "link_id")
        direction = require_choice(tool_input, "direction", _DIRECTIONS)
        description = f"Update link {link_id} direction to {direction}"
        details = {
            "operation": operation,
            "link_id": str(link_id),
            "direction": direction,
            "changes": [f"Set link {link_id} sync direction to {direction}"],
        }
    else:  # delete
        link_id = require_uuid(tool_input, "link_id")
        description = f"Delete link {link_id}"
        details = {
            "operation": operation,
            "link_id": str(link_id),
            "warning": (
                "removes the sync link; the playlist and connector data stay intact"
            ),
            "changes": [f"Delete sync link {link_id}"],
        }

    return propose_action(ctx, "manage_playlist_link", tool_input, description, details)


def _project_link(link: PlaylistLink) -> JsonDict:
    """Compact model-facing view of a link — ids raw, direction as its value.

    Deliberate write-confirmation subset of the canonical full projection in
    ``links.py::_project_link`` (8 fields incl. last_synced/status); when a new
    link field is added there, consciously decide whether the confirmation echo
    needs it too.
    """
    return {
        "link_id": str(link.id),
        "connector_name": link.connector_name,
        "sync_direction": link.sync_direction.value,
    }


async def exec_manage_playlist_link(action: PendingAction, user_id: str) -> JsonValue:
    """Commit the proposed link mutation via its use case.

    Reconstructs the Command from ``action.details`` and runs the same use case
    the web UI calls. A playlist/link removed between propose and confirm, or a
    now-invalid direction, surfaces as an actionable error.
    """
    details = action.details
    operation = str(details["operation"])

    if operation == "create":
        command = CreatePlaylistLinkCommand(
            user_id=user_id,
            playlist_id=UUID(str(details["playlist_id"])),
            connector=str(details["connector"]),
            connector_playlist_identifier=ConnectorPlaylistIdentifier(
                str(details["identifier"])
            ),
            sync_direction=_to_sync_direction(str(details["direction"])),
        )
        created = await commit(
            lambda uow: CreatePlaylistLinkUseCase().execute(command, uow),
            user_id,
            not_found=(
                "The playlist could not be found — it may have been deleted "
                "since this link was proposed. Re-check and try again."
            ),
            invalid_prefix="The link could not be created",
        )
        return {
            "status": "confirmed",
            "operation": operation,
            "description": action.description,
            "link": _project_link(created.link),
        }

    if operation == "update":
        update_command = UpdatePlaylistLinkCommand(
            user_id=user_id,
            link_id=UUID(str(details["link_id"])),
            sync_direction=_to_sync_direction(str(details["direction"])),
        )
        updated = await commit(
            lambda uow: UpdatePlaylistLinkUseCase().execute(update_command, uow),
            user_id,
            not_found=(
                "The link no longer exists — it may have been deleted since this "
                "update was proposed. Re-check and try again."
            ),
            invalid_prefix="The link could not be updated",
        )
        return {
            "status": "confirmed",
            "operation": operation,
            "description": action.description,
            "link": _project_link(updated.link),
        }

    if operation == "delete":
        delete_command = DeletePlaylistLinkCommand(
            user_id=user_id,
            link_id=UUID(str(details["link_id"])),
        )
        deleted = await commit(
            lambda uow: DeletePlaylistLinkUseCase().execute(delete_command, uow),
            user_id,
            not_found=(
                "The link no longer exists — it may have already been deleted. "
                "Nothing to do."
            ),
            invalid_prefix="The link could not be deleted",
        )
        return {
            "status": "confirmed",
            "operation": operation,
            "description": action.description,
            "link_id": str(details["link_id"]),
            "deleted": deleted.deleted,
        }

    raise ToolExecutionError(f"Unknown manage_playlist_link operation {operation!r}")


SPECS: list[dict[str, object]] = [
    {
        "name": "manage_playlist_link",
        "description": (
            "Call this to propose a sync-link change between a canonical Mixd playlist and an "
            "external connector playlist. Pick an operation: 'create' links a "
            "playlist to an external one (playlist_id + connector + identifier, "
            "optional direction); 'update' changes an existing link's direction "
            "(link_id + direction); 'delete' removes a link (link_id). Direction "
            "is 'pull' (external is truth) or 'push' (canonical is truth). Every "
            "operation is a proposal — nothing changes until the user confirms "
            "on the card this returns. Look up real playlist_ids, link_ids, and "
            "connector identifiers first; never guess them."
        ),
        "input_schema": MANAGE_PLAYLIST_LINK_INPUT_SCHEMA,
        "dispatch": handle_manage_playlist_link,
        "use_cases": (
            "CreatePlaylistLinkUseCase",
            "UpdatePlaylistLinkUseCase",
            "DeletePlaylistLinkUseCase",
        ),
        "kind": "write",
        "executor": exec_manage_playlist_link,
    },
]
