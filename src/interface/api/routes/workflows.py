"""Workflow CRUD + validation + node catalog endpoints.

Each handler is 5-10 lines: parse request -> build Command -> execute_use_case() -> serialize.
All business logic lives in the use cases.
"""

# pyright: reportExplicitAny=false
# Legitimate Any: node config schema values

from fastapi import APIRouter, Query
from fastapi.responses import Response

from src.application.runner import execute_use_case
from src.application.use_cases.workflow_crud import (
    CreateWorkflowCommand,
    CreateWorkflowUseCase,
    DeleteWorkflowCommand,
    DeleteWorkflowUseCase,
    GetWorkflowCommand,
    GetWorkflowUseCase,
    ListWorkflowsUseCase,
    UpdateWorkflowCommand,
    UpdateWorkflowUseCase,
)
import src.application.workflows.node_catalog as _node_catalog  # noqa: F401  # pyright: ignore[reportUnusedImport] — side-effect: registers nodes
from src.application.workflows.node_registry import list_nodes
from src.application.workflows.validation import (
    get_node_config_schema,
    validate_workflow_def_detailed,
)
from src.interface.api.schemas.common import PaginatedResponse
from src.interface.api.schemas.workflows import (
    CreateWorkflowRequest,
    NodeTypeInfoSchema,
    UpdateWorkflowRequest,
    WorkflowDetailSchema,
    WorkflowSummarySchema,
    WorkflowValidationErrorSchema,
    WorkflowValidationRequest,
    WorkflowValidationResponse,
    schema_to_workflow_def,
    to_workflow_detail,
    to_workflow_summary,
)

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.get("")
async def list_workflows(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    include_templates: bool = Query(default=True),
) -> PaginatedResponse[WorkflowSummarySchema]:
    """List all workflows with pagination."""
    result = await execute_use_case(
        lambda uow: ListWorkflowsUseCase().execute(
            uow, include_templates=include_templates
        )
    )
    workflows = result.workflows[offset : offset + limit]
    return PaginatedResponse(
        data=[to_workflow_summary(w) for w in workflows],
        total=result.total_count,
        limit=limit,
        offset=offset,
    )


@router.post("", status_code=201)
async def create_workflow(body: CreateWorkflowRequest) -> WorkflowDetailSchema:
    """Create a new user workflow."""
    definition = schema_to_workflow_def(body.definition)
    command = CreateWorkflowCommand(definition=definition)
    result = await execute_use_case(
        lambda uow: CreateWorkflowUseCase().execute(command, uow)
    )
    return to_workflow_detail(result.workflow)


@router.get("/nodes")
async def list_node_types() -> list[NodeTypeInfoSchema]:
    """List all available workflow node types with config schemas."""
    config_schemas = get_node_config_schema()
    nodes = list_nodes()
    result: list[NodeTypeInfoSchema] = []
    for node_id, meta in nodes.items():
        config_schema = config_schemas.get(node_id, {})
        result.append(
            NodeTypeInfoSchema(
                type=node_id,
                category=meta["category"],
                description=meta.get("description", ""),
                required_config=list(config_schema.keys()),
            )
        )
    return result


@router.post("/validate")
async def validate_workflow(
    body: WorkflowValidationRequest,
) -> WorkflowValidationResponse:
    """Validate a workflow definition without persisting."""
    definition = schema_to_workflow_def(body.definition)
    errors = validate_workflow_def_detailed(definition)
    return WorkflowValidationResponse(
        valid=len(errors) == 0,
        errors=[WorkflowValidationErrorSchema(**e) for e in errors],
    )


@router.get("/{workflow_id}")
async def get_workflow(workflow_id: int) -> WorkflowDetailSchema:
    """Get a workflow by ID with full definition."""
    command = GetWorkflowCommand(workflow_id=workflow_id)
    result = await execute_use_case(
        lambda uow: GetWorkflowUseCase().execute(command, uow)
    )
    return to_workflow_detail(result.workflow)


@router.patch("/{workflow_id}")
async def update_workflow(
    workflow_id: int, body: UpdateWorkflowRequest
) -> WorkflowDetailSchema:
    """Update a user workflow's definition. Template workflows cannot be modified."""
    definition = schema_to_workflow_def(body.definition)
    command = UpdateWorkflowCommand(workflow_id=workflow_id, definition=definition)
    result = await execute_use_case(
        lambda uow: UpdateWorkflowUseCase().execute(command, uow)
    )
    return to_workflow_detail(result.workflow)


@router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(workflow_id: int) -> Response:
    """Delete a user workflow. Template workflows cannot be deleted."""
    command = DeleteWorkflowCommand(workflow_id=workflow_id)
    await execute_use_case(lambda uow: DeleteWorkflowUseCase().execute(command, uow))
    return Response(status_code=204)
