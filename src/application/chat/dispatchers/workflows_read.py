"""Read tools over workflow previews and run/version history.

Two tools live here, both thin adapters over existing application use cases:

- ``preview_workflow`` dry-runs a complete workflow definition against the
  user's real library and returns a compact head of the output tracks plus
  per-node counts. It is the one tool that breaks the standard dispatcher
  pattern: :class:`PreviewWorkflowUseCase` manages its own sessions and takes a
  parsed ``WorkflowDef`` (not a Command/uow), so it is called directly rather
  than through ``execute_use_case``.
- ``query_workflow_history`` fans a ``resource``/``scope`` discriminator over
  the run-history and version-history query use cases so the model has one tool
  to learn: run history for one workflow, the cross-workflow active runs, the
  latest run per workflow, a single run's detail, and a workflow's version list
  or one version's detail.

No business logic lives here — every branch coerces the model's arguments, runs
a use case, and projects the domain result into a compact, user-data-marked
dict (the application layer imports inward only, never interface schemas).
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from uuid import UUID

from src.application.chat.dispatchers._common import (
    opt_choice,
    opt_int,
    opt_uuid,
    require_uuid,
    require_uuid_list,
    user_text,
)
from src.application.chat.protocols import ToolContext
from src.application.runner import execute_use_case
from src.application.use_cases.workflow_preview import PreviewWorkflowUseCase
from src.application.use_cases.workflow_runs import (
    GetLatestWorkflowRunsCommand,
    GetLatestWorkflowRunsUseCase,
    GetWorkflowRunCommand,
    GetWorkflowRunUseCase,
    ListActiveRunsCommand,
    ListActiveRunsUseCase,
    ListWorkflowRunsCommand,
    ListWorkflowRunsUseCase,
)
from src.application.use_cases.workflow_versions import (
    GetWorkflowVersionCommand,
    GetWorkflowVersionUseCase,
    ListWorkflowVersionsCommand,
    ListWorkflowVersionsUseCase,
)
from src.domain.entities.shared import JsonDict, JsonValue
from src.domain.entities.workflow import (
    WorkflowRun,
    WorkflowVersion,
    parse_workflow_def,
)
from src.domain.exceptions import NotFoundError, ToolExecutionError

# Cap the preview output head returned to the model; total_track_count carries
# the true size so the model knows the head is a sample.
_PREVIEW_HEAD_LIMIT = 20

_RESOURCES: tuple[str, ...] = ("runs", "versions")
_RUN_SCOPES: tuple[str, ...] = ("history", "active", "latest")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


# --- preview_workflow -------------------------------------------------------

PREVIEW_WORKFLOW_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "workflow_def": {
            "type": "object",
            "description": (
                "A complete workflow definition (id, name, tasks) to dry-run "
                "against real library data. Destination nodes are not written — "
                "the preview only reports the tracks the pipeline would produce."
            ),
        },
    },
    "required": ["workflow_def"],
    "additionalProperties": False,
}


def _compact_preview_track(track: Mapping[str, object]) -> JsonDict:
    """Re-project one pre-serialized preview track, marking its free text.

    ``PreviewWorkflowResult.output_tracks`` are already plain dicts (title and
    artists as strings), so their user-originated display fields are wrapped in
    :class:`UserText` here before they cross the model boundary.
    """
    title = track.get("title")
    track_id = track.get("track_id")
    rank = track.get("rank")
    raw_artists = track.get("artists")
    if isinstance(raw_artists, str):
        artists: JsonValue = [user_text(raw_artists)]
    elif isinstance(raw_artists, Sequence):
        items: Sequence[object] = raw_artists
        artists = [user_text(a) for a in items if isinstance(a, str)]
    else:
        artists = []
    return {
        "track_id": track_id if isinstance(track_id, str) else None,
        "title": user_text(title) if isinstance(title, str) else None,
        "artists": artists,
        "rank": rank if isinstance(rank, int) else None,
    }


async def handle_preview_workflow(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    """Dry-run a workflow definition and return a compact preview summary.

    Breaks the standard dispatcher pattern: ``PreviewWorkflowUseCase`` owns its
    own sessions and takes a parsed ``WorkflowDef`` directly, so it is not
    wrapped in ``execute_use_case``. A malformed or invalid definition surfaces
    as ``ToolExecutionError`` so the model can fix it within the turn.
    """
    raw = tool_input.get("workflow_def")
    if not isinstance(raw, Mapping):
        raise ToolExecutionError("'workflow_def' must be a JSON object")

    try:
        wf_def = parse_workflow_def(raw)
    except (ValueError, TypeError, KeyError) as e:
        raise ToolExecutionError(
            f"'workflow_def' is not a parseable workflow definition: {e}"
        ) from e

    try:
        result = await PreviewWorkflowUseCase().execute(wf_def, user_id=ctx.user_id)
    except ValueError as e:
        # validate_workflow_def / the executor rejected the definition — hand
        # the reason back so the model can correct the pipeline and retry.
        raise ToolExecutionError(f"Preview could not run this workflow: {e}") from e

    head = [
        _compact_preview_track(t) for t in result.output_tracks[:_PREVIEW_HEAD_LIMIT]
    ]
    return {
        "total_track_count": result.total_track_count,
        "duration_ms": result.duration_ms,
        "metric_columns": list(result.metric_columns),
        "output_tracks": head,
        "node_summaries": [
            {
                "node_id": s.node_id,
                "node_type": s.node_type,
                "track_count": s.track_count,
            }
            for s in result.node_summaries
        ],
    }


# --- query_workflow_history -------------------------------------------------

QUERY_WORKFLOW_HISTORY_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "resource": {
            "type": "string",
            "enum": list(_RESOURCES),
            "description": (
                "Which history to read (default 'runs'). 'runs' reads workflow "
                "execution history; 'versions' reads a workflow's saved "
                "definition versions."
            ),
        },
        "scope": {
            "type": "string",
            "enum": list(_RUN_SCOPES),
            "description": (
                "resource='runs' only (default 'history'). 'history' lists past "
                "runs of ONE workflow and REQUIRES 'workflow_id' (supports "
                "'limit'/'offset'). 'active' lists in-flight runs across ALL "
                "workflows (no 'workflow_id'; supports 'limit'/'offset'). "
                "'latest' returns the most recent run for each workflow and "
                "REQUIRES 'workflow_ids'."
            ),
        },
        "workflow_id": {
            "type": "string",
            "description": (
                "A workflow UUID. Required for scope 'history', for a single-run "
                "lookup (with 'run_id'), and for resource='versions'."
            ),
        },
        "workflow_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "scope 'latest' only, REQUIRED: the workflow UUIDs to fetch the "
                "latest run for."
            ),
        },
        "run_id": {
            "type": "string",
            "description": (
                "resource='runs' only: a run UUID to fetch one run's detail "
                "instead of a listing. Requires 'workflow_id'."
            ),
        },
        "version": {
            "type": "integer",
            "description": (
                "resource='versions' only: a version number to fetch one "
                "version's detail instead of the version list. Requires "
                "'workflow_id'."
            ),
        },
        "limit": {
            "type": "integer",
            "description": (
                "Page size for scope 'history'/'active' (1-500, default 20)."
            ),
        },
        "offset": {
            "type": "integer",
            "description": (
                "Zero-based offset for scope 'history'/'active' (default 0)."
            ),
        },
    },
    "additionalProperties": False,
}


def _project_run(run: WorkflowRun) -> JsonDict:
    """Compact model-facing view of a WorkflowRun — ids raw, error left plain."""
    return {
        "run_id": str(run.id),
        "workflow_id": str(run.workflow_id),
        "run_number": run.run_number,
        "status": run.status,
        "created_at": _iso(run.created_at),
        "started_at": _iso(run.started_at),
        "completed_at": _iso(run.completed_at),
        "duration_ms": run.duration_ms,
        "output_track_count": run.output_track_count,
        "node_count": len(run.nodes),
        # System-generated (truncated exception text), not user free text.
        "error": run.error_message,
    }


def _project_version(version: WorkflowVersion) -> JsonDict:
    """Compact model-facing view of a WorkflowVersion.

    ``change_summary`` is system-generated (e.g. "Before revert to v2"), so it
    is left plain rather than wrapped as user text.
    """
    return {
        "version": version.version,
        "change_summary": version.change_summary,
        "created_at": _iso(version.created_at),
    }


async def handle_query_workflow_history(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    """Read run or version history, dispatching on ``resource``/``scope``."""
    resource = opt_choice(tool_input, "resource", _RESOURCES, "runs")
    if resource == "versions":
        return await _query_versions(tool_input, ctx)
    return await _query_runs(tool_input, ctx)


async def _query_runs(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    run_id = opt_uuid(tool_input, "run_id")
    if run_id is not None:
        return await _run_detail(tool_input, ctx, run_id)

    scope = opt_choice(tool_input, "scope", _RUN_SCOPES, "history")
    if scope == "active":
        return await _active_runs(tool_input, ctx)
    if scope == "latest":
        return await _latest_runs(tool_input, ctx)
    return await _run_history(tool_input, ctx)


async def _run_history(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    command = ListWorkflowRunsCommand(
        user_id=ctx.user_id,
        workflow_id=require_uuid(tool_input, "workflow_id"),
        limit=opt_int(tool_input, "limit", default=20),
        offset=opt_int(tool_input, "offset", default=0, minimum=0),
    )
    try:
        result = await execute_use_case(
            lambda uow: ListWorkflowRunsUseCase().execute(command, uow),
            user_id=ctx.user_id,
        )
    except NotFoundError as e:
        raise ToolExecutionError(
            f"No workflow with id {command.workflow_id} — call query_workflow "
            "history with resource 'runs' and scope 'active', or list_user_"
            "workflows, to find real workflow ids."
        ) from e
    return {
        "runs": [_project_run(r) for r in result.runs],
        "total_count": result.total_count,
    }


async def _active_runs(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    command = ListActiveRunsCommand(
        user_id=ctx.user_id,
        limit=opt_int(tool_input, "limit", default=50),
        offset=opt_int(tool_input, "offset", default=0, minimum=0),
    )
    result = await execute_use_case(
        lambda uow: ListActiveRunsUseCase().execute(command, uow),
        user_id=ctx.user_id,
    )
    return {
        "runs": [_project_run(r) for r in result.runs],
        "total_count": result.total_count,
    }


async def _latest_runs(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    command = GetLatestWorkflowRunsCommand(
        user_id=ctx.user_id,
        workflow_ids=require_uuid_list(tool_input, "workflow_ids"),
    )
    result = await execute_use_case(
        lambda uow: GetLatestWorkflowRunsUseCase().execute(command, uow),
        user_id=ctx.user_id,
    )
    return {
        "latest_runs": {
            str(workflow_id): _project_run(run)
            for workflow_id, run in result.latest_runs.items()
        }
    }


async def _run_detail(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext, run_id: UUID
) -> JsonValue:
    command = GetWorkflowRunCommand(
        user_id=ctx.user_id,
        workflow_id=require_uuid(tool_input, "workflow_id"),
        run_id=run_id,
    )
    try:
        result = await execute_use_case(
            lambda uow: GetWorkflowRunUseCase().execute(command, uow),
            user_id=ctx.user_id,
        )
    except NotFoundError as e:
        raise ToolExecutionError(
            f"No run {run_id} for workflow {command.workflow_id} — call "
            "query_workflow_history with scope 'history' to find real run ids."
        ) from e
    return {"run": _project_run(result.run)}


async def _query_versions(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    workflow_id = require_uuid(tool_input, "workflow_id")
    version = tool_input.get("version")
    if version is not None:
        return await _version_detail(tool_input, ctx, workflow_id)

    command = ListWorkflowVersionsCommand(user_id=ctx.user_id, workflow_id=workflow_id)
    try:
        result = await execute_use_case(
            lambda uow: ListWorkflowVersionsUseCase().execute(command, uow),
            user_id=ctx.user_id,
        )
    except NotFoundError as e:
        raise ToolExecutionError(
            f"No workflow with id {workflow_id} — call list_user_workflows to "
            "find real workflow ids."
        ) from e
    return {"versions": [_project_version(v) for v in result.versions]}


async def _version_detail(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext, workflow_id: UUID
) -> JsonValue:
    number = opt_int(tool_input, "version", default=1, minimum=1)
    command = GetWorkflowVersionCommand(
        user_id=ctx.user_id, workflow_id=workflow_id, version=number
    )
    try:
        result = await execute_use_case(
            lambda uow: GetWorkflowVersionUseCase().execute(command, uow),
            user_id=ctx.user_id,
        )
    except NotFoundError as e:
        raise ToolExecutionError(
            f"No version {number} for workflow {workflow_id} — call "
            "query_workflow_history with resource 'versions' to list real "
            "version numbers."
        ) from e
    detail = _project_version(result.version)
    detail["definition"] = {
        "id": result.version.definition.id,
        "name": user_text(result.version.definition.name),
        "task_count": len(result.version.definition.tasks),
    }
    return detail


SPECS: list[dict[str, object]] = [
    {
        "name": "preview_workflow",
        "description": (
            "Call this to dry-run a complete workflow definition against the "
            "user's real library and see the tracks it would produce, before "
            "saving or running it. Nothing is written — destination nodes are "
            "skipped. Returns the total track count, a head of the output "
            "tracks, per-node counts, and the run duration."
        ),
        "input_schema": PREVIEW_WORKFLOW_INPUT_SCHEMA,
        "dispatch": handle_preview_workflow,
        "use_cases": ("PreviewWorkflowUseCase",),
        "kind": "read",
    },
    {
        "name": "query_workflow_history",
        "description": (
            "Call this to read workflow run history or version history. "
            "resource='runs' (default): scope 'history' lists one workflow's "
            "past runs (needs workflow_id), scope 'active' lists in-flight runs "
            "across all workflows, scope 'latest' returns the newest run per "
            "workflow (needs workflow_ids), or pass run_id (with workflow_id) "
            "for one run's detail. resource='versions' (needs workflow_id) lists "
            "a workflow's saved versions, or pass version for one version's "
            "detail."
        ),
        "input_schema": QUERY_WORKFLOW_HISTORY_INPUT_SCHEMA,
        "dispatch": handle_query_workflow_history,
        "use_cases": (
            "ListWorkflowRunsUseCase",
            "ListActiveRunsUseCase",
            "GetLatestWorkflowRunsUseCase",
            "GetWorkflowRunUseCase",
            "ListWorkflowVersionsUseCase",
            "GetWorkflowVersionUseCase",
        ),
        "kind": "read",
    },
]
