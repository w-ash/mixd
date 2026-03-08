"""Workflow repository for CRUD operations on persisted workflows."""

# pyright: reportAny=false

from datetime import UTC, datetime

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
    async def list_workflows(self, *, include_templates: bool = True) -> list[Workflow]:
        """List all workflows, optionally filtering out templates."""
        stmt = select(DBWorkflow).order_by(DBWorkflow.updated_at.desc())
        if not include_templates:
            stmt = stmt.where(DBWorkflow.is_template == False)  # noqa: E712
        result = await self.session.execute(stmt)
        db_models = list(result.scalars().all())
        return await self.mapper.map_collection(db_models)

    @db_operation("get_workflow_by_id")
    async def get_workflow_by_id(self, workflow_id: int) -> Workflow:
        """Get workflow by ID. Raises NotFoundError if not found."""
        stmt = self.select_by_id(workflow_id)
        db_model = await self.execute_select_one(stmt)
        if not db_model:
            raise NotFoundError(f"Workflow with ID {workflow_id} not found")
        return await self.mapper.to_domain(db_model)

    @db_operation("save_workflow")
    async def save_workflow(self, workflow: Workflow) -> Workflow:
        """Create or update a workflow."""
        if workflow.id is None:
            # Create
            db_model = self.mapper.to_db(workflow)
            self.session.add(db_model)
            await self.session.flush()
            await self.session.refresh(db_model)
            return await self.mapper.to_domain(db_model)

        # Update existing
        stmt = self.select_by_id(workflow.id)
        db_model = await self.execute_select_one(stmt)
        if not db_model:
            raise NotFoundError(f"Workflow with ID {workflow.id} not found")

        definition_dict = attrs.asdict(workflow.definition)
        db_model.name = workflow.definition.name
        db_model.description = workflow.definition.description or None
        db_model.definition = definition_dict
        db_model.definition_version = workflow.definition_version
        db_model.is_template = workflow.is_template
        db_model.source_template = workflow.source_template
        db_model.updated_at = datetime.now(UTC)
        await self.session.flush()
        return await self.mapper.to_domain(db_model)

    @db_operation("delete_workflow")
    async def delete_workflow(self, workflow_id: int) -> bool:
        """Delete workflow by ID. Returns True if deleted."""
        result = await self.session.execute(
            delete(DBWorkflow)
            .where(DBWorkflow.id == workflow_id)
            .returning(DBWorkflow.id)
        )
        deleted_ids = result.scalars().all()
        return bool(deleted_ids)

    @db_operation("get_workflow_by_source_template")
    async def get_workflow_by_source_template(
        self, source_template: str
    ) -> Workflow | None:
        """Find workflow by source template key."""
        stmt = select(DBWorkflow).where(DBWorkflow.source_template == source_template)
        result = await self.session.execute(stmt)
        db_model = result.scalar_one_or_none()
        if db_model is None:
            return None
        return await self.mapper.to_domain(db_model)
