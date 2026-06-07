"""Workflow run repository for execution history persistence."""

# pyright: reportAttributeAccessIssue=false, reportUnknownMemberType=false
# Legitimate: CursorResult.rowcount is valid but invisible to pyright through generic Result[Any]

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import get_logger
from src.config.constants import WorkflowConstants
from src.domain.entities.workflow import RunStatus, WorkflowRun, WorkflowRunNode
from src.domain.exceptions import NotFoundError, WorkflowAlreadyRunningError
from src.infrastructure.persistence.database.db_models import (
    DBWorkflow,
    DBWorkflowRun,
    DBWorkflowRunNode,
)
from src.infrastructure.persistence.repositories.repo_decorator import db_operation
from src.infrastructure.persistence.repositories.workflow.run_mapper import (
    WorkflowRunMapper,
)

logger = get_logger(__name__)

_ACTIVE_RUN_CONSTRAINT = "uq_workflow_runs_active"


def _is_active_run_conflict(exc: IntegrityError) -> bool:
    """True if ``exc`` is the active-run partial unique index violation.

    Matches by constraint name (psycopg ``diag.constraint_name``) and falls back
    to a name-in-message check. Deliberately narrow so a different unique
    conflict on the same table (e.g. ``operation_id``) is NOT misreported as a
    concurrency conflict and still surfaces as a real integrity error.
    """
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    constraint: object = getattr(diag, "constraint_name", None)
    if isinstance(constraint, str):
        return constraint == _ACTIVE_RUN_CONSTRAINT
    return _ACTIVE_RUN_CONSTRAINT in str(getattr(exc, "orig", None) or exc)


class WorkflowRunRepository:
    """Manages workflow run persistence with node-level execution records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.mapper = WorkflowRunMapper()

    @db_operation("create_run")
    async def create_run(self, run: WorkflowRun) -> WorkflowRun:
        """Persist a new workflow run with optional pre-created node records.

        The first flush inserts the run row, where the ``uq_workflow_runs_active``
        partial unique index enforces the one-active-run-per-workflow guard. A
        collision there means a concurrent run already holds the slot (possibly
        on another instance) → ``WorkflowAlreadyRunningError`` (409). Any other
        integrity error re-raises unchanged.
        """
        db_run = self.mapper.to_db(run)
        self.session.add(db_run)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            if _is_active_run_conflict(exc):
                raise WorkflowAlreadyRunningError(str(run.workflow_id)) from exc
            raise

        # Add node records if provided
        for node in run.nodes:
            db_node = self.mapper.node_to_db(node, run_id=db_run.id)
            self.session.add(db_node)

        await self.session.flush()
        await self.session.refresh(db_run, attribute_names=["nodes"])
        return self.mapper.to_domain(db_run, include_nodes=True)

    @db_operation("bump_heartbeat")
    async def bump_heartbeat(self, run_id: UUID) -> None:
        """Set ``heartbeat_at = now()`` for a run.

        Called periodically by the workflow's heartbeat ticker so the sweeper
        can distinguish active runs from orphans. Silently no-ops if the row
        is missing — heartbeats can race with completion.
        """
        await self.session.execute(
            update(DBWorkflowRun)
            .where(DBWorkflowRun.id == run_id)
            .values(heartbeat_at=datetime.now(UTC))
        )

    @db_operation("list_stalled_runs")
    async def list_stalled_runs(
        self, *, stale_threshold_seconds: int, limit: int | None = None
    ) -> list[WorkflowRun]:
        """Find runs whose heartbeat has gone silent past the threshold.

        Returns rows where ``status='running'`` AND either:
        - ``heartbeat_at IS NULL AND started_at < now() - threshold`` — cold-start
          hang: the workflow runner never began executing tasks.
        - ``heartbeat_at < now() - threshold`` — stalled mid-execution.

        Callers distinguish the two cases by checking ``run.heartbeat_at`` to
        produce appropriate ``error_message`` text. ``limit`` caps the rows
        returned per call (oldest-first) so one sweep cycle stays bounded under
        a large backlog.
        """
        threshold = datetime.now(UTC) - timedelta(seconds=stale_threshold_seconds)
        stmt = (
            select(DBWorkflowRun)
            .where(
                DBWorkflowRun.status == WorkflowConstants.RUN_STATUS_RUNNING,
                DBWorkflowRun.started_at < threshold,
                or_(
                    DBWorkflowRun.heartbeat_at.is_(None),
                    DBWorkflowRun.heartbeat_at < threshold,
                ),
            )
            .order_by(DBWorkflowRun.started_at.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        db_runs = list(result.scalars().all())
        return [
            self.mapper.to_domain(r, include_nodes=False, include_definition=False)
            for r in db_runs
        ]

    @db_operation("update_run_status")
    async def update_run_status(
        self,
        run_id: UUID,
        status: RunStatus,
        *,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        duration_ms: int | None = None,
        output_track_count: int | None = None,
        output_playlist_id: UUID | None = None,
        output_tracks: list[dict[str, object]] | None = None,
        error_message: str | None = None,
    ) -> bool:
        """Update run status and optional completion fields.

        Returns ``True`` when a row was actually transitioned, ``False`` when a
        guarded terminal write was a silent no-op (the row was already terminal —
        a lost first-writer-wins race). Counting callers (the sweeper) use this
        to avoid over-reporting; most callers ignore the return.
        """
        values: dict[str, object] = {
            "status": status,
            "updated_at": datetime.now(UTC),
        }
        if started_at is not None:
            values["started_at"] = started_at
        if completed_at is not None:
            values["completed_at"] = completed_at
        if duration_ms is not None:
            values["duration_ms"] = duration_ms
        if output_track_count is not None:
            values["output_track_count"] = output_track_count
        if output_playlist_id is not None:
            values["output_playlist_id"] = output_playlist_id
        if output_tracks is not None:
            values["output_tracks"] = output_tracks
        if error_message is not None:
            values["error_message"] = error_message

        # First-writer-wins guard on terminal writes. The completion path, the
        # SIGTERM/reload handler, and the sweeper can all race to record an
        # outcome on the same row; without a guard the last writer wins and
        # leaves self-contradictory duration_ms/error_message. Guarding terminal
        # writes with ``status NOT IN (terminal set)`` lets the first terminal
        # write win and makes every later one a silent no-op (mirrors Prefect's
        # HandleFlowTerminalStateTransitions). Same conditional-UPDATE /
        # rowcount==0 idiom as playlist/links.py and track/core.py.
        stmt = update(DBWorkflowRun).where(DBWorkflowRun.id == run_id)
        if status in WorkflowConstants.RUN_STATUSES_TERMINAL:
            stmt = stmt.where(
                DBWorkflowRun.status.notin_(WorkflowConstants.RUN_STATUSES_TERMINAL)
            )
            # A lost terminal race (row already terminal) and a missing row both
            # surface as rowcount==0 here; both are acceptable no-ops for a
            # terminal write, so do not raise — the run already has an outcome.
            # Report whether this write won, so the sweeper counts only real
            # transitions (rowcount==0 → False).
            result = await self.session.execute(stmt.values(**values))
            return cast("int", result.rowcount) > 0

        # Non-terminal write (e.g. pending → running): a missing row is a real
        # error, so keep the strict not-found semantics.
        result = await self.session.execute(stmt.values(**values))
        if result.rowcount == 0:
            raise NotFoundError(f"Workflow run {run_id} not found")
        return True

    @db_operation("save_node_record")
    async def save_node_record(self, node: WorkflowRunNode) -> WorkflowRunNode:
        """Persist a new node execution record."""
        db_node = self.mapper.node_to_db(node, run_id=node.run_id)
        self.session.add(db_node)
        await self.session.flush()
        await self.session.refresh(db_node)
        return self.mapper.node_to_domain(db_node)

    @db_operation("update_node_status")
    async def update_node_status(
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
        node_details: dict[str, object] | None = None,
    ) -> None:
        """Update a node's status and execution metrics."""
        values: dict[str, object] = {"status": status}
        if started_at is not None:
            values["started_at"] = started_at
        if completed_at is not None:
            values["completed_at"] = completed_at
        if duration_ms is not None:
            values["duration_ms"] = duration_ms
        if input_track_count is not None:
            values["input_track_count"] = input_track_count
        if output_track_count is not None:
            values["output_track_count"] = output_track_count
        if error_message is not None:
            values["error_message"] = error_message
        if node_details is not None:
            values["node_details"] = node_details

        result = await self.session.execute(
            update(DBWorkflowRunNode)
            .where(
                DBWorkflowRunNode.run_id == run_id,
                DBWorkflowRunNode.node_id == node_id,
            )
            .values(**values)
        )
        if result.rowcount == 0:
            raise NotFoundError(f"Node '{node_id}' not found in run {run_id}")

    @db_operation("get_runs_for_workflow")
    async def get_runs_for_workflow(
        self, workflow_id: UUID, limit: int = 20, offset: int = 0
    ) -> tuple[list[WorkflowRun], int]:
        """List runs for a workflow (without nodes) with total count."""
        # Count
        count_result = await self.session.execute(
            select(func.count(DBWorkflowRun.id)).where(
                DBWorkflowRun.workflow_id == workflow_id
            )
        )
        total = count_result.scalar_one()

        # Fetch page (no nodes — summary list)
        stmt = (
            select(DBWorkflowRun)
            .where(DBWorkflowRun.workflow_id == workflow_id)
            .order_by(DBWorkflowRun.created_at.desc(), DBWorkflowRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        db_runs = list(result.scalars().all())
        runs = [
            self.mapper.to_domain(r, include_nodes=False, include_definition=False)
            for r in db_runs
        ]
        return runs, total

    @db_operation("get_active_runs_for_user")
    async def get_active_runs_for_user(
        self, user_id: str, limit: int = 50, offset: int = 0
    ) -> tuple[list[WorkflowRun], int]:
        """List the user's in-flight (pending/running) runs across all workflows.

        Cross-instance truth rebuilt from the DB — the app-global "what's running
        now" source for the workflow detail page's reconnection and a future
        sidebar indicator. User scoping is enforced by the JOIN to ``workflows``;
        the ``RUN_STATUSES_ACTIVE`` filter matches the ``uq_workflow_runs_active``
        partial unique index (so at most one active run per workflow).
        """
        active = WorkflowConstants.RUN_STATUSES_ACTIVE
        scope = (
            DBWorkflow.user_id == user_id,
            DBWorkflowRun.status.in_(active),
        )

        count_result = await self.session.execute(
            select(func.count(DBWorkflowRun.id))
            .join(DBWorkflow, DBWorkflowRun.workflow_id == DBWorkflow.id)
            .where(*scope)
        )
        total = count_result.scalar_one()

        stmt = (
            select(DBWorkflowRun)
            .join(DBWorkflow, DBWorkflowRun.workflow_id == DBWorkflow.id)
            .where(*scope)
            .order_by(DBWorkflowRun.created_at.desc(), DBWorkflowRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        db_runs = list(result.scalars().all())
        runs = [
            self.mapper.to_domain(r, include_nodes=False, include_definition=False)
            for r in db_runs
        ]
        return runs, total

    @db_operation("get_run_by_id")
    async def get_run_by_id(self, run_id: UUID) -> WorkflowRun:
        """Get a single run with all node records loaded."""
        stmt = (
            select(DBWorkflowRun)
            .where(DBWorkflowRun.id == run_id)
            .options(selectinload(DBWorkflowRun.nodes))
        )
        result = await self.session.execute(stmt)
        db_run = result.scalar_one_or_none()
        if db_run is None:
            raise NotFoundError(f"Workflow run {run_id} not found")
        return self.mapper.to_domain(db_run, include_nodes=True)

    @db_operation("get_run_by_operation_id")
    async def get_run_by_operation_id(self, operation_id: str) -> WorkflowRun | None:
        """Resolve an SSE operation_id to its run row, or None if unknown.

        Returns None (not raising) when the operation_id has no matching
        row — the snapshot endpoint maps that to a 404. Pre-migration
        rows have NULL operation_id so they never match.
        """
        stmt = (
            select(DBWorkflowRun)
            .where(DBWorkflowRun.operation_id == operation_id)
            .options(selectinload(DBWorkflowRun.nodes))
        )
        result = await self.session.execute(stmt)
        db_run = result.scalar_one_or_none()
        if db_run is None:
            return None
        return self.mapper.to_domain(db_run, include_nodes=True)

    @db_operation("get_latest_run_for_workflow")
    async def get_latest_run_for_workflow(
        self, workflow_id: UUID
    ) -> WorkflowRun | None:
        """Get the most recent run for a workflow, or None."""
        stmt = (
            select(DBWorkflowRun)
            .where(DBWorkflowRun.workflow_id == workflow_id)
            .order_by(DBWorkflowRun.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        db_run = result.scalar_one_or_none()
        if db_run is None:
            return None
        return self.mapper.to_domain(
            db_run, include_nodes=False, include_definition=False
        )

    @db_operation("get_latest_runs_for_workflows")
    async def get_latest_runs_for_workflows(
        self, workflow_ids: list[UUID]
    ) -> dict[UUID, WorkflowRun]:
        """Batch-fetch the latest run for each workflow ID using a window function."""
        if not workflow_ids:
            return {}

        # Window function: rank runs per workflow by created_at desc
        row_number = (
            func
            .row_number()
            .over(
                partition_by=DBWorkflowRun.workflow_id,
                order_by=DBWorkflowRun.created_at.desc(),
            )
            .label("rn")
        )
        subq = (
            select(DBWorkflowRun, row_number)
            .where(DBWorkflowRun.workflow_id.in_(workflow_ids))
            .subquery()
        )
        stmt = select(DBWorkflowRun).join(
            subq,
            (DBWorkflowRun.id == subq.c.id) & (subq.c.rn == 1),
        )
        result = await self.session.execute(stmt)
        db_runs = list(result.scalars().all())
        return {
            r.workflow_id: self.mapper.to_domain(
                r, include_nodes=False, include_definition=False
            )
            for r in db_runs
        }
