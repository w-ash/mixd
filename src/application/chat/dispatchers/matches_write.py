"""``manage_track_matches`` — two-phase writes over connector-mapping matches.

One tool, four operations selected by an ``operation`` discriminator:
``relink`` (move a mapping to a different canonical track), ``unlink`` (sever a
mapping, destructive), ``set_primary`` (promote a mapping to primary for its
connector), and ``resolve_review`` (accept/reject a queued match review).

``handle_manage_track_matches`` *proposes*: it coerces the per-operation fields,
builds a human-readable confirmation card (with a destructive ``warning`` for
``unlink``), and stores a pending action — nothing mutates yet. After the user
confirms, the registry routes the claimed action to
``exec_manage_track_matches``, which reconstructs the Command from ``details``
and runs the matching use case through ``execute_use_case`` exactly as the web
UI does, so RLS scoping and validation are identical to a human doing it.
"""

from collections.abc import Mapping
from typing import Literal, cast
from uuid import UUID

from src.application.chat.dispatchers._common import (
    commit,
    propose_action,
    require_choice,
    require_uuid,
)
from src.application.chat.pending_actions import PendingAction
from src.application.chat.protocols import ToolContext
from src.application.use_cases.relink_connector_track import (
    RelinkConnectorTrackCommand,
    RelinkConnectorTrackUseCase,
)
from src.application.use_cases.resolve_match_review import (
    ResolveMatchReviewCommand,
    ResolveMatchReviewUseCase,
)
from src.application.use_cases.set_primary_mapping import (
    SetPrimaryMappingCommand,
    SetPrimaryMappingUseCase,
)
from src.application.use_cases.unlink_connector_track import (
    UnlinkConnectorTrackCommand,
    UnlinkConnectorTrackUseCase,
)
from src.domain.entities.shared import JsonDict, JsonValue

_OPERATIONS = ("relink", "unlink", "set_primary", "resolve_review")
_REVIEW_ACTIONS = ("accept", "reject")

# Shared commit-time failure messages for this module's match use cases.
_COMMIT_NOT_FOUND = (
    "The mapping, track, or review no longer exists — it may have changed since "
    "this action was proposed. Re-check the match and try again."
)
_COMMIT_INVALID_PREFIX = "The match operation is no longer valid"

MANAGE_TRACK_MATCHES_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": list(_OPERATIONS),
            "description": (
                "Which match operation to perform. 'relink' moves a mapping to "
                "another track (needs mapping_id, new_track_id, current_track_id). "
                "'unlink' severs a mapping (destructive; needs mapping_id, "
                "current_track_id). 'set_primary' promotes a mapping to primary "
                "for its connector (needs mapping_id, track_id). 'resolve_review' "
                "accepts or rejects a queued match review (needs review_id, action)."
            ),
        },
        "mapping_id": {
            "type": "string",
            "description": (
                "UUID of the connector mapping. Required for relink, unlink, "
                "set_primary."
            ),
        },
        "current_track_id": {
            "type": "string",
            "description": (
                "UUID of the track the mapping is currently attached to (tamper "
                "guard). Required for relink and unlink."
            ),
        },
        "new_track_id": {
            "type": "string",
            "description": "UUID of the target track to relink onto. Required for relink.",
        },
        "track_id": {
            "type": "string",
            "description": "UUID of the track the mapping is on. Required for set_primary.",
        },
        "review_id": {
            "type": "string",
            "description": "UUID of the match review to resolve. Required for resolve_review.",
        },
        "action": {
            "type": "string",
            "enum": list(_REVIEW_ACTIONS),
            "description": (
                "resolve_review: 'accept' creates the real mapping, 'reject' "
                "marks it rejected so it is not re-queued."
            ),
        },
    },
    "required": ["operation"],
    "additionalProperties": False,
}


def _propose_relink(tool_input: Mapping[str, JsonValue], ctx: ToolContext) -> JsonValue:
    mapping_id = require_uuid(tool_input, "mapping_id")
    new_track_id = require_uuid(tool_input, "new_track_id")
    current_track_id = require_uuid(tool_input, "current_track_id")
    details: JsonDict = {
        "operation": "relink",
        "mapping_id": str(mapping_id),
        "new_track_id": str(new_track_id),
        "current_track_id": str(current_track_id),
        "changes": [
            f"Mapping {mapping_id} moves from track {current_track_id} "
            f"to track {new_track_id}",
        ],
    }
    description = (
        f"Relink mapping {mapping_id} from track {current_track_id} "
        f"to track {new_track_id}"
    )
    return propose_action(ctx, "manage_track_matches", tool_input, description, details)


def _propose_unlink(tool_input: Mapping[str, JsonValue], ctx: ToolContext) -> JsonValue:
    mapping_id = require_uuid(tool_input, "mapping_id")
    current_track_id = require_uuid(tool_input, "current_track_id")
    details: JsonDict = {
        "operation": "unlink",
        "mapping_id": str(mapping_id),
        "current_track_id": str(current_track_id),
        "changes": [
            f"Mapping {mapping_id} is removed from track {current_track_id}",
            "If the connector track is left unmapped, a new orphan track is created",
        ],
        "severity": "destructive",
        "warning": "severs the connector mapping",
    }
    description = f"Unlink mapping {mapping_id} from track {current_track_id}"
    return propose_action(ctx, "manage_track_matches", tool_input, description, details)


def _propose_set_primary(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    mapping_id = require_uuid(tool_input, "mapping_id")
    track_id = require_uuid(tool_input, "track_id")
    details: JsonDict = {
        "operation": "set_primary",
        "mapping_id": str(mapping_id),
        "track_id": str(track_id),
        "changes": [
            f"Mapping {mapping_id} becomes the primary mapping for its "
            f"connector on track {track_id}",
        ],
    }
    description = f"Set mapping {mapping_id} as primary for track {track_id}"
    return propose_action(ctx, "manage_track_matches", tool_input, description, details)


def _propose_resolve_review(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    review_id = require_uuid(tool_input, "review_id")
    review_action = require_choice(tool_input, "action", _REVIEW_ACTIONS)
    verb = "Accept" if review_action == "accept" else "Reject"
    outcome = (
        "a real connector mapping is created"
        if review_action == "accept"
        else "it is marked rejected and will not be re-queued"
    )
    details: JsonDict = {
        "operation": "resolve_review",
        "review_id": str(review_id),
        "action": review_action,
        "changes": [f"Match review {review_id} is {review_action}ed — {outcome}"],
    }
    description = f"{verb} match review {review_id}"
    return propose_action(ctx, "manage_track_matches", tool_input, description, details)


async def handle_manage_track_matches(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    """Propose one match operation — nothing mutates until the user confirms.

    Coerces the per-operation fields up front so an unparseable id can never sit
    in a pending action, then stores a confirmation card. Missing/invalid fields
    raise ``ToolExecutionError`` naming what is required so the model
    self-corrects in the same turn.
    """
    operation = require_choice(tool_input, "operation", _OPERATIONS)
    if operation == "relink":
        return _propose_relink(tool_input, ctx)
    if operation == "unlink":
        return _propose_unlink(tool_input, ctx)
    if operation == "set_primary":
        return _propose_set_primary(tool_input, ctx)
    return _propose_resolve_review(tool_input, ctx)


async def _exec_relink(d: JsonDict, user_id: str) -> JsonValue:
    command = RelinkConnectorTrackCommand(
        user_id=user_id,
        mapping_id=UUID(str(d["mapping_id"])),
        new_track_id=UUID(str(d["new_track_id"])),
        current_track_id=UUID(str(d["current_track_id"])),
    )
    result = await commit(
        lambda uow: RelinkConnectorTrackUseCase().execute(command, uow),
        user_id,
        not_found=_COMMIT_NOT_FOUND,
        invalid_prefix=_COMMIT_INVALID_PREFIX,
    )
    return {
        "status": "confirmed",
        "operation": "relink",
        "old_track_id": str(result.old_track_id),
        "new_track_id": str(result.new_track_id),
    }


async def _exec_unlink(d: JsonDict, user_id: str) -> JsonValue:
    command = UnlinkConnectorTrackCommand(
        user_id=user_id,
        mapping_id=UUID(str(d["mapping_id"])),
        current_track_id=UUID(str(d["current_track_id"])),
    )
    result = await commit(
        lambda uow: UnlinkConnectorTrackUseCase().execute(command, uow),
        user_id,
        not_found=_COMMIT_NOT_FOUND,
        invalid_prefix=_COMMIT_INVALID_PREFIX,
    )
    orphan = result.orphan_track_id
    return {
        "status": "confirmed",
        "operation": "unlink",
        "deleted_mapping_id": str(result.deleted_mapping_id),
        "orphan_track_id": str(orphan) if orphan is not None else None,
    }


async def _exec_set_primary(d: JsonDict, user_id: str) -> JsonValue:
    command = SetPrimaryMappingCommand(
        user_id=user_id,
        mapping_id=UUID(str(d["mapping_id"])),
        track_id=UUID(str(d["track_id"])),
    )
    # SetPrimaryMappingUseCase.execute returns None — the confirmation echoes
    # the committed ids rather than a Result object.
    await commit(
        lambda uow: SetPrimaryMappingUseCase().execute(command, uow),
        user_id,
        not_found=_COMMIT_NOT_FOUND,
        invalid_prefix=_COMMIT_INVALID_PREFIX,
    )
    return {
        "status": "confirmed",
        "operation": "set_primary",
        "mapping_id": str(command.mapping_id),
        "track_id": str(command.track_id),
    }


async def _exec_resolve_review(d: JsonDict, user_id: str) -> JsonValue:
    command = ResolveMatchReviewCommand(
        user_id=user_id,
        review_id=UUID(str(d["review_id"])),
        action=cast("Literal['accept', 'reject']", str(d["action"])),
    )
    result = await commit(
        lambda uow: ResolveMatchReviewUseCase().execute(command, uow),
        user_id,
        not_found=_COMMIT_NOT_FOUND,
        invalid_prefix=_COMMIT_INVALID_PREFIX,
    )
    return {
        "status": "confirmed",
        "operation": "resolve_review",
        "review_id": str(result.review.id),
        "review_status": result.review.status,
        "mapping_created": result.mapping_created,
    }


async def exec_manage_track_matches(action: PendingAction, user_id: str) -> JsonValue:
    """Commit the proposed match operation through its use case.

    Re-validates at commit time: a mapping/track/review that changed or vanished
    between propose and confirm surfaces as an actionable ``ToolExecutionError``
    (``NotFoundError`` → gone; ``ValueError`` → the guard/state check the use
    case runs) instead of a raw failure.
    """
    d = action.details
    operation = str(d["operation"])
    if operation == "relink":
        return await _exec_relink(d, user_id)
    if operation == "unlink":
        return await _exec_unlink(d, user_id)
    if operation == "set_primary":
        return await _exec_set_primary(d, user_id)
    return await _exec_resolve_review(d, user_id)


SPECS: list[dict[str, object]] = [
    {
        "name": "manage_track_matches",
        "description": (
            "Call this to propose a change to how a connector track is matched to a canonical "
            "track. Pick an `operation`: 'relink' moves a mapping to another "
            "track (needs mapping_id, new_track_id, current_track_id); 'unlink' "
            "severs a mapping (destructive; needs mapping_id, current_track_id); "
            "'set_primary' promotes a mapping to primary for its connector (needs "
            "mapping_id, track_id); 'resolve_review' accepts or rejects a queued "
            "match review (needs review_id and action=accept|reject). It only "
            "proposes a confirmation card — nothing changes until the user confirms."
        ),
        "input_schema": MANAGE_TRACK_MATCHES_INPUT_SCHEMA,
        "dispatch": handle_manage_track_matches,
        "use_cases": (
            "RelinkConnectorTrackUseCase",
            "UnlinkConnectorTrackUseCase",
            "SetPrimaryMappingUseCase",
            "ResolveMatchReviewUseCase",
        ),
        "kind": "write",
        "executor": exec_manage_track_matches,
    },
]
