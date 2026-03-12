"""Workflow run repository for execution history persistence."""

# pyright: reportExplicitAny=false, reportAttributeAccessIssue=false, reportUnknownMemberType=false
# Legitimate: CursorResult.rowcount is valid but invisible to pyright through generic Result[Any]

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import get_logger
from src.domain.entities.workflow import RunStatus, WorkflowRun, WorkflowRunNode
from src.domain.exceptions import NotFoundError
from src.infrastructure.persistence.database.db_models import (
    DBWorkflowRun,
    DBWorkflowRunNode,
)
from src.infrastructure.persistence.repositories.repo_decorator import db_operation
from src.infrastructure.persistence.repositories.workflow.run_mapper import (
    WorkflowRunMapper,
)

logger = get_logger(__name__)


class WorkflowRunRepository:
    """Manages workflow run persistence with node-level execution records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.mapper = WorkflowRunMapper()

    @db_operation("create_run")
    async def create_run(self, run: WorkflowRun) -> WorkflowRun:
        """Persist a new workflow run with optional pre-created node records."""
        db_run = self.mapper.to_db(run)
        self.session.add(db_run)
        await self.session.flush()

        # Add node records if provided
        for node in run.nodes:
            db_node = self.mapper.node_to_db(node, run_id=db_run.id)
            self.session.add(db_node)

        await self.session.flush()
        await self.session.refresh(db_run, attribute_names=["nodes"])
        return self.mapper.to_domain(db_run, include_nodes=True)

    @db_operation("update_run_status")
    async def update_run_status(
        self,
        run_id: int,
        status: RunStatus,
        *,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        duration_ms: int | None = None,
        output_track_count: int | None = None,
        output_playlist_id: int | None = None,
        output_tracks: list[dict[str, object]] | None = None,
        error_message: str | None = None,
    ) -> None:
        """Update run status and optional completion fields."""
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

        result = await self.session.execute(
            update(DBWorkflowRun).where(DBWorkflowRun.id == run_id).values(**values)
        )
        if result.rowcount == 0:
            raise NotFoundError(f"Workflow run {run_id} not found")

    @db_operation("save_node_record")
    async def save_node_record(self, node: WorkflowRunNode) -> WorkflowRunNode:
        """Persist a new node execution record."""
        if node.run_id is None:
            raise ValueError("Node must have a run_id")
        db_node = self.mapper.node_to_db(node, run_id=node.run_id)
        self.session.add(db_node)
        await self.session.flush()
        await self.session.refresh(db_node)
        return self.mapper.node_to_domain(db_node)

    @db_operation("update_node_status")
    async def update_node_status(
        self,
        run_id: int,
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
        self, workflow_id: int, limit: int = 20, offset: int = 0
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
            .order_by(DBWorkflowRun.created_at.desc())
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
    async def get_run_by_id(self, run_id: int) -> WorkflowRun:
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

    @db_operation("get_latest_run_for_workflow")
    async def get_latest_run_for_workflow(self, workflow_id: int) -> WorkflowRun | None:
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
        self, workflow_ids: list[int]
    ) -> dict[int, WorkflowRun]:
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
