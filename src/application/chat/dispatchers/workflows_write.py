"""Write tools over persisted workflows and their schedules.

Two two-phase-confirmed dispatchers live here, mirroring ``save_workflow``:

- ``manage_workflow`` — instantiate a workflow from a definition, duplicate an
  existing one, delete one (destructive), or revert it to a saved version.
- ``manage_schedule`` — create-or-replace, enable/disable, or delete
  (destructive) the automated schedule for a workflow or a background sync.

Each ``handle_*`` *proposes*: it coerces the operation's inputs (missing or
mistyped fields raise :class:`ToolExecutionError` naming the fault so the model
self-corrects in the same turn), builds a human-readable ``changes`` list, and
stores a pending action — nothing mutates. The destructive operations add a
``severity``/``warning`` pair the confirmation card renders. After the user
confirms, the registry routes the claimed action to ``exec_*``, which
reconstructs the use-case Command from ``action.details`` and runs the same use
case the web UI calls (identical RLS scoping and validation), mapping
commit-time ``NotFoundError``/``ValueError`` back to actionable errors.
"""

from collections.abc import Mapping
from uuid import UUID

from src.application.chat.dispatchers._common import (
    commit,
    opt_int,
    opt_str,
    opt_uuid,
    propose_action,
    require_choice,
    require_uuid,
    user_text,
)
from src.application.chat.pending_actions import PendingAction
from src.application.chat.protocols import ToolContext
from src.application.chat.workflow_schema import workflow_def_to_dict
from src.application.use_cases.schedules import (
    DeleteScheduleCommand,
    DeleteScheduleUseCase,
    ToggleScheduleCommand,
    ToggleScheduleUseCase,
    UpsertScheduleCommand,
    UpsertScheduleUseCase,
)
from src.application.use_cases.workflow_crud import (
    DeleteWorkflowCommand,
    DeleteWorkflowUseCase,
    DuplicateWorkflowCommand,
    DuplicateWorkflowUseCase,
    InstantiateWorkflowCommand,
    InstantiateWorkflowUseCase,
)
from src.application.use_cases.workflow_versions import (
    RevertWorkflowVersionCommand,
    RevertWorkflowVersionUseCase,
)
from src.domain.entities.schedule import Schedule
from src.domain.entities.shared import JsonDict, JsonValue
from src.domain.entities.workflow import Workflow, parse_workflow_def
from src.domain.exceptions import ToolExecutionError

_MAX_HOUR = 23
_MAX_MINUTE = 59
_MAX_DAY_OF_WEEK = 6

_WORKFLOW_OPERATIONS = ("instantiate", "duplicate", "delete", "revert_version")
_SCHEDULE_OPERATIONS = ("upsert", "toggle", "delete")

# Shared commit-time failure messages for this module's write use cases.
_COMMIT_NOT_FOUND = (
    "The target no longer exists — it may have been deleted since this action "
    "was proposed. Re-check it and try again."
)
_COMMIT_INVALID_PREFIX = "The operation failed validation at confirm time"


# --- manage_workflow --------------------------------------------------------

MANAGE_WORKFLOW_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": list(_WORKFLOW_OPERATIONS),
            "description": (
                "The workflow mutation to perform. 'instantiate' creates a new "
                "user-owned workflow from a complete definition (need "
                "'workflow_def'). 'duplicate' clones an existing workflow (need "
                "'workflow_id'). 'delete' permanently removes a workflow and its "
                "version history (destructive; need 'workflow_id'). "
                "'revert_version' restores a workflow to a saved version (need "
                "'workflow_id' + 'version')."
            ),
        },
        "workflow_def": {
            "type": "object",
            "description": (
                "instantiate: a complete workflow definition (id, name, tasks) "
                "to create a new workflow from."
            ),
        },
        "workflow_id": {
            "type": "string",
            "description": (
                "duplicate/delete/revert_version: UUID of the workflow, from "
                "list_user_workflows."
            ),
        },
        "version": {
            "type": "integer",
            "description": (
                "revert_version: the saved version number to restore the "
                "workflow to (from query_workflow_history resource='versions')."
            ),
        },
    },
    "required": ["operation"],
    "additionalProperties": False,
}


def _project_workflow(workflow: Workflow) -> JsonDict:
    """Compact model-facing view of a Workflow — id raw, name marked."""
    return {
        "workflow_id": str(workflow.id),
        "name": user_text(workflow.definition.name),
    }


async def handle_manage_workflow(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    """Propose a workflow mutation — nothing persists until the user confirms."""
    operation = require_choice(tool_input, "operation", _WORKFLOW_OPERATIONS)

    if operation == "instantiate":
        raw = tool_input.get("workflow_def")
        if not isinstance(raw, Mapping):
            raise ToolExecutionError("'workflow_def' must be a JSON object")
        try:
            wf_def = parse_workflow_def(raw)
        except (ValueError, TypeError, KeyError) as e:
            raise ToolExecutionError(
                f"'workflow_def' is not a parseable workflow definition: {e}"
            ) from e
        task_count = len(wf_def.tasks)
        description = f"Create workflow '{wf_def.name}' from the provided definition"
        details: JsonDict = {
            "operation": operation,
            "workflow_def": workflow_def_to_dict(wf_def),
            "changes": [
                f"Create a new workflow '{wf_def.name}' with "
                f"{task_count} task{'s' if task_count != 1 else ''}"
            ],
        }
    elif operation == "duplicate":
        workflow_id = require_uuid(tool_input, "workflow_id")
        description = f"Duplicate workflow {workflow_id}"
        details = {
            "operation": operation,
            "workflow_id": str(workflow_id),
            "changes": [f"Create an independent copy of workflow {workflow_id}"],
        }
    elif operation == "delete":
        workflow_id = require_uuid(tool_input, "workflow_id")
        description = f"Delete workflow {workflow_id}"
        details = {
            "operation": operation,
            "workflow_id": str(workflow_id),
            "severity": "destructive",
            "warning": "permanently deletes the workflow and its version history",
            "changes": [
                f"Permanently delete workflow {workflow_id} and its version history"
            ],
        }
    else:  # revert_version
        workflow_id = require_uuid(tool_input, "workflow_id")
        if tool_input.get("version") is None:
            raise ToolExecutionError(
                "'version' is required for revert_version and must be an integer"
            )
        # definition_version bumps on every edit, so valid versions climb well
        # past the 500 page-size default; allow ordinal version numbers.
        version = opt_int(
            tool_input, "version", default=1, minimum=1, maximum=1_000_000
        )
        description = f"Revert workflow {workflow_id} to version {version}"
        details = {
            "operation": operation,
            "workflow_id": str(workflow_id),
            "version": version,
            "changes": [f"Restore workflow {workflow_id} to version {version}"],
        }

    return propose_action(ctx, "manage_workflow", tool_input, description, details)


async def exec_manage_workflow(action: PendingAction, user_id: str) -> JsonValue:
    """Commit the proposed workflow mutation via its use case."""
    details = action.details
    operation = str(details["operation"])

    if operation == "instantiate":
        raw = details["workflow_def"]
        if not isinstance(raw, Mapping):
            raise ToolExecutionError("Pending action is missing its workflow_def")
        definition = parse_workflow_def(raw)
        command = InstantiateWorkflowCommand(user_id=user_id, definition=definition)
        result = await commit(
            lambda uow: InstantiateWorkflowUseCase().execute(command, uow),
            user_id,
            not_found=_COMMIT_NOT_FOUND,
            invalid_prefix=_COMMIT_INVALID_PREFIX,
        )
        return {
            "status": "confirmed",
            "operation": operation,
            **_project_workflow(result.workflow),
        }

    if operation == "duplicate":
        dup_command = DuplicateWorkflowCommand(
            user_id=user_id, workflow_id=UUID(str(details["workflow_id"]))
        )
        dup = await commit(
            lambda uow: DuplicateWorkflowUseCase().execute(dup_command, uow),
            user_id,
            not_found=_COMMIT_NOT_FOUND,
            invalid_prefix=_COMMIT_INVALID_PREFIX,
        )
        return {
            "status": "confirmed",
            "operation": operation,
            **_project_workflow(dup.workflow),
        }

    if operation == "delete":
        del_command = DeleteWorkflowCommand(
            user_id=user_id, workflow_id=UUID(str(details["workflow_id"]))
        )
        deleted = await commit(
            lambda uow: DeleteWorkflowUseCase().execute(del_command, uow),
            user_id,
            not_found=_COMMIT_NOT_FOUND,
            invalid_prefix=_COMMIT_INVALID_PREFIX,
        )
        return {
            "status": "confirmed",
            "operation": operation,
            "workflow_id": str(deleted.workflow_id),
        }

    if operation == "revert_version":
        rev_command = RevertWorkflowVersionCommand(
            user_id=user_id,
            workflow_id=UUID(str(details["workflow_id"])),
            version=int(str(details["version"])),
        )
        reverted = await commit(
            lambda uow: RevertWorkflowVersionUseCase().execute(rev_command, uow),
            user_id,
            not_found=_COMMIT_NOT_FOUND,
            invalid_prefix=_COMMIT_INVALID_PREFIX,
        )
        return {
            "status": "confirmed",
            "operation": operation,
            **_project_workflow(reverted.workflow),
        }

    raise ToolExecutionError(f"Unknown manage_workflow operation {operation!r}")


# --- manage_schedule --------------------------------------------------------

MANAGE_SCHEDULE_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": list(_SCHEDULE_OPERATIONS),
            "description": (
                "The schedule mutation to perform. 'upsert' creates or replaces "
                "the schedule for a target (takes hour/minute/optional "
                "day_of_week/timezone). 'toggle' enables or disables it (takes "
                "'enabled'). 'delete' removes it (destructive). Every operation "
                "targets exactly one of 'workflow_id' or 'sync_target'."
            ),
        },
        "workflow_id": {
            "type": "string",
            "description": (
                "A workflow UUID to schedule that workflow. Supply exactly one of "
                "'workflow_id'/'sync_target'."
            ),
        },
        "sync_target": {
            "type": "string",
            "description": (
                "A background sync identity (e.g. 'lastfm:plays') to schedule that "
                "sync. Supply exactly one of 'workflow_id'/'sync_target'."
            ),
        },
        "hour": {
            "type": "integer",
            "description": "upsert: hour of day, 0-23 (default 0).",
        },
        "minute": {
            "type": "integer",
            "description": "upsert: minute of hour, 0-59 (default 0).",
        },
        "day_of_week": {
            "type": "integer",
            "description": (
                "upsert: 0 (Sunday) to 6 (Saturday) for a weekly schedule; omit "
                "for a daily schedule."
            ),
        },
        "timezone": {
            "type": "string",
            "description": "upsert: IANA timezone name (default 'UTC').",
        },
        "enabled": {
            "type": "boolean",
            "description": "toggle: true to enable the schedule, false to disable.",
        },
    },
    "required": ["operation"],
    "additionalProperties": False,
}


def _resolve_target(
    tool_input: Mapping[str, JsonValue],
) -> tuple[UUID | None, str | None]:
    """Coerce and validate the exclusive target arc (exactly one of the two)."""
    workflow_id = opt_uuid(tool_input, "workflow_id")
    sync_target = opt_str(tool_input, "sync_target")
    if (workflow_id is None) == (sync_target is None):
        raise ToolExecutionError(
            "manage_schedule requires exactly one of 'workflow_id' or "
            "'sync_target' (got neither or both)."
        )
    return workflow_id, sync_target


def _project_schedule(schedule: Schedule) -> JsonDict:
    """Compact model-facing view of a Schedule — target ids raw.

    Deliberate write-confirmation subset of the canonical full projection in
    ``schedules.py::_project_schedule`` (adds next_run_at/last_run_at); when a
    new schedule field is added there, consciously decide whether this
    confirmation echo needs it too.
    """
    return {
        "schedule_id": str(schedule.id),
        "workflow_id": str(schedule.workflow_id)
        if schedule.workflow_id is not None
        else None,
        "sync_target": schedule.sync_target,
        "hour": schedule.hour,
        "minute": schedule.minute,
        "day_of_week": schedule.day_of_week,
        "timezone": schedule.timezone,
        "status": schedule.status,
    }


def _target_desc(workflow_id: UUID | None, sync_target: str | None) -> str:
    return (
        f"workflow {workflow_id}"
        if workflow_id is not None
        else f"sync '{sync_target}'"
    )


async def handle_manage_schedule(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    """Propose a schedule mutation — nothing persists until the user confirms."""
    operation = require_choice(tool_input, "operation", _SCHEDULE_OPERATIONS)
    workflow_id, sync_target = _resolve_target(tool_input)
    target_id = str(workflow_id) if workflow_id is not None else None
    target = _target_desc(workflow_id, sync_target)

    if operation == "upsert":
        hour = opt_int(tool_input, "hour", default=0, minimum=0, maximum=_MAX_HOUR)
        minute = opt_int(
            tool_input, "minute", default=0, minimum=0, maximum=_MAX_MINUTE
        )
        day_of_week = (
            None
            if tool_input.get("day_of_week") is None
            else opt_int(
                tool_input,
                "day_of_week",
                default=0,
                minimum=0,
                maximum=_MAX_DAY_OF_WEEK,
            )
        )
        timezone = opt_str(tool_input, "timezone") or "UTC"
        cadence = "daily" if day_of_week is None else f"weekly (day {day_of_week})"
        description = (
            f"Schedule {target}: {cadence} at {hour:02d}:{minute:02d} {timezone}"
        )
        details: JsonDict = {
            "operation": operation,
            "workflow_id": target_id,
            "sync_target": sync_target,
            "hour": hour,
            "minute": minute,
            "day_of_week": day_of_week,
            "timezone": timezone,
            "changes": [
                f"Set {cadence} schedule for {target} at "
                f"{hour:02d}:{minute:02d} {timezone}"
            ],
        }
    elif operation == "toggle":
        raw_enabled = tool_input.get("enabled")
        if raw_enabled is None:
            raise ToolExecutionError(
                "'enabled' is required for toggle and must be true or false"
            )
        if not isinstance(raw_enabled, bool):
            raise ToolExecutionError("'enabled' must be true or false")
        enabled = raw_enabled
        verb = "Enable" if enabled else "Disable"
        description = f"{verb} schedule for {target}"
        details = {
            "operation": operation,
            "workflow_id": target_id,
            "sync_target": sync_target,
            "enabled": enabled,
            "changes": [f"{verb} the schedule for {target}"],
        }
    else:  # delete
        description = f"Delete schedule for {target}"
        details = {
            "operation": operation,
            "workflow_id": target_id,
            "sync_target": sync_target,
            "severity": "destructive",
            "warning": "removes the automated schedule for this target",
            "changes": [f"Delete the schedule for {target}"],
        }

    return propose_action(ctx, "manage_schedule", tool_input, description, details)


async def exec_manage_schedule(action: PendingAction, user_id: str) -> JsonValue:
    """Commit the proposed schedule mutation via its use case."""
    details = action.details
    operation = str(details["operation"])
    workflow_id = (
        UUID(str(details["workflow_id"]))
        if details.get("workflow_id") is not None
        else None
    )
    sync_target = (
        str(details["sync_target"]) if details.get("sync_target") is not None else None
    )

    if operation == "upsert":
        day_of_week = details.get("day_of_week")
        command = UpsertScheduleCommand(
            user_id=user_id,
            workflow_id=workflow_id,
            sync_target=sync_target,
            hour=int(str(details["hour"])),
            minute=int(str(details["minute"])),
            day_of_week=int(str(day_of_week)) if day_of_week is not None else None,
            timezone=str(details["timezone"]),
        )
        upserted = await commit(
            lambda uow: UpsertScheduleUseCase().execute(command, uow),
            user_id,
            not_found=_COMMIT_NOT_FOUND,
            invalid_prefix=_COMMIT_INVALID_PREFIX,
        )
        return {
            "status": "confirmed",
            "operation": operation,
            "created": upserted.created,
            "schedule": _project_schedule(upserted.schedule),
        }

    if operation == "toggle":
        toggle_command = ToggleScheduleCommand(
            user_id=user_id,
            enabled=bool(details["enabled"]),
            workflow_id=workflow_id,
            sync_target=sync_target,
        )
        toggled = await commit(
            lambda uow: ToggleScheduleUseCase().execute(toggle_command, uow),
            user_id,
            not_found=_COMMIT_NOT_FOUND,
            invalid_prefix=_COMMIT_INVALID_PREFIX,
        )
        return {
            "status": "confirmed",
            "operation": operation,
            "schedule": _project_schedule(toggled.schedule),
        }

    if operation == "delete":
        delete_command = DeleteScheduleCommand(
            user_id=user_id, workflow_id=workflow_id, sync_target=sync_target
        )
        deleted = await commit(
            lambda uow: DeleteScheduleUseCase().execute(delete_command, uow),
            user_id,
            not_found=_COMMIT_NOT_FOUND,
            invalid_prefix=_COMMIT_INVALID_PREFIX,
        )
        return {
            "status": "confirmed",
            "operation": operation,
            "schedule_id": str(deleted.schedule_id),
        }

    raise ToolExecutionError(f"Unknown manage_schedule operation {operation!r}")


SPECS: list[dict[str, object]] = [
    {
        "name": "manage_workflow",
        "description": (
            "Call this to propose a change to the user's saved workflows. Pick an "
            "`operation`: 'instantiate' creates a new workflow from a complete "
            "definition (workflow_def); 'duplicate' clones an existing workflow "
            "(workflow_id); 'delete' permanently removes a workflow and its "
            "version history (destructive; workflow_id); 'revert_version' "
            "restores a workflow to a saved version (workflow_id + version). "
            "Every operation is a proposal — nothing changes until the user "
            "confirms on the card this returns. Look up real workflow_ids "
            "(list_user_workflows) and version numbers (query_workflow_history) "
            "first; never guess them."
        ),
        "input_schema": MANAGE_WORKFLOW_INPUT_SCHEMA,
        "dispatch": handle_manage_workflow,
        "use_cases": (
            "InstantiateWorkflowUseCase",
            "DuplicateWorkflowUseCase",
            "DeleteWorkflowUseCase",
            "RevertWorkflowVersionUseCase",
        ),
        "kind": "write",
        "executor": exec_manage_workflow,
    },
    {
        "name": "manage_schedule",
        "description": (
            "Call this to propose a change to a workflow's or sync's automated schedule. Pick "
            "an `operation`: 'upsert' creates or replaces the schedule (hour, "
            "minute, optional day_of_week for weekly, timezone); 'toggle' "
            "enables or disables it (enabled); 'delete' removes it (destructive). "
            "Every operation targets exactly one of workflow_id or sync_target, "
            "and is a proposal — nothing changes until the user confirms on the "
            "card this returns. Look up real targets (list_user_workflows, "
            "query_schedules) first; never guess them."
        ),
        "input_schema": MANAGE_SCHEDULE_INPUT_SCHEMA,
        "dispatch": handle_manage_schedule,
        "use_cases": (
            "UpsertScheduleUseCase",
            "ToggleScheduleUseCase",
            "DeleteScheduleUseCase",
        ),
        "kind": "write",
        "executor": exec_manage_schedule,
    },
]
