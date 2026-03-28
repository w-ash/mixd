"""CRUD use cases for persisted workflows.

Five small use cases in one file following the established pattern:
frozen Command/Result objects, slots=True UseCase classes, async with uow
transaction boundaries.
"""

from uuid import UUID

from attrs import define

from src.domain.entities.workflow import Workflow, WorkflowDef, WorkflowVersion
from src.domain.exceptions import NotFoundError, TemplateReadOnlyError
from src.domain.repositories.interfaces import UnitOfWorkProtocol


def _tasks_changed(old_def: WorkflowDef, new_def: WorkflowDef) -> bool:
    """Compare task lists to detect definition changes requiring a version bump."""
    return old_def.tasks != new_def.tasks


def _generate_change_summary(old_def: WorkflowDef, new_def: WorkflowDef) -> str:
    """Generate a human-readable summary of changes between two definitions."""
    old_by_id = {t.id: t for t in old_def.tasks}
    new_by_id = {t.id: t for t in new_def.tasks}

    added = new_by_id.keys() - old_by_id.keys()
    removed = old_by_id.keys() - new_by_id.keys()
    common = old_by_id.keys() & new_by_id.keys()
    modified = {tid for tid in common if old_by_id[tid] != new_by_id[tid]}

    parts: list[str] = []
    if added:
        parts.append(f"Added {len(added)} node{'s' if len(added) != 1 else ''}")
    if removed:
        parts.append(f"Removed {len(removed)} node{'s' if len(removed) != 1 else ''}")
    if modified:
        parts.append(
            f"Modified {len(modified)} node{'s' if len(modified) != 1 else ''}"
        )

    if old_def.name != new_def.name:
        parts.append(f"Renamed to '{new_def.name}'")

    return ", ".join(parts) if parts else "Definition updated"


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class ListWorkflowsCommand:
    include_templates: bool = True


@define(frozen=True, slots=True)
class ListWorkflowsResult:
    workflows: list[Workflow]
    total_count: int


@define(slots=True)
class ListWorkflowsUseCase:
    async def execute(
        self, command: ListWorkflowsCommand, uow: UnitOfWorkProtocol
    ) -> ListWorkflowsResult:
        async with uow:
            repo = uow.get_workflow_repository()
            workflows = await repo.list_workflows(
                include_templates=command.include_templates
            )
            return ListWorkflowsResult(
                workflows=workflows,
                total_count=len(workflows),
            )


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class GetWorkflowCommand:
    workflow_id: UUID


@define(frozen=True, slots=True)
class GetWorkflowResult:
    workflow: Workflow


@define(slots=True)
class GetWorkflowUseCase:
    async def execute(
        self, command: GetWorkflowCommand, uow: UnitOfWorkProtocol
    ) -> GetWorkflowResult:
        async with uow:
            repo = uow.get_workflow_repository()
            workflow = await repo.get_workflow_by_id(command.workflow_id)
            return GetWorkflowResult(workflow=workflow)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class CreateWorkflowCommand:
    definition: WorkflowDef
    source_template: str | None = None


@define(frozen=True, slots=True)
class CreateWorkflowResult:
    workflow: Workflow


@define(slots=True)
class CreateWorkflowUseCase:
    async def execute(
        self, command: CreateWorkflowCommand, uow: UnitOfWorkProtocol
    ) -> CreateWorkflowResult:
        from src.application.workflows.validation import validate_workflow_def

        validate_workflow_def(command.definition)

        workflow = Workflow(
            definition=command.definition,
            is_template=command.source_template is not None,
            source_template=command.source_template,
        )

        async with uow:
            repo = uow.get_workflow_repository()
            saved = await repo.save_workflow(workflow)
            return CreateWorkflowResult(workflow=saved)


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class UpdateWorkflowCommand:
    workflow_id: UUID
    definition: WorkflowDef


@define(frozen=True, slots=True)
class UpdateWorkflowResult:
    workflow: Workflow


@define(slots=True)
class UpdateWorkflowUseCase:
    async def execute(
        self, command: UpdateWorkflowCommand, uow: UnitOfWorkProtocol
    ) -> UpdateWorkflowResult:
        from src.application.workflows.validation import validate_workflow_def

        async with uow:
            repo = uow.get_workflow_repository()
            existing = await repo.get_workflow_by_id(command.workflow_id)

            if existing.is_template:
                raise TemplateReadOnlyError(
                    f"Cannot modify template workflow '{existing.definition.name}'"
                )

            validate_workflow_def(command.definition)

            # Bump version when task pipeline changes; preserve on name/description-only edits
            new_version = existing.definition_version
            tasks_differ = _tasks_changed(existing.definition, command.definition)
            if tasks_differ:
                new_version = existing.definition_version + 1

            # Snapshot the current definition as a version record before overwriting
            if tasks_differ:
                version_repo = uow.get_workflow_version_repository()
                next_ver = await version_repo.get_max_version_number(existing.id) + 1
                change_summary = _generate_change_summary(
                    existing.definition, command.definition
                )
                snapshot = WorkflowVersion(
                    workflow_id=existing.id,
                    version=next_ver,
                    definition=existing.definition,
                    change_summary=change_summary,
                )
                await version_repo.create_version(snapshot)

            updated = Workflow(
                id=existing.id,
                definition=command.definition,
                is_template=existing.is_template,
                source_template=existing.source_template,
                definition_version=new_version,
                created_at=existing.created_at,
            )
            saved = await repo.save_workflow(updated)
            return UpdateWorkflowResult(workflow=saved)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class DeleteWorkflowCommand:
    workflow_id: UUID


@define(frozen=True, slots=True)
class DeleteWorkflowResult:
    workflow_id: UUID


@define(slots=True)
class DeleteWorkflowUseCase:
    async def execute(
        self, command: DeleteWorkflowCommand, uow: UnitOfWorkProtocol
    ) -> DeleteWorkflowResult:
        async with uow:
            repo = uow.get_workflow_repository()
            existing = await repo.get_workflow_by_id(command.workflow_id)

            if existing.is_template:
                raise TemplateReadOnlyError(
                    f"Cannot delete template workflow '{existing.definition.name}'"
                )

            deleted = await repo.delete_workflow(command.workflow_id)
            if not deleted:
                raise NotFoundError(f"Workflow {command.workflow_id} not found")

            return DeleteWorkflowResult(workflow_id=command.workflow_id)
