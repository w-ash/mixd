"""``query_operations`` — the read tool over the user's operation-run history.

A thin adapter: one ``view`` discriminator selects an existing operations use
case and projects its Result (or domain entity) into a compact, model-facing
dict. Three views only — 'run_detail', 'run_list', 'sync_checkpoint'.
``GetOperationSnapshotUseCase`` is deliberately NOT exposed here: it is an
internal SSE-watchdog fallback keyed by an ephemeral operation_id, not a
user-facing query.

Run summaries in the 'run_list' view are shaped as reusable activity-feed rows
(operation_type, status, started_at, ended_at, counts) so the same projection
answers "what ran recently?" as well as list paging.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Literal, cast

from src.application.chat.dispatchers._common import (
    opt_int,
    opt_str,
    require_choice,
    require_str,
    require_str_list,
    require_uuid,
)
from src.application.chat.protocols import ToolContext
from src.application.runner import execute_use_case
from src.application.use_cases.get_operation_run import (
    GetOperationRunCommand,
    GetOperationRunUseCase,
)
from src.application.use_cases.list_operation_runs import (
    ListOperationRunsCommand,
    ListOperationRunsUseCase,
)
from src.application.use_cases.sync_likes import (
    GetSyncCheckpointStatusCommand,
    GetSyncCheckpointStatusUseCase,
)
from src.domain.entities.operation_run import OperationRun, OperationStatus
from src.domain.entities.shared import JsonDict, JsonValue

_VIEWS = ("run_detail", "run_list", "sync_checkpoint")
_STATUSES = ("running", "complete", "error", "cancelled")
_ENTITY_TYPES = ("likes", "plays")


def _iso(value: datetime | None) -> str | None:
    """ISO-8601 string for a datetime, or None."""
    return value.isoformat() if value is not None else None


def _run_summary(run: OperationRun) -> JsonDict:
    """Compact activity-feed row — reusable for both list paging and feeds."""
    return {
        "run_id": str(run.id),
        "operation_type": run.operation_type,
        "status": run.status,
        "started_at": _iso(run.started_at),
        "ended_at": _iso(run.ended_at),
        "counts": run.counts,
    }


async def _view_run_detail(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonDict:
    """One run's full status, counts, and issues by run_id."""
    run_id = require_uuid(tool_input, "run_id")
    command = GetOperationRunCommand(user_id=ctx.user_id, run_id=run_id)
    run = await execute_use_case(
        lambda uow: GetOperationRunUseCase().execute(command, uow),
        user_id=ctx.user_id,
    )
    if run is None:
        return {
            "view": "run_detail",
            "found": False,
            "message": (
                f"No operation run with id {run_id} owned by this user. Call "
                "query_operations with view 'run_list' to see recent runs and "
                "their ids."
            ),
        }
    return {
        "view": "run_detail",
        "found": True,
        "run_id": str(run.id),
        "operation_type": run.operation_type,
        "status": run.status,
        "started_at": _iso(run.started_at),
        "ended_at": _iso(run.ended_at),
        "counts": run.counts,
        "issues": list(run.issues),
        "operation_id": run.operation_id,
    }


async def _view_run_list(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonDict:
    """A paginated activity feed of recent operation runs (cursor pagination)."""
    limit = opt_int(tool_input, "limit", default=20)
    cursor = opt_str(tool_input, "cursor")
    operation_types = (
        require_str_list(tool_input, "operation_types")
        if tool_input.get("operation_types") is not None
        else None
    )
    status: OperationStatus | None = (
        cast(OperationStatus, require_choice(tool_input, "status", _STATUSES))
        if tool_input.get("status") is not None
        else None
    )
    command = ListOperationRunsCommand(
        user_id=ctx.user_id,
        limit=limit,
        encoded_cursor=cursor,
        operation_types=operation_types,
        status=status,
    )
    result = await execute_use_case(
        lambda uow: ListOperationRunsUseCase().execute(command, uow),
        user_id=ctx.user_id,
    )
    runs: list[JsonValue] = [_run_summary(r) for r in result.runs]
    return {"view": "run_list", "runs": runs, "next_cursor": result.next_cursor}


async def _view_sync_checkpoint(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonDict:
    """A service's likes/plays sync checkpoint state."""
    service = require_str(tool_input, "service")
    entity_type = cast(
        Literal["likes", "plays"],
        require_choice(tool_input, "entity_type", _ENTITY_TYPES),
    )
    command = GetSyncCheckpointStatusCommand(
        user_id=ctx.user_id, service=service, entity_type=entity_type
    )
    status = await execute_use_case(
        lambda uow: GetSyncCheckpointStatusUseCase().execute(command, uow),
        user_id=ctx.user_id,
    )
    return {
        "view": "sync_checkpoint",
        "service": status.service,
        "entity_type": status.entity_type,
        "last_sync_timestamp": _iso(status.last_sync_timestamp),
        "has_previous_sync": status.has_previous_sync,
        "local_count": status.local_count,
        "remote_total": status.remote_total,
    }


async def handle_query_operations(
    tool_input: Mapping[str, JsonValue],
    ctx: ToolContext,
) -> JsonValue:
    """Dispatch one operations ``view`` to its use case and project the result.

    Defaults to the ``run_list`` view. 'run_detail' requires run_id;
    'sync_checkpoint' requires service and entity_type. Missing required fields
    and unknown views raise ``ToolExecutionError`` naming what is valid so the
    model self-corrects in the same turn.
    """
    view = (
        require_choice(tool_input, "view", _VIEWS)
        if "view" in tool_input
        else "run_list"
    )
    if view == "run_detail":
        return await _view_run_detail(tool_input, ctx)
    if view == "sync_checkpoint":
        return await _view_sync_checkpoint(tool_input, ctx)
    return await _view_run_list(tool_input, ctx)


QUERY_OPERATIONS_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "view": {
            "type": "string",
            "enum": list(_VIEWS),
            "description": (
                "Which operations view to read. 'run_list' (default): a "
                "paginated activity feed of recent runs. 'run_detail': one "
                "run's full status/counts/issues (requires run_id). "
                "'sync_checkpoint': a service's sync state (requires service "
                "and entity_type)."
            ),
        },
        "run_id": {
            "type": "string",
            "description": "run_detail: UUID of the operation run to fetch.",
        },
        "limit": {
            "type": "integer",
            "description": "run_list: page size (default 20).",
        },
        "cursor": {
            "type": "string",
            "description": (
                "run_list: opaque next_cursor returned by a previous "
                "query_operations run_list call, to fetch the next page."
            ),
        },
        "operation_types": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "run_list: restrict the feed to these operation types (e.g. "
                "'import_connector_playlists')."
            ),
        },
        "status": {
            "type": "string",
            "enum": list(_STATUSES),
            "description": "run_list: restrict the feed to runs with this status.",
        },
        "service": {
            "type": "string",
            "description": (
                "sync_checkpoint: service name whose checkpoint to read (e.g. "
                "'spotify', 'lastfm')."
            ),
        },
        "entity_type": {
            "type": "string",
            "enum": list(_ENTITY_TYPES),
            "description": "sync_checkpoint: which entity's checkpoint to read.",
        },
    },
    "additionalProperties": False,
}


SPECS: list[dict[str, object]] = [
    {
        "name": "query_operations",
        "description": (
            "Call this to read the user's background-operation history and sync "
            "state. Pick a `view`: 'run_list' for a paginated activity feed of "
            "recent operation runs (filter by operation_types/status, page with "
            "cursor), 'run_detail' for one run's full status/counts/issues by "
            "run_id, 'sync_checkpoint' for a service's likes/plays sync "
            "checkpoint. Use it before answering questions about what ran, "
            "whether an import finished, or when a service last synced."
        ),
        "input_schema": QUERY_OPERATIONS_INPUT_SCHEMA,
        "dispatch": handle_query_operations,
        "use_cases": (
            "GetOperationRunUseCase",
            "ListOperationRunsUseCase",
            "GetSyncCheckpointStatusUseCase",
        ),
        "kind": "read",
    },
]
