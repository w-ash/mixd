"""Read tool over the user's automation schedules.

``query_schedules`` is a thin adapter over the schedule query use cases: no
argument lists every schedule the user has (workflow triggers and sync
triggers, each with a resolved target label); naming exactly one target
(``workflow_id`` or ``sync_target``) fetches that single schedule's detail. No
business logic lives here — each branch coerces the model's arguments, runs a
use case, and projects the ``Schedule`` domain entity into a compact,
user-data-marked dict.
"""

from collections.abc import Mapping
from uuid import UUID

from src.application.chat.dispatchers._common import (
    iso,
    opt_str,
    opt_uuid,
    user_text,
)
from src.application.chat.protocols import ToolContext
from src.application.runner import execute_use_case
from src.application.use_cases.schedules import (
    GetScheduleCommand,
    GetScheduleUseCase,
    ListSchedulesCommand,
    ListSchedulesUseCase,
)
from src.domain.entities.schedule import Schedule
from src.domain.entities.shared import JsonDict, JsonValue
from src.domain.exceptions import ToolExecutionError

QUERY_SCHEDULES_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "workflow_id": {
            "type": "string",
            "description": (
                "A workflow UUID to fetch the schedule that triggers that "
                "workflow. Supply at most one of 'workflow_id'/'sync_target'; "
                "omit both to list every schedule."
            ),
        },
        "sync_target": {
            "type": "string",
            "description": (
                "A background sync identity (e.g. 'lastfm:plays') to fetch the "
                "schedule that triggers that sync. Supply at most one of "
                "'workflow_id'/'sync_target'; omit both to list every schedule."
            ),
        },
    },
    "additionalProperties": False,
}


def _project_schedule(
    schedule: Schedule, *, target_label: str | None = None
) -> JsonDict:
    """Compact model-facing view of a Schedule — ids raw, cadence derived.

    ``target_label`` (a workflow's name or a sync's friendly name, resolved by
    the list use case) is user-originated for workflow targets, so it is wrapped
    as user text. ``sync_target`` is a system identity and stays plain.
    """
    out: JsonDict = {
        "schedule_id": str(schedule.id),
        "workflow_id": str(schedule.workflow_id)
        if schedule.workflow_id is not None
        else None,
        "sync_target": schedule.sync_target,
        "cadence": schedule.schedule_type,
        "hour": schedule.hour,
        "minute": schedule.minute,
        "day_of_week": schedule.day_of_week,
        "timezone": schedule.timezone,
        "status": schedule.status,
        "next_run_at": iso(schedule.next_run_at),
        "last_run_at": iso(schedule.last_run_at),
        "last_run_status": schedule.last_run_status,
        "run_count": schedule.run_count,
    }
    if target_label is not None:
        out["target_label"] = user_text(target_label)
    return out


async def _list_schedules(ctx: ToolContext) -> JsonValue:
    command = ListSchedulesCommand(user_id=ctx.user_id)
    result = await execute_use_case(
        lambda uow: ListSchedulesUseCase().execute(command, uow),
        user_id=ctx.user_id,
    )
    return {
        "schedules": [
            _project_schedule(entry.schedule, target_label=entry.target_label)
            for entry in result.entries
        ]
    }


async def _get_schedule(
    ctx: ToolContext, workflow_id: UUID | None, sync_target: str | None
) -> JsonValue:
    command = GetScheduleCommand(
        user_id=ctx.user_id, workflow_id=workflow_id, sync_target=sync_target
    )
    try:
        result = await execute_use_case(
            lambda uow: GetScheduleUseCase().execute(command, uow),
            user_id=ctx.user_id,
        )
    except ValueError as e:
        # The use case rejects zero or two targets — surface it so the model
        # names exactly one and retries.
        raise ToolExecutionError(
            "query_schedules accepts at most one of 'workflow_id' or "
            "'sync_target' when fetching a single schedule."
        ) from e
    if result.schedule is None:
        return {
            "schedule": None,
            "message": "No schedule is configured for that target.",
        }
    return {"schedule": _project_schedule(result.schedule)}


async def handle_query_schedules(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    """List every schedule, or fetch one by its target.

    No arguments lists all schedules; naming a ``workflow_id`` or a
    ``sync_target`` fetches that single schedule (or a null when none is
    configured for the target).
    """
    workflow_id = opt_uuid(tool_input, "workflow_id")
    sync_target = opt_str(tool_input, "sync_target")
    if workflow_id is None and sync_target is None:
        return await _list_schedules(ctx)
    return await _get_schedule(ctx, workflow_id, sync_target)


SPECS: list[dict[str, object]] = [
    {
        "name": "query_schedules",
        "description": (
            "Call this to read the user's automation schedules before answering "
            "questions about when a workflow or sync runs, or proposing a "
            "schedule change. With no arguments it lists every schedule and its "
            "target; with a workflow_id or sync_target it returns that single "
            "schedule's cadence, next run, and last-run status."
        ),
        "input_schema": QUERY_SCHEDULES_INPUT_SCHEMA,
        "dispatch": handle_query_schedules,
        "use_cases": (
            "ListSchedulesUseCase",
            "GetScheduleUseCase",
        ),
        "kind": "read",
    },
]
