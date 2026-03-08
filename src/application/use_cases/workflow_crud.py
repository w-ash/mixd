"""CRUD use cases for persisted workflows.

Five small use cases in one file following the established pattern:
frozen Command/Result objects, slots=True UseCase classes, async with uow
transaction boundaries.
"""

from attrs import define

from src.domain.entities.workflow import Workflow, WorkflowDef
from src.domain.exceptions import NotFoundError, TemplateReadOnlyError
from src.domain.repositories.interfaces import UnitOfWorkProtocol

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
    workflow_id: int


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
    workflow_id: int
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

            updated = Workflow(
                id=existing.id,
                definition=command.definition,
                is_template=existing.is_template,
                source_template=existing.source_template,
                created_at=existing.created_at,
            )
            saved = await repo.save_workflow(updated)
            return UpdateWorkflowResult(workflow=saved)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class DeleteWorkflowCommand:
    workflow_id: int


@define(frozen=True, slots=True)
class DeleteWorkflowResult:
    workflow_id: int


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
