"""Pydantic v2 schemas for workflow API endpoints.

Domain-to-schema conversion functions translate attrs entities into
Pydantic models for JSON serialization.
"""

# pyright: reportExplicitAny=false
# Legitimate Any: node config values are heterogeneous

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from src.domain.entities.workflow import Workflow, WorkflowDef, WorkflowTaskDef

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


class WorkflowSummarySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    is_template: bool
    source_template: str | None = None
    task_count: int
    node_types: list[str]
    created_at: datetime | None = None
    updated_at: datetime | None = None


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


# --- Converters ---


def _extract_node_types(wf_def: WorkflowDef) -> list[str]:
    """Extract unique node type categories from workflow tasks."""
    categories: set[str] = set()
    for task in wf_def.tasks:
        if "." in task.type:
            categories.add(task.type.split(".", 1)[0])
        else:
            categories.add(task.type)
    return sorted(categories)


def to_workflow_summary(workflow: Workflow) -> WorkflowSummarySchema:
    return WorkflowSummarySchema(
        id=workflow.id or 0,
        name=workflow.definition.name,
        description=workflow.definition.description or None,
        is_template=workflow.is_template,
        source_template=workflow.source_template,
        task_count=len(workflow.definition.tasks),
        node_types=_extract_node_types(workflow.definition),
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )


def to_workflow_detail(workflow: Workflow) -> WorkflowDetailSchema:
    definition = WorkflowDefSchema(
        id=workflow.definition.id,
        name=workflow.definition.name,
        description=workflow.definition.description,
        version=workflow.definition.version,
        tasks=[
            WorkflowTaskDefSchema(
                id=t.id,
                type=t.type,
                config=t.config,
                upstream=t.upstream,
                result_key=t.result_key,
            )
            for t in workflow.definition.tasks
        ],
    )
    return WorkflowDetailSchema(
        id=workflow.id or 0,
        name=workflow.definition.name,
        description=workflow.definition.description or None,
        is_template=workflow.is_template,
        source_template=workflow.source_template,
        task_count=len(workflow.definition.tasks),
        node_types=_extract_node_types(workflow.definition),
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
        definition=definition,
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
