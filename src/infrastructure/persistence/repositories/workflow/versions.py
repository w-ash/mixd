"""Repository for workflow version history persistence."""

from uuid import UUID

import attrs
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.workflow import WorkflowVersion, parse_workflow_def
from src.domain.exceptions import NotFoundError
from src.infrastructure.persistence.database.db_models import DBWorkflowVersion


class WorkflowVersionRepository:
    """SQLAlchemy repository for workflow version snapshots."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_version(self, version: WorkflowVersion) -> WorkflowVersion:
        """Persist a new version snapshot."""
        db_version = DBWorkflowVersion(
            workflow_id=version.workflow_id,
            version=version.version,
            definition=attrs.asdict(version.definition),
            change_summary=version.change_summary,
        )
        self._session.add(db_version)
        await self._session.flush()

        return WorkflowVersion(
            id=db_version.id,
            workflow_id=db_version.workflow_id,
            version=db_version.version,
            definition=version.definition,
            created_at=db_version.created_at,
            change_summary=db_version.change_summary,
        )

    async def list_versions(self, workflow_id: UUID) -> list[WorkflowVersion]:
        """List all versions for a workflow, ordered by version desc."""
        stmt = (
            select(DBWorkflowVersion)
            .where(DBWorkflowVersion.workflow_id == workflow_id)
            .order_by(DBWorkflowVersion.version.desc())
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()

        return [_to_domain(row) for row in rows]

    async def get_version(self, workflow_id: UUID, version: int) -> WorkflowVersion:
        """Get a specific version. Raises NotFoundError if not found."""
        stmt = select(DBWorkflowVersion).where(
            DBWorkflowVersion.workflow_id == workflow_id,
            DBWorkflowVersion.version == version,
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()

        if row is None:
            raise NotFoundError(
                f"Version {version} not found for workflow {workflow_id}"
            )

        return _to_domain(row)

    async def get_max_version_number(self, workflow_id: UUID) -> int:
        """Return the highest version number for a workflow, or 0 if none exist."""
        stmt = select(func.max(DBWorkflowVersion.version)).where(
            DBWorkflowVersion.workflow_id == workflow_id
        )
        result = await self._session.execute(stmt)
        return result.scalar() or 0

    async def delete_versions_for_workflow(self, workflow_id: UUID) -> None:
        """Delete all versions for a workflow."""
        stmt = delete(DBWorkflowVersion).where(
            DBWorkflowVersion.workflow_id == workflow_id
        )
        await self._session.execute(stmt)


def _to_domain(db: DBWorkflowVersion) -> WorkflowVersion:
    """Convert a DB model to a domain WorkflowVersion."""
    return WorkflowVersion(
        id=db.id,
        workflow_id=db.workflow_id,
        version=db.version,
        definition=parse_workflow_def(db.definition),
        created_at=db.created_at,
        change_summary=db.change_summary,
    )
