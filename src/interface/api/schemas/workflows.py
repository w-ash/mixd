"""Pydantic v2 schemas for workflow API endpoints.

Domain-to-schema conversion functions translate attrs entities into
Pydantic models for JSON serialization.
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: node config values are heterogeneous, model_dump() returns dict[str, Any]

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from src.domain.entities.workflow import (
    RunStatus,
    Workflow,
    WorkflowDef,
    WorkflowRun,
    WorkflowTaskDef,
)

# --- Definition schemas (mirror domain entities) ---


class WorkflowTaskDefSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    type: str
    config: dict[str, Any] = {}
    upstream: list[str] = []
    result_key: str | None = None


class WorkflowDefSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str = ""
    version: str = "1.0"
    tasks: list[WorkflowTaskDefSchema] = []


# --- Response schemas ---


class LastRunSchema(BaseModel):
    """Lightweight last-run summary for the workflow list page."""

    id: int
    status: RunStatus
    definition_version: int = 1
    completed_at: datetime | None = None
    output_track_count: int | None = None


class WorkflowSummarySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    is_template: bool
    source_template: str | None = None
    definition_version: int = 1
    task_count: int
    node_types: list[str]
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_run: LastRunSchema | None = None


class WorkflowDetailSchema(WorkflowSummarySchema):
    definition: WorkflowDefSchema


# --- Request schemas ---


class CreateWorkflowRequest(BaseModel):
    definition: WorkflowDefSchema


class UpdateWorkflowRequest(BaseModel):
    definition: WorkflowDefSchema


# --- Validation schemas ---


class WorkflowValidationRequest(BaseModel):
    definition: WorkflowDefSchema


class WorkflowValidationErrorSchema(BaseModel):
    task_id: str
    field: str
    message: str


class WorkflowValidationResponse(BaseModel):
    valid: bool
    errors: list[WorkflowValidationErrorSchema] = []


# --- Node catalog schema ---


class NodeTypeInfoSchema(BaseModel):
    type: str
    category: str
    description: str
    required_config: list[str] = []
    optional_config: list[str] = []


# --- Run schemas ---


class WorkflowRunNodeSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    node_id: str
    node_type: str
    status: RunStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int = 0
    input_track_count: int | None = None
    output_track_count: int | None = None
    error_message: str | None = None
    execution_order: int = 0
    node_details: dict[str, Any] | None = None


class WorkflowRunSummarySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workflow_id: int
    status: RunStatus
    definition_version: int = 1
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    output_track_count: int | None = None
    output_playlist_id: int | None = None
    error_message: str | None = None
    created_at: datetime | None = None


class WorkflowRunDetailSchema(WorkflowRunSummarySchema):
    definition_snapshot: WorkflowDefSchema
    output_tracks: list[dict[str, Any]] = []
    nodes: list[WorkflowRunNodeSchema] = []


class WorkflowRunStartedResponse(BaseModel):
    operation_id: str
    run_id: int


# --- Converters ---


def _def_to_schema(wf_def: WorkflowDef) -> WorkflowDefSchema:
    """Convert a domain WorkflowDef to its API schema representation."""
    return WorkflowDefSchema(
        id=wf_def.id,
        name=wf_def.name,
        description=wf_def.description,
        version=wf_def.version,
        tasks=[
            WorkflowTaskDefSchema(
                id=t.id,
                type=t.type,
                config=t.config,
                upstream=t.upstream,
                result_key=t.result_key,
            )
            for t in wf_def.tasks
        ],
    )


def _extract_node_types(wf_def: WorkflowDef) -> list[str]:
    """Extract unique node type categories from workflow tasks."""
    categories: set[str] = set()
    for task in wf_def.tasks:
        if "." in task.type:
            categories.add(task.type.split(".", 1)[0])
        else:
            categories.add(task.type)
    return sorted(categories)


def to_workflow_summary(
    workflow: Workflow,
    last_run: WorkflowRun | None = None,
) -> WorkflowSummarySchema:
    last_run_schema: LastRunSchema | None = None
    if last_run is not None and last_run.id is not None:
        last_run_schema = LastRunSchema(
            id=last_run.id,
            status=last_run.status,
            definition_version=last_run.definition_version,
            completed_at=last_run.completed_at,
            output_track_count=last_run.output_track_count,
        )
    return WorkflowSummarySchema(
        id=workflow.id or 0,
        name=workflow.definition.name,
        description=workflow.definition.description or None,
        is_template=workflow.is_template,
        source_template=workflow.source_template,
        definition_version=workflow.definition_version,
        task_count=len(workflow.definition.tasks),
        node_types=_extract_node_types(workflow.definition),
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
        last_run=last_run_schema,
    )


def to_workflow_detail(
    workflow: Workflow, last_run: WorkflowRun | None = None
) -> WorkflowDetailSchema:
    summary = to_workflow_summary(workflow, last_run=last_run)
    return WorkflowDetailSchema(
        **summary.model_dump(),
        definition=_def_to_schema(workflow.definition),
    )


def schema_to_workflow_def(schema: WorkflowDefSchema) -> WorkflowDef:
    """Convert a request body schema to a domain WorkflowDef."""
    return WorkflowDef(
        id=schema.id,
        name=schema.name,
        description=schema.description,
        version=schema.version,
        tasks=[
            WorkflowTaskDef(
                id=t.id,
                type=t.type,
                config=t.config,
                upstream=t.upstream,
                result_key=t.result_key,
            )
            for t in schema.tasks
        ],
    )


def to_run_summary(run: WorkflowRun) -> WorkflowRunSummarySchema:
    return WorkflowRunSummarySchema(
        id=run.id or 0,
        workflow_id=run.workflow_id,
        status=run.status,
        definition_version=run.definition_version,
        started_at=run.started_at,
        completed_at=run.completed_at,
        duration_ms=run.duration_ms,
        output_track_count=run.output_track_count,
        output_playlist_id=run.output_playlist_id,
        error_message=run.error_message,
        created_at=run.created_at,
    )


def to_run_detail(run: WorkflowRun) -> WorkflowRunDetailSchema:
    summary = to_run_summary(run)
    return WorkflowRunDetailSchema(
        **summary.model_dump(),
        definition_snapshot=_def_to_schema(run.definition_snapshot),
        output_tracks=run.output_tracks,
        nodes=[
            WorkflowRunNodeSchema(
                id=n.id or 0,
                node_id=n.node_id,
                node_type=n.node_type,
                status=n.status,
                started_at=n.started_at,
                completed_at=n.completed_at,
                duration_ms=n.duration_ms,
                input_track_count=n.input_track_count,
                output_track_count=n.output_track_count,
                error_message=n.error_message,
                execution_order=n.execution_order,
                node_details=n.node_details,
            )
            for n in run.nodes
        ],
    )
