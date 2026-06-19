"""CRUD use cases for persisted workflows.

Five small use cases in one file following the established pattern:
frozen Command/Result objects, slots=True UseCase classes, async with uow
transaction boundaries.
"""

import re
from uuid import UUID, uuid4

from attrs import define, evolve

from src.domain.entities.workflow import Workflow, WorkflowDef, WorkflowVersion
from src.domain.exceptions import NotFoundError
from src.domain.repositories.uow import UnitOfWorkProtocol

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    """Lowercase, hyphenate runs of non-alphanumerics, trim dashes.

    Returns ``""`` when there is nothing usable (e.g. an emoji-only name).
    """
    return _SLUG_RE.sub("-", text.lower()).strip("-")


def _clone_definition(definition: WorkflowDef, *, name: str | None) -> WorkflowDef:
    """Copy a definition into a fresh, independently-identified one.

    Mints a new unique ``id`` slug so instantiated/duplicated workflows never
    shadow their source — or each other — on the slug that the CLI
    (``mixd workflow … --id <slug>``) and the personal seeder
    (``existing_by_slug``) key on. Optionally overrides the display name; the
    task pipeline is copied verbatim.
    """
    display_name = name if name is not None else definition.name
    slug = f"{_slugify(display_name) or 'workflow'}-{uuid4().hex[:8]}"
    return evolve(definition, id=slug, name=display_name)


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
    user_id: str


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
            workflows = await repo.list_workflows(user_id=command.user_id)
            return ListWorkflowsResult(
                workflows=workflows,
                total_count=len(workflows),
            )


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class GetWorkflowCommand:
    user_id: str
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
            workflow = await repo.get_workflow_by_id(
                command.workflow_id, user_id=command.user_id
            )
            return GetWorkflowResult(workflow=workflow)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class CreateWorkflowCommand:
    user_id: str
    definition: WorkflowDef


@define(frozen=True, slots=True)
class CreateWorkflowResult:
    workflow: Workflow


@define(slots=True)
class CreateWorkflowUseCase:
    async def execute(
        self, command: CreateWorkflowCommand, uow: UnitOfWorkProtocol
    ) -> CreateWorkflowResult:
        from src.application.workflows.definition.validation import (
            validate_workflow_def,
        )

        validate_workflow_def(command.definition)

        workflow = Workflow(
            user_id=command.user_id,
            definition=command.definition,
        )

        async with uow:
            repo = uow.get_workflow_repository()
            saved = await repo.save_workflow(workflow)
            return CreateWorkflowResult(workflow=saved)


# ---------------------------------------------------------------------------
# Instantiate (clone a definition into a new user-owned workflow)
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class InstantiateWorkflowCommand:
    """Create a new user-owned workflow from a built-in template definition.

    The caller supplies a gallery ``WorkflowDef``; the result is always a
    plain, editable, user-owned workflow with a freshly-minted slug — never a
    template. (Duplicating an existing workflow has its own use case.)
    """

    user_id: str
    definition: WorkflowDef


@define(frozen=True, slots=True)
class InstantiateWorkflowResult:
    workflow: Workflow


@define(slots=True)
class InstantiateWorkflowUseCase:
    async def execute(
        self, command: InstantiateWorkflowCommand, uow: UnitOfWorkProtocol
    ) -> InstantiateWorkflowResult:
        from src.application.workflows.definition.validation import (
            validate_workflow_def,
        )

        definition = _clone_definition(command.definition, name=None)
        validate_workflow_def(definition)

        workflow = Workflow(user_id=command.user_id, definition=definition)

        async with uow:
            repo = uow.get_workflow_repository()
            saved = await repo.save_workflow(workflow)
            return InstantiateWorkflowResult(workflow=saved)


# ---------------------------------------------------------------------------
# Duplicate (clone an existing persisted workflow into a new copy)
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class DuplicateWorkflowCommand:
    user_id: str
    workflow_id: UUID


@define(frozen=True, slots=True)
class DuplicateWorkflowResult:
    workflow: Workflow


@define(slots=True)
class DuplicateWorkflowUseCase:
    """Clone an existing workflow into a new, independent user-owned copy.

    Fetch + clone + save run inside a single ``async with uow:`` so one
    transaction owns the whole operation. Composing GetWorkflowUseCase and
    InstantiateWorkflowUseCase in the route instead would open two UoW blocks
    on one instance — and once the first latches ``_committed``, the second's
    auto-commit is silently skipped.
    """

    async def execute(
        self, command: DuplicateWorkflowCommand, uow: UnitOfWorkProtocol
    ) -> DuplicateWorkflowResult:
        from src.application.workflows.definition.validation import (
            validate_workflow_def,
        )

        async with uow:
            repo = uow.get_workflow_repository()
            existing = await repo.get_workflow_by_id(
                command.workflow_id, user_id=command.user_id
            )
            clone = _clone_definition(
                existing.definition, name=f"{existing.definition.name} (copy)"
            )
            # Validate like Instantiate/Create/Update — never persist a copy the
            # other write paths would reject (e.g. a source whose definition
            # predates a tightened rule).
            validate_workflow_def(clone)
            workflow = Workflow(user_id=command.user_id, definition=clone)
            saved = await repo.save_workflow(workflow)
            return DuplicateWorkflowResult(workflow=saved)


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class UpdateWorkflowCommand:
    user_id: str
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
        from src.application.workflows.definition.validation import (
            validate_workflow_def,
        )

        async with uow:
            repo = uow.get_workflow_repository()
            existing = await repo.get_workflow_by_id(
                command.workflow_id, user_id=command.user_id
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

            updated = evolve(
                existing,
                definition=command.definition,
                definition_version=new_version,
            )
            saved = await repo.save_workflow(updated)
            return UpdateWorkflowResult(workflow=saved)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class DeleteWorkflowCommand:
    user_id: str
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
            deleted = await repo.delete_workflow(
                command.workflow_id, user_id=command.user_id
            )
            if not deleted:
                raise NotFoundError(f"Workflow {command.workflow_id} not found")

            return DeleteWorkflowResult(workflow_id=command.workflow_id)
