"""Workflow, run, and version repository protocols.

Split from the former monolithic ``interfaces.py``.
"""

from collections.abc import Awaitable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from src.domain.entities.workflow import (
    RunStatus,
    Workflow,
    WorkflowRun,
    WorkflowRunNode,
    WorkflowVersion,
)


class WorkflowRepositoryProtocol(Protocol):
    """Repository interface for workflow persistence operations."""

    def list_workflows(self, *, user_id: str) -> Awaitable[list[Workflow]]:
        """List the user's own workflows."""
        ...

    def get_workflow_by_id(
        self, workflow_id: UUID, *, user_id: str
    ) -> Awaitable[Workflow]:
        """Get workflow by ID. Raises NotFoundError if not found or wrong user."""
        ...

    def save_workflow(self, workflow: Workflow) -> Awaitable[Workflow]:
        """Create or update a workflow."""
        ...

    def delete_workflow(self, workflow_id: UUID, *, user_id: str) -> Awaitable[bool]:
        """Delete workflow by ID, verifying ownership. Returns True if deleted."""
        ...


class WorkflowRunRepositoryProtocol(Protocol):
    """Repository interface for workflow run history persistence."""

    def create_run(self, run: WorkflowRun) -> Awaitable[WorkflowRun]:
        """Persist a new workflow run record."""
        ...

    def update_run_status(
        self,
        run_id: UUID,
        status: RunStatus,
        *,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        duration_ms: int | None = None,
        output_track_count: int | None = None,
        output_playlist_id: UUID | None = None,
        error_message: str | None = None,
    ) -> Awaitable[bool]:
        """Update run status and optional completion fields.

        Returns True if a row was transitioned, False if a guarded terminal
        write no-op'd (the row was already terminal — a lost first-writer race).
        """
        ...

    def bump_heartbeat(self, run_id: UUID) -> Awaitable[None]:
        """Set ``heartbeat_at = now()`` for a run. Used by liveness ticker."""
        ...

    def list_stalled_runs(
        self, *, stale_threshold_seconds: int, limit: int | None = None
    ) -> Awaitable[list[WorkflowRun]]:
        """Return ``status='running'`` runs whose heartbeat is older than the threshold.

        ``limit`` caps the rows returned (oldest-first) so one sweep stays bounded.
        """
        ...

    def save_node_record(self, node: WorkflowRunNode) -> Awaitable[WorkflowRunNode]:
        """Persist a new node execution record."""
        ...

    def update_node_status(
        self,
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
    ) -> Awaitable[None]:
        """Update a node's status and execution metrics."""
        ...

    def get_runs_for_workflow(
        self, workflow_id: UUID, limit: int = 20, offset: int = 0
    ) -> Awaitable[tuple[list[WorkflowRun], int]]:
        """List runs for a workflow (without nodes loaded) with total count."""
        ...

    def get_active_runs_for_user(
        self, user_id: str, limit: int = 50, offset: int = 0
    ) -> Awaitable[tuple[list[WorkflowRun], int]]:
        """List the user's in-flight (pending/running) runs across all workflows."""
        ...

    def get_run_by_id(self, run_id: UUID) -> Awaitable[WorkflowRun]:
        """Get a single run with all node records loaded."""
        ...

    def get_run_by_operation_id(
        self, operation_id: str
    ) -> Awaitable[WorkflowRun | None]:
        """Resolve an SSE operation_id to its run row, or None if unknown."""
        ...

    def get_latest_run_for_workflow(
        self, workflow_id: UUID
    ) -> Awaitable[WorkflowRun | None]:
        """Get the most recent run for a workflow, or None."""
        ...

    def get_latest_runs_for_workflows(
        self, workflow_ids: list[UUID]
    ) -> Awaitable[dict[UUID, WorkflowRun]]:
        """Batch-fetch the latest run for each workflow ID."""
        ...


class WorkflowVersionRepositoryProtocol(Protocol):
    """Repository interface for workflow version history."""

    def create_version(self, version: WorkflowVersion) -> Awaitable[WorkflowVersion]:
        """Persist a new version snapshot."""
        ...

    def list_versions(self, workflow_id: UUID) -> Awaitable[list[WorkflowVersion]]:
        """List all versions for a workflow, ordered by version desc."""
        ...

    def get_version(
        self, workflow_id: UUID, version: int
    ) -> Awaitable[WorkflowVersion]:
        """Get a specific version. Raises NotFoundError if not found."""
        ...

    def get_max_version_number(self, workflow_id: UUID) -> Awaitable[int]:
        """Return the highest version number for a workflow, or 0 if none exist."""
        ...

    def delete_versions_for_workflow(self, workflow_id: UUID) -> Awaitable[None]:
        """Delete all versions for a workflow (cascade cleanup)."""
        ...
