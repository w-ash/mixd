"""Write tool: ``manage_playlist_assignments`` — two-phase-confirmed assignment
mutations.

A PlaylistAssignment binds a cached connector playlist to a metadata action
(``set_preference`` or ``add_tag``) applied to every track in that playlist.
Three operations: 'create' (declare the assignment), 'create_and_apply' (declare
it and apply it to the playlist's tracks immediately — the synchronous single
apply, NOT the bulk long-running apply), 'delete' (remove the assignment).
Propose/commit like every write tool — ``handle_manage_playlist_assignments``
validates and stores a pending action carrying the exact commit parameters;
``exec_manage_playlist_assignments`` reconstructs the use-case Command from
``action.details`` at confirm time and runs the same use case the web UI calls
(identical RLS scoping and value validation).
"""

from collections.abc import Mapping
from uuid import UUID

from src.application.chat.dispatchers._common import (
    commit,
    propose_action,
    require_choice,
    require_str,
    require_uuid,
)
from src.application.chat.pending_actions import PendingAction
from src.application.chat.protocols import ToolContext
from src.application.use_cases.create_and_apply_assignment import (
    CreateAndApplyAssignmentCommand,
    CreateAndApplyAssignmentUseCase,
)
from src.application.use_cases.create_playlist_assignment import (
    CreatePlaylistAssignmentCommand,
    CreatePlaylistAssignmentUseCase,
)
from src.application.use_cases.delete_playlist_assignment import (
    DeletePlaylistAssignmentCommand,
    DeletePlaylistAssignmentUseCase,
)
from src.domain.entities.playlist_assignment import (
    AssignmentActionType,
    PlaylistAssignment,
)
from src.domain.entities.shared import JsonDict, JsonValue
from src.domain.exceptions import ToolExecutionError

_OPERATIONS = ("create", "create_and_apply", "delete")
_ACTION_TYPES = ("set_preference", "add_tag")

# Shared commit-time failure messages for the create/create_and_apply paths.
_ASSIGN_NOT_FOUND = (
    "The connector playlist could not be found — it may have been removed since "
    "this assignment was proposed. Re-check and try again."
)
_ASSIGN_INVALID_PREFIX = "The assignment failed validation at confirm time"


MANAGE_PLAYLIST_ASSIGNMENTS_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": list(_OPERATIONS),
            "description": (
                "The assignment mutation to perform. 'create': declare an "
                "assignment (need connector_playlist_id + action_type + "
                "action_value). 'create_and_apply': declare it and apply it to "
                "the playlist's tracks immediately (same inputs). 'delete': "
                "remove an assignment (need assignment_id)."
            ),
        },
        "connector_playlist_id": {
            "type": "string",
            "description": (
                "create/create_and_apply: UUID of the cached connector playlist "
                "to attach the action to."
            ),
        },
        "action_type": {
            "type": "string",
            "enum": list(_ACTION_TYPES),
            "description": (
                "create/create_and_apply: 'set_preference' (action_value is one "
                "of hmm/nah/yah/star) or 'add_tag' (action_value is a tag name)."
            ),
        },
        "action_value": {
            "type": "string",
            "description": (
                "create/create_and_apply: the preference value or tag name to "
                "apply to every track in the playlist."
            ),
        },
        "assignment_id": {
            "type": "string",
            "description": "delete: UUID of the assignment to remove.",
        },
    },
    "required": ["operation"],
    "additionalProperties": False,
}


def _to_action_type(value: str) -> AssignmentActionType:
    """Coerce a tool ``action_type`` string to the domain literal (no cast)."""
    match value:
        case "set_preference":
            return "set_preference"
        case "add_tag":
            return "add_tag"
        case _:
            raise ToolExecutionError(
                f"action_type must be one of: set_preference, add_tag (got {value!r})"
            )


async def handle_manage_playlist_assignments(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    """Propose an assignment mutation — nothing persists until the user confirms.

    Validates the operation's required inputs (missing/mistyped fields raise
    ``ToolExecutionError`` naming what is wrong so the model self-corrects in the
    same turn) and stores the exact commit parameters in ``details``.
    """
    operation = require_choice(tool_input, "operation", _OPERATIONS)

    if operation in ("create", "create_and_apply"):
        connector_playlist_id = require_uuid(tool_input, "connector_playlist_id")
        action_type = require_choice(tool_input, "action_type", _ACTION_TYPES)
        action_value = require_str(tool_input, "action_value")
        verb = "Create and apply" if operation == "create_and_apply" else "Create"
        change_tail = (
            " and apply it to every track" if operation == "create_and_apply" else ""
        )
        description = (
            f"{verb} assignment {action_type}={action_value} on playlist "
            f"{connector_playlist_id}"
        )
        details: JsonDict = {
            "operation": operation,
            "connector_playlist_id": str(connector_playlist_id),
            "action_type": action_type,
            "action_value": action_value,
            "changes": [
                f"Set {action_type}={action_value} for connector playlist "
                f"{connector_playlist_id}{change_tail}"
            ],
        }
    else:  # delete
        assignment_id = require_uuid(tool_input, "assignment_id")
        description = f"Delete assignment {assignment_id}"
        details = {
            "operation": operation,
            "assignment_id": str(assignment_id),
            "warning": (
                "removes the assignment; metadata already written by past "
                "applies is left in place"
            ),
            "changes": [f"Delete assignment {assignment_id}"],
        }

    return propose_action(
        ctx, "manage_playlist_assignments", tool_input, description, details
    )


def _project_assignment(assignment: PlaylistAssignment) -> JsonDict:
    """Compact model-facing view of an assignment — ids raw, action verbatim."""
    return {
        "assignment_id": str(assignment.id),
        "connector_playlist_id": str(assignment.connector_playlist_id),
        "action_type": assignment.action_type,
        "action_value": assignment.action_value,
    }


async def exec_manage_playlist_assignments(
    action: PendingAction, user_id: str
) -> JsonValue:
    """Commit the proposed assignment mutation via its use case.

    Reconstructs the Command from ``action.details`` and runs the same use case
    the web UI calls — the domain entity re-validates ``action_value`` at
    construction, so an invalid preference/tag surfaces as an actionable error.
    """
    details = action.details
    operation = str(details["operation"])

    if operation == "create":
        command = CreatePlaylistAssignmentCommand(
            user_id=user_id,
            connector_playlist_id=UUID(str(details["connector_playlist_id"])),
            action_type=_to_action_type(str(details["action_type"])),
            raw_action_value=str(details["action_value"]),
        )
        result = await commit(
            lambda uow: CreatePlaylistAssignmentUseCase().execute(command, uow),
            user_id,
            not_found=_ASSIGN_NOT_FOUND,
            invalid_prefix=_ASSIGN_INVALID_PREFIX,
        )
        return {
            "status": "confirmed",
            "operation": operation,
            "description": action.description,
            "assignment": _project_assignment(result.assignment),
            "created": result.created,
        }

    if operation == "create_and_apply":
        apply_command = CreateAndApplyAssignmentCommand(
            user_id=user_id,
            connector_playlist_id=UUID(str(details["connector_playlist_id"])),
            action_type=_to_action_type(str(details["action_type"])),
            raw_action_value=str(details["action_value"]),
        )
        apply_outcome = await commit(
            lambda uow: CreateAndApplyAssignmentUseCase().execute(apply_command, uow),
            user_id,
            not_found=_ASSIGN_NOT_FOUND,
            invalid_prefix=_ASSIGN_INVALID_PREFIX,
        )
        apply_result = apply_outcome.apply_result
        return {
            "status": "confirmed",
            "operation": operation,
            "description": action.description,
            "assignment": _project_assignment(apply_outcome.assignment),
            "applied": {
                "preferences_applied": apply_result.preferences_applied,
                "tags_applied": apply_result.tags_applied,
                "assignments_processed": apply_result.assignments_processed,
            },
        }

    if operation == "delete":
        delete_command = DeletePlaylistAssignmentCommand(
            user_id=user_id,
            assignment_id=UUID(str(details["assignment_id"])),
        )
        deleted = await commit(
            lambda uow: DeletePlaylistAssignmentUseCase().execute(delete_command, uow),
            user_id,
            not_found=(
                "The assignment no longer exists — it may have already been "
                "deleted. Nothing to do."
            ),
            invalid_prefix="The assignment could not be deleted",
        )
        return {
            "status": "confirmed",
            "operation": operation,
            "description": action.description,
            "assignment_id": str(details["assignment_id"]),
            "deleted": deleted.deleted,
        }

    raise ToolExecutionError(
        f"Unknown manage_playlist_assignments operation {operation!r}"
    )


SPECS: list[dict[str, object]] = [
    {
        "name": "manage_playlist_assignments",
        "description": (
            "Call this to propose a metadata-assignment change on a cached connector "
            "playlist. Pick an operation: 'create' declares an assignment "
            "(connector_playlist_id + action_type + action_value); "
            "'create_and_apply' declares it and applies it to every track "
            "immediately; 'delete' removes an assignment (assignment_id). "
            "action_type is 'set_preference' (value hmm/nah/yah/star) or "
            "'add_tag' (value is a tag name). Every operation is a proposal — "
            "nothing changes until the user confirms on the card this returns. "
            "Look up real connector_playlist_ids and assignment_ids first; never "
            "guess them."
        ),
        "input_schema": MANAGE_PLAYLIST_ASSIGNMENTS_INPUT_SCHEMA,
        "dispatch": handle_manage_playlist_assignments,
        "use_cases": (
            "CreatePlaylistAssignmentUseCase",
            "DeletePlaylistAssignmentUseCase",
            "CreateAndApplyAssignmentUseCase",
        ),
        "kind": "write",
        "executor": exec_manage_playlist_assignments,
    },
]
