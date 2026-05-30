"""Workflow repository for CRUD operations on persisted workflows."""

from datetime import UTC, datetime
from uuid import UUID

import attrs
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities.workflow import Workflow
from src.domain.exceptions import NotFoundError
from src.infrastructure.persistence.database.db_models import DBWorkflow
from src.infrastructure.persistence.repositories.base_repo import BaseRepository
from src.infrastructure.persistence.repositories.repo_decorator import db_operation
from src.infrastructure.persistence.repositories.workflow.mapper import WorkflowMapper

logger = get_logger(__name__)


class WorkflowRepository(BaseRepository[DBWorkflow, Workflow]):
    """Manages workflow persistence with template awareness."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(
            session=session,
            model_class=DBWorkflow,
            mapper=WorkflowMapper(),
        )

    @db_operation("list_workflows")
    async def list_workflows(self, *, user_id: str) -> list[Workflow]:
        """List the user's own workflows, most-recently-updated first."""
        stmt = (
            select(DBWorkflow)
            .where(DBWorkflow.user_id == user_id)
            .order_by(DBWorkflow.updated_at.desc())
        )
        result = await self.session.execute(stmt)
        db_models = list(result.scalars().all())
        return await self.mapper.map_collection(db_models)

    @db_operation("get_workflow_by_id")
    async def get_workflow_by_id(self, workflow_id: UUID, *, user_id: str) -> Workflow:
        """Get a workflow by ID, scoped to its owner.

        Args:
            workflow_id: Internal workflow ID.
            user_id: Owner's user ID for ownership verification.

        Raises:
            NotFoundError: If workflow not found or belongs to another user.
        """
        stmt = self.select_by_id(workflow_id).where(DBWorkflow.user_id == user_id)
        db_model = await self.execute_select_one(stmt)
        if not db_model:
            raise NotFoundError(f"Workflow with ID {workflow_id} not found")
        return await self.mapper.to_domain(db_model)

    @db_operation("save_workflow")
    async def save_workflow(self, workflow: Workflow) -> Workflow:
        """Create or update a workflow. Entity carries user_id."""
        # Check if workflow already exists in DB
        stmt = self.select_by_id(workflow.id)
        db_model = await self.execute_select_one(stmt)

        if not db_model:
            # Create
            db_model = self.mapper.to_db(workflow)
            self.session.add(db_model)
            await self.session.flush()
            await self.session.refresh(db_model)
            return await self.mapper.to_domain(db_model)

        definition_dict = attrs.asdict(workflow.definition)
        db_model.name = workflow.definition.name
        db_model.description = workflow.definition.description or None
        db_model.definition = definition_dict
        db_model.definition_version = workflow.definition_version
        db_model.updated_at = datetime.now(UTC)
        await self.session.flush()
        return await self.mapper.to_domain(db_model)

    @db_operation("delete_workflow")
    async def delete_workflow(self, workflow_id: UUID, *, user_id: str) -> bool:
        """Delete workflow by ID, verifying ownership. Templates cannot be deleted.

        Args:
            workflow_id: Internal workflow ID.
            user_id: Owner's user ID for ownership verification.

        Returns:
            True if deleted, False if not found.
        """
        result = await self.session.execute(
            delete(DBWorkflow)
            .where(DBWorkflow.id == workflow_id)
            .where(DBWorkflow.user_id == user_id)
            .returning(DBWorkflow.id)
        )
        deleted_ids = result.scalars().all()
        return bool(deleted_ids)
