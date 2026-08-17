"""Workflow CRUD + validation + node catalog + execution + run history endpoints.

Each handler is 5-10 lines: parse request -> build Command -> execute_use_case() -> serialize.
All business logic lives in the use cases.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from src.application.runner import execute_use_case
from src.application.use_cases.workflow_crud import (
    CreateWorkflowCommand,
    CreateWorkflowUseCase,
    DuplicateWorkflowCommand,
    DuplicateWorkflowUseCase,
    GetWorkflowCommand,
    GetWorkflowUseCase,
    InstantiateWorkflowCommand,
    InstantiateWorkflowUseCase,
    ListWorkflowsCommand,
    ListWorkflowsUseCase,
    UpdateWorkflowCommand,
    UpdateWorkflowUseCase,
)
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
    RevertWorkflowVersionCommand,
    RevertWorkflowVersionUseCase,
)
from src.application.workflows.definition.loader import list_workflow_defs
from src.application.workflows.definition.validation import (
    is_validation_error,
    validate_workflow_def_detailed,
)
from src.application.workflows.nodes.config_fields import get_node_config_fields
from src.application.workflows.nodes.registry import list_nodes
from src.config import get_logger
from src.domain.entities.workflow import Workflow, WorkflowDef, WorkflowRun
from src.domain.exceptions import NotFoundError, WorkflowAlreadyRunningError
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.interface.api.deps import get_current_user_id
from src.interface.api.routes._schedule_ops import (
    delete_schedule,
    get_schedule,
    toggle_schedule,
    upsert_schedule,
)
from src.interface.api.schemas.common import PaginatedResponse
from src.interface.api.schemas.schedules import (
    ScheduleResponse,
    ScheduleToggleRequest,
    ScheduleUpsertRequest,
)
from src.interface.api.schemas.workflows import (
    CreateWorkflowRequest,
    NodeTypeInfoSchema,
    PreviewStartedResponse,
    UpdateWorkflowRequest,
    WorkflowDetailSchema,
    WorkflowRunDetailSchema,
    WorkflowRunStartedResponse,
    WorkflowRunSummarySchema,
    WorkflowSummarySchema,
    WorkflowTemplateSchema,
    WorkflowValidationErrorSchema,
    WorkflowValidationRequest,
    WorkflowValidationResponse,
    WorkflowVersionSchema,
    config_field_to_schema,
    schema_to_workflow_def,
    to_run_detail,
    to_run_summary,
    to_template_schema,
    to_version_schema,
    to_workflow_detail,
    to_workflow_summary,
)
from src.interface.api.services.background import (
    finalize_sse_operation,
    launch_background,
)
from src.interface.api.services.sse_operations import (
    acquire_operation_slot,
    prepare_sse_operation,
)
from src.interface.api.services.workflow_execution import (
    execute_preview_background,
    launch_workflow_run,
)

logger = get_logger(__name__).bind(service="workflows_api")

router = APIRouter(prefix="/workflows", tags=["workflows"])


async def _detail_with_run_summary(
    workflow: Workflow, user_id: str, uow: UnitOfWorkProtocol
) -> WorkflowDetailSchema:
    """Serialize a detail payload with its last run and completed-run count.

    Every handler returning an *existing* workflow goes through here. The web
    client writes these responses straight into its detail query cache, so a
    defaulted zero count would render as fact until the next refetch — the
    counts have to be real on every path, not just the list.
    """
    latest = await GetLatestWorkflowRunsUseCase().execute(
        GetLatestWorkflowRunsCommand(user_id=user_id, workflow_ids=[workflow.id]),
        uow,
    )
    return to_workflow_detail(
        workflow,
        last_run=latest.latest_runs.get(workflow.id),
        successful_run_count=latest.successful_run_counts.get(workflow.id, 0),
    )


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------


@router.get("")
async def list_workflows(
    user_id: str = Depends(get_current_user_id),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[WorkflowSummarySchema]:
    """List all workflows with pagination and last-run status."""

    async def _fetch(
        uow: UnitOfWorkProtocol,
    ) -> PaginatedResponse[WorkflowSummarySchema]:
        result = await ListWorkflowsUseCase().execute(
            ListWorkflowsCommand(user_id=user_id),
            uow,
        )
        workflows = result.workflows[offset : offset + limit]

        # Batch-fetch latest runs + completed-run counts for this page
        workflow_ids = [w.id for w in workflows]
        latest_runs: dict[UUID, WorkflowRun] = {}
        run_counts: dict[UUID, int] = {}
        if workflow_ids:
            latest_result = await GetLatestWorkflowRunsUseCase().execute(
                GetLatestWorkflowRunsCommand(
                    user_id=user_id, workflow_ids=workflow_ids
                ),
                uow,
            )
            latest_runs = latest_result.latest_runs
            run_counts = latest_result.successful_run_counts

        return PaginatedResponse(
            data=[
                to_workflow_summary(
                    w,
                    last_run=latest_runs.get(w.id),
                    successful_run_count=run_counts.get(w.id, 0),
                )
                for w in workflows
            ],
            total=result.total_count,
            limit=limit,
            offset=offset,
        )

    return await execute_use_case(_fetch, user_id=user_id)


@router.post("", status_code=201)
async def create_workflow(
    body: CreateWorkflowRequest,
    user_id: str = Depends(get_current_user_id),
) -> WorkflowDetailSchema:
    """Create a new user workflow."""
    definition = schema_to_workflow_def(body.definition)
    command = CreateWorkflowCommand(user_id=user_id, definition=definition)
    result = await execute_use_case(
        lambda uow: CreateWorkflowUseCase().execute(command, uow),
        user_id=user_id,
    )
    return to_workflow_detail(result.workflow)


# ---------------------------------------------------------------------------
# Template gallery + instantiation (built-in defs, not persisted rows)
# ---------------------------------------------------------------------------


@router.get("/templates")
async def list_workflow_templates() -> list[WorkflowTemplateSchema]:
    """List the built-in workflow templates (file-backed gallery)."""
    return [to_template_schema(d) for d in list_workflow_defs()]


@router.post("/templates/{template_id}/use", status_code=201)
async def use_workflow_template(
    template_id: str,
    user_id: str = Depends(get_current_user_id),
) -> WorkflowDetailSchema:
    """Instantiate a built-in template as a new user-owned, editable workflow."""
    wf_def = next((d for d in list_workflow_defs() if d.id == template_id), None)
    if wf_def is None:
        raise NotFoundError(f"Template '{template_id}' not found")
    result = await execute_use_case(
        lambda uow: InstantiateWorkflowUseCase().execute(
            InstantiateWorkflowCommand(user_id=user_id, definition=wf_def), uow
        ),
        user_id=user_id,
    )
    return to_workflow_detail(result.workflow)


@router.post("/{workflow_id}/duplicate", status_code=201)
async def duplicate_workflow(
    workflow_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> WorkflowDetailSchema:
    """Duplicate any workflow into a new user-owned, editable copy."""
    result = await execute_use_case(
        lambda uow: DuplicateWorkflowUseCase().execute(
            DuplicateWorkflowCommand(user_id=user_id, workflow_id=workflow_id), uow
        ),
        user_id=user_id,
    )
    return to_workflow_detail(result.workflow)


@router.get("/nodes")
async def list_node_types() -> list[NodeTypeInfoSchema]:
    """List all available workflow node types with rich config field metadata."""
    all_fields = get_node_config_fields()
    nodes = list_nodes()
    result: list[NodeTypeInfoSchema] = []
    for node_id, meta in nodes.items():
        fields = all_fields.get(node_id, ())
        result.append(
            NodeTypeInfoSchema(
                type=node_id,
                category=meta["category"],
                description=meta.get("description", ""),
                config_fields=[config_field_to_schema(f) for f in fields],
                required_config=[f.key for f in fields if f.required],
                optional_config=[f.key for f in fields if not f.required],
            )
        )
    return result


@router.get("/active-runs")
async def list_active_runs(
    user_id: str = Depends(get_current_user_id),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[WorkflowRunSummarySchema]:
    """List the caller's in-flight runs across all workflows.

    Cross-instance, DB-backed source for reconnecting the detail page to a live
    run after reload and for a future "a run is happening" sidebar indicator.
    Declared before ``/{workflow_id}`` so the literal path wins over the param.
    """
    command = ListActiveRunsCommand(user_id=user_id, limit=limit, offset=offset)
    result = await execute_use_case(
        lambda uow: ListActiveRunsUseCase().execute(command, uow),
        user_id=user_id,
    )
    return PaginatedResponse(
        data=[to_run_summary(r) for r in result.runs],
        total=result.total_count,
        limit=limit,
        offset=offset,
    )


@router.post("/validate")
async def validate_workflow(
    body: WorkflowValidationRequest,
) -> WorkflowValidationResponse:
    """Validate a workflow definition without persisting."""
    definition = schema_to_workflow_def(body.definition)
    items = validate_workflow_def_detailed(definition)
    return WorkflowValidationResponse(
        valid=not any(is_validation_error(item) for item in items),
        errors=[WorkflowValidationErrorSchema(**e) for e in items],
    )


# ---------------------------------------------------------------------------
# Preview endpoints (run the graph, skip destination writes)
# ---------------------------------------------------------------------------


@router.post("/preview", status_code=202)
async def preview_unsaved_workflow(
    body: CreateWorkflowRequest,
    user_id: str = Depends(get_current_user_id),
) -> PreviewStartedResponse:
    """Preview an unsaved workflow definition. Returns operation_id for SSE.

    Sources materialize canonical rows (idempotent); destinations are skipped.
    """
    definition = schema_to_workflow_def(body.definition)
    return await _start_preview(definition, user_id)


@router.post("/{workflow_id}/preview", status_code=202)
async def preview_saved_workflow(
    workflow_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> PreviewStartedResponse:
    """Preview a saved workflow. Returns operation_id for SSE.

    Same write semantics as the unsaved form; 409 while a run is active.
    """
    command = GetWorkflowCommand(user_id=user_id, workflow_id=workflow_id)
    result = await execute_use_case(
        lambda uow: GetWorkflowUseCase().execute(command, uow),
        user_id=user_id,
    )
    return await _start_preview(
        result.workflow.definition, user_id, workflow_id=workflow_id
    )


async def _start_preview(
    workflow_def: WorkflowDef, user_id: str, workflow_id: UUID | None = None
) -> PreviewStartedResponse:
    """Shared kickoff: run guard, concurrency slot, SSE queue, background task.

    A preview writes canonical tracks, so it gets run-style protections:
    best-effort 409 against an active run (saved workflows only —
    ``workflow_id`` is the row id the run guard keys on; previews create no
    run row, and the residual race degrades to write contention the ingest
    path retries), and an operation-slot claim so previews count against the
    global 429 cap.
    """
    if workflow_id is not None:
        active_runs = await execute_use_case(
            lambda uow: ListActiveRunsUseCase().execute(
                ListActiveRunsCommand(user_id=user_id), uow
            ),
            user_id=user_id,
        )
        if any(run.workflow_id == workflow_id for run in active_runs.runs):
            raise WorkflowAlreadyRunningError(str(workflow_id))

    operation_id, sse_queue = await prepare_sse_operation()
    try:
        # Slot released by execute_preview_background's finally.
        acquire_operation_slot(operation_id)
    except Exception:
        # 429 — tear down the queue we just registered so it doesn't leak.
        # Grace 0: no client has connected yet, and the default 30s read
        # window would block this request.
        await finalize_sse_operation(operation_id, grace_period_seconds=0.0)
        raise

    launch_background(
        f"workflow_preview_{operation_id}",
        lambda: execute_preview_background(
            operation_id, workflow_def, sse_queue, user_id
        ),
    )

    return PreviewStartedResponse(operation_id=operation_id)


@router.get("/{workflow_id}")
async def get_workflow(
    workflow_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> WorkflowDetailSchema:
    """Get a workflow by ID with full definition."""

    async def _fetch(uow: UnitOfWorkProtocol) -> WorkflowDetailSchema:
        result = await GetWorkflowUseCase().execute(
            GetWorkflowCommand(user_id=user_id, workflow_id=workflow_id), uow
        )
        return await _detail_with_run_summary(result.workflow, user_id, uow)

    return await execute_use_case(_fetch, user_id=user_id)


@router.patch("/{workflow_id}")
async def update_workflow(
    workflow_id: UUID,
    body: UpdateWorkflowRequest,
    user_id: str = Depends(get_current_user_id),
) -> WorkflowDetailSchema:
    """Update a user workflow's definition. Template workflows cannot be modified."""
    definition = schema_to_workflow_def(body.definition)
    command = UpdateWorkflowCommand(
        user_id=user_id, workflow_id=workflow_id, definition=definition
    )

    async def _update(uow: UnitOfWorkProtocol) -> WorkflowDetailSchema:
        result = await UpdateWorkflowUseCase().execute(command, uow)
        return await _detail_with_run_summary(result.workflow, user_id, uow)

    return await execute_use_case(_update, user_id=user_id)


# ---------------------------------------------------------------------------
# Run endpoints
# ---------------------------------------------------------------------------


@router.post("/{workflow_id}/run", status_code=202)
async def run_workflow_endpoint(
    workflow_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> WorkflowRunStartedResponse:
    """Start a workflow execution. Returns immediately with operation_id + run_id."""
    return await launch_workflow_run(workflow_id, user_id)


@router.get("/{workflow_id}/runs")
async def list_workflow_runs(
    workflow_id: UUID,
    user_id: str = Depends(get_current_user_id),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[WorkflowRunSummarySchema]:
    """List execution history for a workflow."""
    command = ListWorkflowRunsCommand(
        user_id=user_id, workflow_id=workflow_id, limit=limit, offset=offset
    )
    result = await execute_use_case(
        lambda uow: ListWorkflowRunsUseCase().execute(command, uow),
        user_id=user_id,
    )
    return PaginatedResponse(
        data=[to_run_summary(r) for r in result.runs],
        total=result.total_count,
        limit=limit,
        offset=offset,
    )


@router.get("/{workflow_id}/runs/{run_id}")
async def get_workflow_run(
    workflow_id: UUID,
    run_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> WorkflowRunDetailSchema:
    """Get a single run with node execution details."""
    command = GetWorkflowRunCommand(
        user_id=user_id, workflow_id=workflow_id, run_id=run_id
    )
    result = await execute_use_case(
        lambda uow: GetWorkflowRunUseCase().execute(command, uow),
        user_id=user_id,
    )
    return to_run_detail(result.run)


# ---------------------------------------------------------------------------
# Version endpoints
# ---------------------------------------------------------------------------


@router.get("/{workflow_id}/versions")
async def list_workflow_versions(
    workflow_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> list[WorkflowVersionSchema]:
    """List version history for a workflow."""
    command = ListWorkflowVersionsCommand(user_id=user_id, workflow_id=workflow_id)
    result = await execute_use_case(
        lambda uow: ListWorkflowVersionsUseCase().execute(command, uow),
        user_id=user_id,
    )
    return [to_version_schema(v) for v in result.versions]


@router.get("/{workflow_id}/versions/{version}")
async def get_workflow_version(
    workflow_id: UUID,
    version: int,
    user_id: str = Depends(get_current_user_id),
) -> WorkflowVersionSchema:
    """Get a specific version with full definition."""
    command = GetWorkflowVersionCommand(
        user_id=user_id, workflow_id=workflow_id, version=version
    )
    result = await execute_use_case(
        lambda uow: GetWorkflowVersionUseCase().execute(command, uow),
        user_id=user_id,
    )
    return to_version_schema(result.version)


@router.post("/{workflow_id}/versions/{version}/revert")
async def revert_workflow_version(
    workflow_id: UUID,
    version: int,
    user_id: str = Depends(get_current_user_id),
) -> WorkflowDetailSchema:
    """Revert a workflow to a previous version. Creates a new version record."""
    command = RevertWorkflowVersionCommand(
        user_id=user_id, workflow_id=workflow_id, version=version
    )

    async def _revert(uow: UnitOfWorkProtocol) -> WorkflowDetailSchema:
        result = await RevertWorkflowVersionUseCase().execute(command, uow)
        return await _detail_with_run_summary(result.workflow, user_id, uow)

    return await execute_use_case(_revert, user_id=user_id)


# ---------------------------------------------------------------------------
# Schedule endpoints (workflow target) — sync targets live on the schedules
# router. PUT is idempotent per workflow (201 created / 200 replaced).
# ---------------------------------------------------------------------------


@router.put("/{workflow_id}/schedule")
async def upsert_workflow_schedule(
    workflow_id: UUID,
    body: ScheduleUpsertRequest,
    response: Response,
    user_id: str = Depends(get_current_user_id),
) -> ScheduleResponse:
    """Create or replace this workflow's schedule (201 created / 200 replaced).

    A workflow the user doesn't own (or that doesn't exist) is rejected by the
    use case's target check → ``NotFoundError`` → 404.
    """
    return await upsert_schedule(
        user_id=user_id, body=body, response=response, workflow_id=workflow_id
    )


@router.get("/{workflow_id}/schedule")
async def get_workflow_schedule(
    workflow_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> ScheduleResponse:
    """Return this workflow's schedule, or 404 if none is configured."""
    return await get_schedule(
        user_id=user_id,
        not_found_message=f"No schedule for workflow {workflow_id}",
        workflow_id=workflow_id,
    )


@router.patch("/{workflow_id}/schedule")
async def toggle_workflow_schedule(
    workflow_id: UUID,
    body: ScheduleToggleRequest,
    user_id: str = Depends(get_current_user_id),
) -> ScheduleResponse:
    """Enable or disable this workflow's schedule (preserves run history)."""
    return await toggle_schedule(
        user_id=user_id, enabled=body.enabled, workflow_id=workflow_id
    )


@router.delete("/{workflow_id}/schedule", status_code=204)
async def delete_workflow_schedule(
    workflow_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> Response:
    """Delete this workflow's schedule (404 if none). Run history is preserved."""
    return await delete_schedule(user_id=user_id, workflow_id=workflow_id)
