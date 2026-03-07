"""Typed workflow definition entities replacing untyped dict[str, Any].

These frozen domain entities represent the structure of workflow JSON definitions.
Used by validation, execution (Prefect), API schemas, and persistence layers.
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: node config values are heterogeneous (str, int, float, bool, list)

from typing import Any, Literal

from attrs import define, field

NodeExecutionStatus = Literal["completed", "failed", "skipped"]


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
