"""Typed workflow definition entities replacing untyped dict[str, Any].

These frozen domain entities represent the structure of workflow JSON definitions.
Used by validation, execution (Prefect), API schemas, and persistence layers.
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: node config values are heterogeneous (str, int, float, bool, list)

from typing import Any

from attrs import define, field


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
class NodeExecutionRecord:
    """Per-node execution result for run history recording.

    Captures what happened to each node during a workflow run. Maps directly
    to v0.4.1's ``workflow_run_nodes`` table rows via ``attrs.asdict()``.

    Attributes:
        node_id: Task identifier within the workflow.
        node_type: Node type key (e.g., "enricher.spotify").
        execution_order: 1-based position in execution sequence.
        status: Outcome — "completed", "failed", or "skipped".
        duration_ms: Wall-clock time in milliseconds.
        input_track_count: Tracks received from upstream (None for source nodes).
        output_track_count: Tracks in result tracklist (None on failure).
        error_message: Exception message on failure, None otherwise.
    """

    node_id: str
    node_type: str
    execution_order: int
    status: str  # "completed" | "failed" | "skipped"
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
