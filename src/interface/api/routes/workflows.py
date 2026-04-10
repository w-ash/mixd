"""Workflow CRUD + validation + node catalog + execution + run history endpoints.

Each handler is 5-10 lines: parse request -> build Command -> execute_use_case() -> serialize.
All business logic lives in the use cases.
"""

# pyright: reportAny=false
# Legitimate Any: node config schema values, Coroutine type params

import asyncio
from asyncio import CancelledError
import contextlib
from datetime import datetime
from typing import Unpack
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from src.application.runner import execute_use_case
from src.application.use_cases.workflow_crud import (
    CreateWorkflowCommand,
    CreateWorkflowUseCase,
    DeleteWorkflowCommand,
    DeleteWorkflowUseCase,
    GetWorkflowCommand,
    GetWorkflowUseCase,
    ListWorkflowsCommand,
    ListWorkflowsUseCase,
    UpdateWorkflowCommand,
    UpdateWorkflowUseCase,
)
from src.application.use_cases.workflow_runs import (
    ExecuteWorkflowRunUseCase,
    GetLatestWorkflowRunsCommand,
    GetLatestWorkflowRunsUseCase,
    GetWorkflowRunCommand,
    GetWorkflowRunUseCase,
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
from src.application.workflows.node_config_fields import get_node_config_fields
from src.application.workflows.node_registry import list_nodes
from src.application.workflows.protocols import RunStatusKwargs
from src.application.workflows.validation import (
    is_validation_error,
    validate_workflow_def_detailed,
)
from src.config import get_logger
from src.config.constants import WorkflowConstants, truncate_error_message
from src.domain.entities.workflow import RunStatus, WorkflowDef, WorkflowRun
from src.domain.repositories import UnitOfWorkProtocol
from src.interface.api.deps import get_current_user_id
from src.interface.api.schemas.common import PaginatedResponse
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
    WorkflowValidationErrorSchema,
    WorkflowValidationRequest,
    WorkflowValidationResponse,
    WorkflowVersionSchema,
    config_field_to_schema,
    schema_to_workflow_def,
    to_run_detail,
    to_run_summary,
    to_version_schema,
    to_workflow_detail,
    to_workflow_summary,
)
from src.interface.api.services.background import (
    finalize_sse_operation,
    launch_background,
)
from src.interface.api.services.sse_operations import (
    build_terminal_event,
    prepare_sse_operation,
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
    include_templates: bool = Query(default=True),
) -> PaginatedResponse[WorkflowSummarySchema]:
    """List all workflows with pagination and last-run status."""

    async def _fetch(
        uow: UnitOfWorkProtocol,
    ) -> PaginatedResponse[WorkflowSummarySchema]:
        result = await ListWorkflowsUseCase().execute(
            ListWorkflowsCommand(user_id=user_id, include_templates=include_templates),
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
        lambda: _execute_preview_background(
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


@router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(
    workflow_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> Response:
    """Delete a user workflow. Template workflows cannot be deleted."""
    command = DeleteWorkflowCommand(user_id=user_id, workflow_id=workflow_id)
    await execute_use_case(
        lambda uow: DeleteWorkflowUseCase().execute(command, uow),
        user_id=user_id,
    )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Run endpoints
# ---------------------------------------------------------------------------


@router.post("/{workflow_id}/run", status_code=202)
async def run_workflow_endpoint(
    workflow_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> WorkflowRunStartedResponse:
    """Start a workflow execution. Returns immediately with operation_id + run_id."""
    # 1. Create run record (PENDING) + check execution guard
    command = RunWorkflowCommand(user_id=user_id, workflow_id=workflow_id)
    result = await execute_use_case(
        lambda uow: RunWorkflowUseCase().execute(command, uow),
        user_id=user_id,
    )
    run_id = result.run_id
    workflow = result.workflow

    # 2. Register SSE queue
    operation_id, sse_queue = await prepare_sse_operation()

    # 3. Launch background execution
    launch_background(
        f"workflow_run_{operation_id}",
        lambda: _execute_workflow_background(
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
# Background execution (SSE lifecycle only — business logic in use case)
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _run_repo_session():
    """Short-lived independent session for run/node status updates.

    Status updates use their own session so they survive workflow failures.
    Lives here (interface layer) because it imports infrastructure directly.
    """
    from src.infrastructure.persistence.database.db_connection import get_session
    from src.infrastructure.persistence.repositories.workflow.runs import (
        WorkflowRunRepository,
    )

    async with get_session(rollback=False) as session:
        yield WorkflowRunRepository(session)
        await session.commit()


async def _update_run_status(
    run_id: UUID,
    status: RunStatus,
    **kwargs: Unpack[RunStatusKwargs],
) -> None:
    """Concrete implementation of RunStatusUpdater."""
    async with _run_repo_session() as repo:
        await repo.update_run_status(run_id, status, **kwargs)


async def _update_node_status(
    run_id: UUID,
    node_id: str,
    status: RunStatus,
    *,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    duration_ms: int | None = None,
    input_track_count: int | None = None,
    output_track_count: int | None = None,
    error_message: str | None = None,
    node_details: dict[str, object] | None = None,
) -> None:
    """Concrete implementation of NodeStatusUpdater."""
    async with _run_repo_session() as repo:
        await repo.update_node_status(
            run_id,
            node_id,
            status,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            input_track_count=input_track_count,
            output_track_count=output_track_count,
            error_message=error_message,
            node_details=node_details,
        )


async def _execute_workflow_background(
    operation_id: str,
    workflow_def: WorkflowDef,
    run_id: UUID,
    sse_queue: asyncio.Queue[object],
    user_id: str,
) -> None:
    """Execute workflow in background, pushing SSE events for the run lifecycle.

    Delegates all business logic (run status management, workflow execution)
    to ``ExecuteWorkflowRunUseCase`` in the application layer. This function
    only handles SSE event emission and cleanup.
    """
    try:
        use_case = ExecuteWorkflowRunUseCase(
            update_run_status=_update_run_status,
            update_node_status=_update_node_status,
        )
        run_result = await use_case.execute(
            workflow_def, run_id, sse_queue=sse_queue, user_id=user_id
        )

        # Push terminal SSE event based on use case result
        if run_result.status == WorkflowConstants.RUN_STATUS_COMPLETED:
            await sse_queue.put(
                build_terminal_event(
                    "evt_final",
                    WorkflowConstants.SSE_EVENT_COMPLETE,
                    operation_id,
                    WorkflowConstants.RUN_STATUS_COMPLETED,
                    run_id=run_id,
                    output_track_count=run_result.output_track_count,
                    duration_ms=run_result.duration_ms,
                )
            )
        else:
            await sse_queue.put(
                build_terminal_event(
                    "evt_error",
                    WorkflowConstants.SSE_EVENT_ERROR,
                    operation_id,
                    WorkflowConstants.RUN_STATUS_FAILED,
                    run_id=run_id,
                    error_message=truncate_error_message(
                        run_result.error_message or "Unknown error",
                        WorkflowConstants.SSE_ERROR_MAX_LENGTH,
                    ),
                )
            )

    except CancelledError:
        # Best-effort push of error SSE event on cancellation
        with contextlib.suppress(CancelledError, Exception):
            await sse_queue.put(
                build_terminal_event(
                    "evt_error",
                    WorkflowConstants.SSE_EVENT_ERROR,
                    operation_id,
                    WorkflowConstants.RUN_STATUS_FAILED,
                    run_id=run_id,
                    error_message=WorkflowConstants.CANCELLED_BY_SERVER_MESSAGE,
                )
            )

    finally:
        await finalize_sse_operation(operation_id)


async def _execute_preview_background(
    operation_id: str,
    workflow_def: WorkflowDef,
    sse_queue: asyncio.Queue[object],
    user_id: str,
) -> None:
    """Execute workflow preview in background, pushing SSE events.

    Delegates to ``PreviewWorkflowUseCase`` which runs with ``dry_run=True``.
    No run records are created — previews are ephemeral.
    """
    from src.application.use_cases.workflow_preview import PreviewWorkflowUseCase

    try:
        use_case = PreviewWorkflowUseCase()
        preview_result = await use_case.execute(
            workflow_def, sse_queue=sse_queue, user_id=user_id
        )

        await sse_queue.put(
            build_terminal_event(
                "evt_final",
                WorkflowConstants.SSE_EVENT_PREVIEW_COMPLETE,
                operation_id,
                WorkflowConstants.RUN_STATUS_COMPLETED,
                output_tracks=preview_result.output_tracks,
                total_track_count=preview_result.total_track_count,
                metric_columns=preview_result.metric_columns,
                node_summaries=[
                    {
                        "node_id": s.node_id,
                        "node_type": s.node_type,
                        "track_count": s.track_count,
                        "sample_titles": s.sample_titles,
                    }
                    for s in preview_result.node_summaries
                ],
                duration_ms=preview_result.duration_ms,
            )
        )

    except (CancelledError, Exception) as exc:
        error_msg = (
            WorkflowConstants.CANCELLED_BY_SERVER_MESSAGE
            if isinstance(exc, CancelledError)
            else truncate_error_message(
                str(exc), WorkflowConstants.SSE_ERROR_MAX_LENGTH
            )
        )
        with contextlib.suppress(CancelledError, Exception):
            await sse_queue.put(
                build_terminal_event(
                    "evt_error",
                    WorkflowConstants.SSE_EVENT_ERROR,
                    operation_id,
                    WorkflowConstants.RUN_STATUS_FAILED,
                    error_message=error_msg,
                )
            )

    finally:
        await finalize_sse_operation(operation_id)
