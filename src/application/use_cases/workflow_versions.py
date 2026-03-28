"""Use cases for workflow version history.

Lists, retrieves, and reverts workflow versions. Version records are created
automatically by ``UpdateWorkflowUseCase`` — see ``workflow_crud.py``.
"""

from uuid import UUID

from attrs import define

from src.domain.entities.workflow import Workflow, WorkflowVersion
from src.domain.repositories.interfaces import UnitOfWorkProtocol

# ---------------------------------------------------------------------------
# List versions
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class ListWorkflowVersionsCommand:
    workflow_id: UUID


@define(frozen=True, slots=True)
class ListWorkflowVersionsResult:
    versions: list[WorkflowVersion]


@define(slots=True)
class ListWorkflowVersionsUseCase:
    async def execute(
        self, command: ListWorkflowVersionsCommand, uow: UnitOfWorkProtocol
    ) -> ListWorkflowVersionsResult:
        async with uow:
            wf_repo = uow.get_workflow_repository()
            await wf_repo.get_workflow_by_id(command.workflow_id)

            version_repo = uow.get_workflow_version_repository()
            versions = await version_repo.list_versions(command.workflow_id)
            return ListWorkflowVersionsResult(versions=versions)


# ---------------------------------------------------------------------------
# Get version
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class GetWorkflowVersionCommand:
    workflow_id: UUID
    version: int


@define(frozen=True, slots=True)
class GetWorkflowVersionResult:
    version: WorkflowVersion


@define(slots=True)
class GetWorkflowVersionUseCase:
    async def execute(
        self, command: GetWorkflowVersionCommand, uow: UnitOfWorkProtocol
    ) -> GetWorkflowVersionResult:
        async with uow:
            wf_repo = uow.get_workflow_repository()
            await wf_repo.get_workflow_by_id(command.workflow_id)

            version_repo = uow.get_workflow_version_repository()
            version = await version_repo.get_version(
                command.workflow_id, command.version
            )
            return GetWorkflowVersionResult(version=version)


# ---------------------------------------------------------------------------
# Revert to version
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class RevertWorkflowVersionCommand:
    workflow_id: UUID
    version: int


@define(frozen=True, slots=True)
class RevertWorkflowVersionResult:
    workflow: Workflow


@define(slots=True)
class RevertWorkflowVersionUseCase:
    """Reverts a workflow to a previous version.

    Creates a new version record with the current definition (so history
    is preserved), then updates the workflow with the old definition.
    """

    async def execute(
        self, command: RevertWorkflowVersionCommand, uow: UnitOfWorkProtocol
    ) -> RevertWorkflowVersionResult:
        async with uow:
            wf_repo = uow.get_workflow_repository()
            version_repo = uow.get_workflow_version_repository()

            existing = await wf_repo.get_workflow_by_id(command.workflow_id)
            target_version = await version_repo.get_version(
                command.workflow_id, command.version
            )

            # Snapshot current definition before reverting
            next_version_num = (
                await version_repo.get_max_version_number(command.workflow_id) + 1
            )

            snapshot = WorkflowVersion(
                workflow_id=command.workflow_id,
                version=next_version_num,
                definition=existing.definition,
                change_summary=f"Before revert to v{command.version}",
            )
            await version_repo.create_version(snapshot)

            # Update workflow with the reverted definition
            new_version = existing.definition_version + 1
            reverted = Workflow(
                id=existing.id,
                definition=target_version.definition,
                is_template=existing.is_template,
                source_template=existing.source_template,
                definition_version=new_version,
                created_at=existing.created_at,
            )
            saved = await wf_repo.save_workflow(reverted)

            return RevertWorkflowVersionResult(workflow=saved)
