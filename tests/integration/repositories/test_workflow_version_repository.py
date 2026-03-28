"""Integration tests for WorkflowVersionRepository with real database operations.

Tests CRUD operations: create, list, get, and delete workflow versions.
"""

from uuid import uuid7

import pytest

from src.domain.entities.workflow import Workflow, WorkflowVersion
from src.domain.exceptions import NotFoundError
from src.infrastructure.persistence.repositories.workflow.core import WorkflowRepository
from src.infrastructure.persistence.repositories.workflow.versions import (
    WorkflowVersionRepository,
)
from tests.fixtures import make_workflow_def


async def _create_parent_workflow(db_session) -> Workflow:
    """Create and persist a parent workflow, returning the saved domain entity."""
    repo = WorkflowRepository(db_session)
    return await repo.save_workflow(Workflow(definition=make_workflow_def()))


class TestCreateVersion:
    async def test_creates_and_returns_version(self, db_session) -> None:
        parent = await _create_parent_workflow(db_session)
        repo = WorkflowVersionRepository(db_session)

        version = WorkflowVersion(
            workflow_id=parent.id,
            version=1,
            definition=parent.definition,
            change_summary="Initial version",
        )

        saved = await repo.create_version(version)

        assert saved.id is not None
        assert saved.workflow_id == parent.id
        assert saved.version == 1
        assert saved.definition.name == parent.definition.name
        assert saved.change_summary == "Initial version"
        assert saved.created_at is not None

    async def test_creates_version_without_change_summary(self, db_session) -> None:
        parent = await _create_parent_workflow(db_session)
        repo = WorkflowVersionRepository(db_session)

        version = WorkflowVersion(
            workflow_id=parent.id,
            version=1,
            definition=parent.definition,
        )

        saved = await repo.create_version(version)

        assert saved.id is not None
        assert saved.change_summary is None

    async def test_preserves_definition_tasks(self, db_session) -> None:
        parent = await _create_parent_workflow(db_session)
        repo = WorkflowVersionRepository(db_session)

        version = WorkflowVersion(
            workflow_id=parent.id,
            version=1,
            definition=parent.definition,
        )

        saved = await repo.create_version(version)

        assert len(saved.definition.tasks) == len(parent.definition.tasks)
        assert saved.definition.tasks[0].type == "source.liked_tracks"


class TestListVersions:
    async def test_returns_versions_sorted_descending(self, db_session) -> None:
        parent = await _create_parent_workflow(db_session)
        repo = WorkflowVersionRepository(db_session)

        for v in range(1, 4):
            await repo.create_version(
                WorkflowVersion(
                    workflow_id=parent.id,
                    version=v,
                    definition=parent.definition,
                    change_summary=f"Version {v}",
                )
            )

        versions = await repo.list_versions(parent.id)

        assert len(versions) == 3
        assert [v.version for v in versions] == [3, 2, 1]

    async def test_returns_empty_list_for_no_versions(self, db_session) -> None:
        parent = await _create_parent_workflow(db_session)
        repo = WorkflowVersionRepository(db_session)

        versions = await repo.list_versions(parent.id)

        assert versions == []

    async def test_scoped_to_workflow_id(self, db_session) -> None:
        """Versions for one workflow do not leak into another workflow's list."""
        workflow_repo = WorkflowRepository(db_session)
        wf_a = await workflow_repo.save_workflow(
            Workflow(definition=make_workflow_def(id="wf-a", name="Workflow A"))
        )
        wf_b = await workflow_repo.save_workflow(
            Workflow(definition=make_workflow_def(id="wf-b", name="Workflow B"))
        )

        version_repo = WorkflowVersionRepository(db_session)
        await version_repo.create_version(
            WorkflowVersion(
                workflow_id=wf_a.id,
                version=1,
                definition=wf_a.definition,
            )
        )
        await version_repo.create_version(
            WorkflowVersion(
                workflow_id=wf_b.id,
                version=1,
                definition=wf_b.definition,
            )
        )

        a_versions = await version_repo.list_versions(wf_a.id)
        b_versions = await version_repo.list_versions(wf_b.id)

        assert len(a_versions) == 1
        assert a_versions[0].definition.name == "Workflow A"
        assert len(b_versions) == 1
        assert b_versions[0].definition.name == "Workflow B"


class TestGetVersion:
    async def test_returns_specific_version(self, db_session) -> None:
        parent = await _create_parent_workflow(db_session)
        repo = WorkflowVersionRepository(db_session)

        await repo.create_version(
            WorkflowVersion(
                workflow_id=parent.id,
                version=1,
                definition=parent.definition,
                change_summary="First",
            )
        )
        await repo.create_version(
            WorkflowVersion(
                workflow_id=parent.id,
                version=2,
                definition=parent.definition,
                change_summary="Second",
            )
        )

        result = await repo.get_version(parent.id, 2)

        assert result.version == 2
        assert result.change_summary == "Second"

    async def test_raises_not_found_for_nonexistent_version(self, db_session) -> None:
        parent = await _create_parent_workflow(db_session)
        repo = WorkflowVersionRepository(db_session)

        with pytest.raises(NotFoundError, match="Version 99 not found"):
            await repo.get_version(parent.id, 99)

    async def test_raises_not_found_for_nonexistent_workflow(self, db_session) -> None:
        repo = WorkflowVersionRepository(db_session)

        with pytest.raises(NotFoundError):
            await repo.get_version(uuid7(), 1)


class TestDeleteVersionsForWorkflow:
    async def test_deletes_all_versions(self, db_session) -> None:
        parent = await _create_parent_workflow(db_session)
        repo = WorkflowVersionRepository(db_session)

        for v in range(1, 4):
            await repo.create_version(
                WorkflowVersion(
                    workflow_id=parent.id,
                    version=v,
                    definition=parent.definition,
                )
            )

        await repo.delete_versions_for_workflow(parent.id)

        remaining = await repo.list_versions(parent.id)
        assert remaining == []

    async def test_does_not_affect_other_workflows(self, db_session) -> None:
        workflow_repo = WorkflowRepository(db_session)
        wf_a = await workflow_repo.save_workflow(
            Workflow(definition=make_workflow_def(id="wf-a", name="Workflow A"))
        )
        wf_b = await workflow_repo.save_workflow(
            Workflow(definition=make_workflow_def(id="wf-b", name="Workflow B"))
        )

        version_repo = WorkflowVersionRepository(db_session)
        await version_repo.create_version(
            WorkflowVersion(workflow_id=wf_a.id, version=1, definition=wf_a.definition)
        )
        await version_repo.create_version(
            WorkflowVersion(workflow_id=wf_b.id, version=1, definition=wf_b.definition)
        )

        await version_repo.delete_versions_for_workflow(wf_a.id)

        assert await version_repo.list_versions(wf_a.id) == []
        assert len(await version_repo.list_versions(wf_b.id)) == 1

    async def test_noop_when_no_versions_exist(self, db_session) -> None:
        parent = await _create_parent_workflow(db_session)
        repo = WorkflowVersionRepository(db_session)

        # Should not raise
        await repo.delete_versions_for_workflow(parent.id)
