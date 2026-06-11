"""Workflow CRUD + validation + node catalog + execution + run history endpoints.

Each handler is 5-10 lines: parse request -> build Command -> execute_use_case() -> serialize.
All business logic lives in the use cases.
"""

from datetime import UTC, datetime
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
    RunWorkflowCommand,
    RunWorkflowUseCase,
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
from src.config.constants import WorkflowConstants
from src.domain.entities.workflow import WorkflowDef, WorkflowRun
from src.domain.exceptions import NotFoundError
from src.domain.repositories import UnitOfWorkProtocol
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
from src.interface.api.services.sse_operations import prepare_sse_operation
from src.interface.api.services.workflow_execution import (
    execute_preview_background,
    execute_workflow_background,
)

logger = get_logger(__name__).bind(service="workflows_api")

router = APIRouter(prefix="/workflows", tags=["workflows"])


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

        # Batch-fetch latest runs for all workflows on this page
        workflow_ids = [w.id for w in workflows]
        latest_runs: dict[UUID, WorkflowRun] = {}
        if workflow_ids:
            latest_result = await GetLatestWorkflowRunsUseCase().execute(
                GetLatestWorkflowRunsCommand(
                    user_id=user_id, workflow_ids=workflow_ids
                ),
                uow,
            )
            latest_runs = latest_result.latest_runs

        return PaginatedResponse(
            data=[
                to_workflow_summary(w, last_run=latest_runs.get(w.id))
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
# Preview endpoints (dry-run execution)
# ---------------------------------------------------------------------------


@router.post("/preview", status_code=202)
async def preview_unsaved_workflow(
    body: CreateWorkflowRequest,
    user_id: str = Depends(get_current_user_id),
) -> PreviewStartedResponse:
    """Preview an unsaved workflow definition (dry-run). Returns operation_id for SSE."""
    definition = schema_to_workflow_def(body.definition)
    return await _start_preview(definition, user_id)


@router.post("/{workflow_id}/preview", status_code=202)
async def preview_saved_workflow(
    workflow_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> PreviewStartedResponse:
    """Preview a saved workflow (dry-run). Returns operation_id for SSE."""
    command = GetWorkflowCommand(user_id=user_id, workflow_id=workflow_id)
    result = await execute_use_case(
        lambda uow: GetWorkflowUseCase().execute(command, uow),
        user_id=user_id,
    )
    return await _start_preview(result.workflow.definition, user_id)


async def _start_preview(
    workflow_def: WorkflowDef, user_id: str
) -> PreviewStartedResponse:
    """Shared logic: register SSE queue, launch background preview."""
    operation_id, sse_queue = await prepare_sse_operation()

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
        workflow = result.workflow
        latest_result = await GetLatestWorkflowRunsUseCase().execute(
            GetLatestWorkflowRunsCommand(user_id=user_id, workflow_ids=[workflow.id]),
            uow,
        )
        last_run = latest_result.latest_runs.get(workflow.id)
        return to_workflow_detail(workflow, last_run=last_run)

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
    result = await execute_use_case(
        lambda uow: UpdateWorkflowUseCase().execute(command, uow),
        user_id=user_id,
    )
    return to_workflow_detail(result.workflow)


# ---------------------------------------------------------------------------
# Run endpoints
# ---------------------------------------------------------------------------


@router.post("/{workflow_id}/run", status_code=202)
async def run_workflow_endpoint(
    workflow_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> WorkflowRunStartedResponse:
    """Start a workflow execution. Returns immediately with operation_id + run_id."""
    # 1. Allocate operation_id + SSE queue first so the run row can persist
    # the operation_id. The snapshot endpoint resolves operation_id -> run
    # via the DB, so the link must exist from the moment the run is created.
    operation_id, sse_queue = await prepare_sse_operation()

    try:
        # 2. Create run record (PENDING) + check execution guard, with operation_id
        command = RunWorkflowCommand(
            user_id=user_id, workflow_id=workflow_id, operation_id=operation_id
        )
        result = await execute_use_case(
            lambda uow: RunWorkflowUseCase().execute(command, uow),
            user_id=user_id,
        )
    except Exception:
        # Use case failed (e.g., 409 already-running). Tear down the SSE
        # queue we just registered so it doesn't leak.
        await finalize_sse_operation(operation_id)
        raise

    run_id = result.run_id
    workflow = result.workflow

    # 3. Push run_accepted before launching the bg task. The queue buffers,
    # so even if the first node takes seconds to produce output the SSE consumer
    # sees activity within ~50 ms of the POST. evt_accept is a string id so it bypasses the
    # numeric Last-Event-ID resume regex (one-shot signaling event).
    await sse_queue.put({
        "id": WorkflowConstants.SSE_EVENT_ID_RUN_ACCEPTED,
        "event": WorkflowConstants.SSE_EVENT_RUN_ACCEPTED,
        "data": {
            "operation_id": operation_id,
            "run_id": str(run_id),
            "workflow_id": str(workflow.definition.id),
            "task_count": len(workflow.definition.tasks),
            "accepted_at": datetime.now(UTC).isoformat(),
        },
    })

    # 4. Launch background execution
    launch_background(
        f"workflow_run_{operation_id}",
        lambda: execute_workflow_background(
            operation_id, workflow.definition, run_id, sse_queue, user_id
        ),
        workflow_id=workflow.definition.id,
        run_id=run_id,
    )

    return WorkflowRunStartedResponse(operation_id=operation_id, run_id=run_id)


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
    result = await execute_use_case(
        lambda uow: RevertWorkflowVersionUseCase().execute(command, uow),
        user_id=user_id,
    )
    return to_workflow_detail(result.workflow)


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
