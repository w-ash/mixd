"""Typed workflow definition entities replacing untyped dict[str, Any].

These frozen domain entities represent the structure of workflow JSON definitions.
Used by validation, execution (Prefect), API schemas, and persistence layers.
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: node config values are heterogeneous (str, int, float, bool, list)

from datetime import datetime
from typing import Any, Literal

from attrs import define, field

NodeExecutionStatus = Literal["completed", "failed", "skipped", "degraded"]
RunStatus = Literal["pending", "running", "completed", "failed", "cancelled"]


@define(frozen=True, slots=True)
class WorkflowTaskDef:
    """A single task within a workflow definition.

    Attributes:
        id: Unique identifier for the task within its workflow.
        type: Node type key (e.g., "source.playlist", "filter.by_metric").
        config: Node-specific configuration, validated by the node registry.
        upstream: IDs of tasks that must complete before this one.
        result_key: Optional key under which to store this task's result.
    """

    id: str
    type: str
    config: dict[str, Any] = field(factory=dict)
    upstream: list[str] = field(factory=list)
    result_key: str | None = None


@define(frozen=True, slots=True)
class NodeExecutionEvent:
    """Snapshot of node execution state passed to lifecycle observers.

    Bundles execution context into a single object so observer protocol methods
    stay at 2 parameters (event + method-specific data like result or error).
    """

    task_def: WorkflowTaskDef
    execution_order: int
    total_nodes: int
    duration_ms: int = 0
    input_track_count: int | None = None
    output_track_count: int | None = None

    def to_record(
        self,
        *,
        status: NodeExecutionStatus,
        error_message: str | None = None,
    ) -> NodeExecutionRecord:
        """Derive a persistence-ready record from this event."""
        return NodeExecutionRecord(
            node_id=self.task_def.id,
            node_type=self.task_def.type,
            execution_order=self.execution_order,
            status=status,
            duration_ms=self.duration_ms,
            input_track_count=self.input_track_count,
            output_track_count=self.output_track_count,
            error_message=error_message,
        )


@define(frozen=True, slots=True)
class NodeExecutionRecord:
    """Per-node execution result for run history recording.

    Captures what happened to each node during a workflow run. Maps directly
    to v0.4.1's ``workflow_run_nodes`` table rows via ``attrs.asdict()``.
    """

    node_id: str
    node_type: str
    execution_order: int
    status: NodeExecutionStatus
    duration_ms: int = 0
    input_track_count: int | None = None
    output_track_count: int | None = None
    error_message: str | None = None


@define(frozen=True, slots=True)
class WorkflowDef:
    """Complete workflow definition — the typed replacement for raw JSON dicts.

    Attributes:
        id: Unique workflow identifier (matches JSON filename stem).
        name: Human-readable workflow name.
        description: Optional description for CLI/UI display.
        version: Schema version for future compatibility.
        tasks: Ordered list of task definitions.
    """

    id: str
    name: str
    description: str = ""
    version: str = "1.0"
    tasks: list[WorkflowTaskDef] = field(factory=list)


def parse_task_dict(t: dict[str, Any]) -> WorkflowTaskDef:
    """Parse a single task dict from JSON into a typed WorkflowTaskDef.

    Shared by the workflow_loader (JSON files) and persistence mapper (DB JSON column).
    """
    return WorkflowTaskDef(
        id=str(t["id"]),
        type=str(t["type"]),
        config=dict(t.get("config", {})),
        upstream=list(t.get("upstream", [])),
        result_key=t.get("result_key"),
    )


def parse_workflow_def(raw: dict[str, Any]) -> WorkflowDef:
    """Reconstruct a WorkflowDef from a JSON-serialized dict."""
    raw_tasks: list[dict[str, Any]] = raw.get("tasks", [])
    return WorkflowDef(
        id=str(raw.get("id", "")),
        name=str(raw.get("name", "")),
        description=str(raw.get("description", "")),
        version=str(raw.get("version", "1.0")),
        tasks=[parse_task_dict(t) for t in raw_tasks],
    )


@define(frozen=True, slots=True)
class WorkflowVersion:
    """Snapshot of a workflow definition at a point in time.

    Created automatically when ``UpdateWorkflowUseCase`` modifies a workflow's
    tasks. Each version captures the *previous* definition before the change
    and a human-readable change summary.
    """

    id: int | None = None
    workflow_id: int = 0
    version: int = 1
    definition: WorkflowDef = field(factory=lambda: WorkflowDef(id="", name=""))
    created_at: datetime | None = None
    change_summary: str | None = None


@define(frozen=True, slots=True)
class Workflow:
    """Persisted workflow wrapping a WorkflowDef with database identity.

    Separates persistence concerns (id, timestamps, template metadata) from
    the workflow definition itself. The definition JSON column maps directly
    to ``WorkflowDef`` via ``attrs.asdict()`` / reconstruction.
    """

    id: int | None = None
    definition: WorkflowDef = field(factory=lambda: WorkflowDef(id="", name=""))
    is_template: bool = False
    source_template: str | None = None
    definition_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None


@define(frozen=True, slots=True)
class WorkflowRunNode:
    """Per-node execution result within a workflow run.

    Each node in a run gets a record tracking its lifecycle from pending
    through running to completed/failed. Maps to ``workflow_run_nodes`` rows.
    """

    id: int | None = None
    run_id: int | None = None
    node_id: str = ""
    node_type: str = ""
    status: RunStatus = "pending"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int = 0
    input_track_count: int | None = None
    output_track_count: int | None = None
    error_message: str | None = None
    execution_order: int = 0
    node_details: dict[str, Any] | None = None


@define(frozen=True, slots=True)
class WorkflowRun:
    """Persisted record of a single workflow execution.

    Created as PENDING before execution begins so the client has a ``run_id``
    immediately. The background task updates status through RUNNING to
    COMPLETED or FAILED. ``definition_snapshot`` freezes the workflow
    definition at execution time — edits to the workflow after launch
    don't affect an in-progress run.
    """

    id: int | None = None
    workflow_id: int = 0
    status: RunStatus = "pending"
    definition_snapshot: WorkflowDef = field(
        factory=lambda: WorkflowDef(id="", name="")
    )
    definition_version: int = 1
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    output_track_count: int | None = None
    output_playlist_id: int | None = None
    output_tracks: list[dict[str, Any]] = field(factory=list)
    error_message: str | None = None
    nodes: list[WorkflowRunNode] = field(factory=list)
    created_at: datetime | None = None
