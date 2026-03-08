"""Unit tests for workflow version history use cases.

Tests list, get, and revert version operations using mock UoW.
"""

from unittest.mock import AsyncMock

import pytest

from src.application.use_cases.workflow_versions import (
    GetWorkflowVersionCommand,
    GetWorkflowVersionUseCase,
    ListWorkflowVersionsCommand,
    ListWorkflowVersionsUseCase,
    RevertWorkflowVersionCommand,
    RevertWorkflowVersionUseCase,
)
from src.domain.entities.workflow import WorkflowVersion
from src.domain.exceptions import NotFoundError
from tests.fixtures import (
    make_mock_uow,
    make_mock_workflow_repo,
    make_workflow,
    make_workflow_def,
)


def _make_version(
    workflow_id: int = 1, version: int = 1, **kwargs
) -> WorkflowVersion:
    return WorkflowVersion(
        id=version,
        workflow_id=workflow_id,
        version=version,
        definition=make_workflow_def(),
        **kwargs,
    )


class TestListWorkflowVersions:
    async def test_returns_versions(self) -> None:
        workflow = make_workflow(id=1)
        versions = [_make_version(version=2), _make_version(version=1)]
        wf_repo = make_mock_workflow_repo(get_workflow_by_id=workflow)
        version_repo = AsyncMock()
        version_repo.list_versions.return_value = versions
        uow = make_mock_uow(workflow_repo=wf_repo, workflow_version_repo=version_repo)

        result = await ListWorkflowVersionsUseCase().execute(
            ListWorkflowVersionsCommand(workflow_id=1), uow
        )

        assert len(result.versions) == 2
        assert result.versions[0].version == 2
        version_repo.list_versions.assert_called_once_with(1)

    async def test_workflow_not_found(self) -> None:
        wf_repo = make_mock_workflow_repo()
        wf_repo.get_workflow_by_id.side_effect = NotFoundError("not found")
        uow = make_mock_uow(workflow_repo=wf_repo)

        with pytest.raises(NotFoundError):
            await ListWorkflowVersionsUseCase().execute(
                ListWorkflowVersionsCommand(workflow_id=999), uow
            )


class TestGetWorkflowVersion:
    async def test_returns_version(self) -> None:
        workflow = make_workflow(id=1)
        version = _make_version(version=3, change_summary="Added 1 node")
        wf_repo = make_mock_workflow_repo(get_workflow_by_id=workflow)
        version_repo = AsyncMock()
        version_repo.get_version.return_value = version
        uow = make_mock_uow(workflow_repo=wf_repo, workflow_version_repo=version_repo)

        result = await GetWorkflowVersionUseCase().execute(
            GetWorkflowVersionCommand(workflow_id=1, version=3), uow
        )

        assert result.version.version == 3
        assert result.version.change_summary == "Added 1 node"

    async def test_version_not_found(self) -> None:
        workflow = make_workflow(id=1)
        wf_repo = make_mock_workflow_repo(get_workflow_by_id=workflow)
        version_repo = AsyncMock()
        version_repo.get_version.side_effect = NotFoundError("v99 not found")
        uow = make_mock_uow(workflow_repo=wf_repo, workflow_version_repo=version_repo)

        with pytest.raises(NotFoundError):
            await GetWorkflowVersionUseCase().execute(
                GetWorkflowVersionCommand(workflow_id=1, version=99), uow
            )


class TestRevertWorkflowVersion:
    async def test_reverts_and_creates_snapshot(self) -> None:
        """Revert snapshots current def as new version, then updates workflow."""
        existing = make_workflow(id=1, definition_version=5)
        old_def = make_workflow_def(name="Old Version")
        target_version = _make_version(version=2)
        target_version = WorkflowVersion(
            id=2,
            workflow_id=1,
            version=2,
            definition=old_def,
        )

        wf_repo = make_mock_workflow_repo(get_workflow_by_id=existing)
        version_repo = AsyncMock()
        version_repo.get_version.return_value = target_version
        version_repo.get_max_version_number.return_value = 3
        version_repo.create_version.side_effect = lambda v: v
        uow = make_mock_uow(workflow_repo=wf_repo, workflow_version_repo=version_repo)

        result = await RevertWorkflowVersionUseCase().execute(
            RevertWorkflowVersionCommand(workflow_id=1, version=2), uow
        )

        # Snapshot was created with next version number (4)
        version_repo.create_version.assert_called_once()
        created_snapshot = version_repo.create_version.call_args[0][0]
        assert created_snapshot.version == 4
        assert created_snapshot.change_summary == "Before revert to v2"

        # Workflow was saved with the reverted definition
        wf_repo.save_workflow.assert_called_once()
        saved = wf_repo.save_workflow.call_args[0][0]
        assert saved.definition.name == "Old Version"
        assert saved.definition_version == 6  # existing 5 + 1
