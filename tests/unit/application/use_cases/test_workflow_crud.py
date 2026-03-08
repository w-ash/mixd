"""Unit tests for workflow CRUD use cases.

Tests list, get, create, update, and delete operations using mock UoW.
"""

import pytest

from src.application.use_cases.workflow_crud import (
    CreateWorkflowCommand,
    CreateWorkflowUseCase,
    DeleteWorkflowCommand,
    DeleteWorkflowUseCase,
    GetWorkflowCommand,
    GetWorkflowUseCase,
    ListWorkflowsCommand,
    ListWorkflowsUseCase,
    UpdateWorkflowCommand,
    UpdateWorkflowUseCase,
)
from src.domain.exceptions import NotFoundError, TemplateReadOnlyError
from tests.fixtures import (
    make_mock_uow,
    make_mock_workflow_repo,
    make_workflow,
    make_workflow_def,
)


class TestListWorkflows:
    async def test_returns_all_workflows(self) -> None:
        workflows = [make_workflow(id=1), make_workflow(id=2)]
        repo = make_mock_workflow_repo(list_workflows=workflows)
        uow = make_mock_uow(workflow_repo=repo)

        result = await ListWorkflowsUseCase().execute(ListWorkflowsCommand(), uow)

        assert result.total_count == 2
        assert len(result.workflows) == 2

    async def test_empty_list(self) -> None:
        uow = make_mock_uow()

        result = await ListWorkflowsUseCase().execute(ListWorkflowsCommand(), uow)

        assert result.total_count == 0
        assert result.workflows == []

    async def test_include_templates_forwarded(self) -> None:
        repo = make_mock_workflow_repo()
        uow = make_mock_uow(workflow_repo=repo)

        await ListWorkflowsUseCase().execute(
            ListWorkflowsCommand(include_templates=False), uow
        )

        repo.list_workflows.assert_called_once_with(include_templates=False)


class TestGetWorkflow:
    async def test_returns_workflow(self) -> None:
        workflow = make_workflow(id=42)
        repo = make_mock_workflow_repo(get_workflow_by_id=workflow)
        uow = make_mock_uow(workflow_repo=repo)

        result = await GetWorkflowUseCase().execute(
            GetWorkflowCommand(workflow_id=42), uow
        )

        assert result.workflow.id == 42

    async def test_not_found_propagates(self) -> None:
        repo = make_mock_workflow_repo()
        repo.get_workflow_by_id.side_effect = NotFoundError("nope")
        uow = make_mock_uow(workflow_repo=repo)

        with pytest.raises(NotFoundError):
            await GetWorkflowUseCase().execute(GetWorkflowCommand(workflow_id=999), uow)


class TestCreateWorkflow:
    async def test_creates_workflow(self) -> None:
        wf_def = make_workflow_def()
        repo = make_mock_workflow_repo()
        uow = make_mock_uow(workflow_repo=repo)

        result = await CreateWorkflowUseCase().execute(
            CreateWorkflowCommand(definition=wf_def), uow
        )

        repo.save_workflow.assert_called_once()
        assert result.workflow.definition == wf_def

    async def test_validation_failure_raises(self) -> None:
        from src.domain.entities.workflow import WorkflowDef

        empty_def = WorkflowDef(id="empty", name="Empty", tasks=[])
        uow = make_mock_uow()

        with pytest.raises(ValueError, match="no tasks"):
            await CreateWorkflowUseCase().execute(
                CreateWorkflowCommand(definition=empty_def), uow
            )


class TestUpdateWorkflow:
    async def test_updates_workflow(self) -> None:
        existing = make_workflow(id=1, is_template=False)
        new_def = make_workflow_def(name="Updated")
        repo = make_mock_workflow_repo(get_workflow_by_id=existing)
        uow = make_mock_uow(workflow_repo=repo)

        await UpdateWorkflowUseCase().execute(
            UpdateWorkflowCommand(workflow_id=1, definition=new_def), uow
        )

        repo.save_workflow.assert_called_once()

    async def test_version_increments_when_tasks_change(self) -> None:
        """definition_version bumps when the task pipeline is modified."""
        from src.domain.entities.workflow import WorkflowTaskDef

        existing = make_workflow(id=1, definition_version=3)
        new_tasks = [
            WorkflowTaskDef(
                id="source", type="source.liked_tracks", config={"service": "spotify"}
            ),
            WorkflowTaskDef(
                id="filter",
                type="filter.by_metric",
                config={"metric_name": "play_count", "min_value": 1},
                upstream=["source"],
            ),
        ]
        new_def = make_workflow_def(tasks=new_tasks)
        repo = make_mock_workflow_repo(get_workflow_by_id=existing)
        uow = make_mock_uow(workflow_repo=repo)

        await UpdateWorkflowUseCase().execute(
            UpdateWorkflowCommand(workflow_id=1, definition=new_def), uow
        )

        saved = repo.save_workflow.call_args[0][0]
        assert saved.definition_version == 4

    async def test_version_preserved_when_only_name_changes(self) -> None:
        """definition_version stays the same when only name/description changes."""
        existing = make_workflow(id=1, definition_version=5)
        # Same tasks, different name
        new_def = make_workflow_def(name="New Name", tasks=existing.definition.tasks)
        repo = make_mock_workflow_repo(get_workflow_by_id=existing)
        uow = make_mock_uow(workflow_repo=repo)

        await UpdateWorkflowUseCase().execute(
            UpdateWorkflowCommand(workflow_id=1, definition=new_def), uow
        )

        saved = repo.save_workflow.call_args[0][0]
        assert saved.definition_version == 5

    async def test_template_rejection(self) -> None:
        template = make_workflow(id=1, is_template=True)
        repo = make_mock_workflow_repo(get_workflow_by_id=template)
        uow = make_mock_uow(workflow_repo=repo)

        with pytest.raises(TemplateReadOnlyError):
            await UpdateWorkflowUseCase().execute(
                UpdateWorkflowCommand(workflow_id=1, definition=make_workflow_def()),
                uow,
            )

    async def test_not_found_propagates(self) -> None:
        repo = make_mock_workflow_repo()
        repo.get_workflow_by_id.side_effect = NotFoundError("nope")
        uow = make_mock_uow(workflow_repo=repo)

        with pytest.raises(NotFoundError):
            await UpdateWorkflowUseCase().execute(
                UpdateWorkflowCommand(workflow_id=999, definition=make_workflow_def()),
                uow,
            )


class TestDeleteWorkflow:
    async def test_deletes_workflow(self) -> None:
        existing = make_workflow(id=1, is_template=False)
        repo = make_mock_workflow_repo(get_workflow_by_id=existing)
        uow = make_mock_uow(workflow_repo=repo)

        result = await DeleteWorkflowUseCase().execute(
            DeleteWorkflowCommand(workflow_id=1), uow
        )

        repo.delete_workflow.assert_called_once_with(1)
        assert result.workflow_id == 1

    async def test_template_rejection(self) -> None:
        template = make_workflow(id=1, is_template=True)
        repo = make_mock_workflow_repo(get_workflow_by_id=template)
        uow = make_mock_uow(workflow_repo=repo)

        with pytest.raises(TemplateReadOnlyError):
            await DeleteWorkflowUseCase().execute(
                DeleteWorkflowCommand(workflow_id=1), uow
            )

    async def test_not_found_propagates(self) -> None:
        repo = make_mock_workflow_repo()
        repo.get_workflow_by_id.side_effect = NotFoundError("nope")
        uow = make_mock_uow(workflow_repo=repo)

        with pytest.raises(NotFoundError):
            await DeleteWorkflowUseCase().execute(
                DeleteWorkflowCommand(workflow_id=999), uow
            )
