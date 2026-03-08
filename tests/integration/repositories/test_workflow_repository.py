"""Integration tests for WorkflowRepository with real database operations.

Tests CRUD, template filtering, and source_template upsert behavior.
"""

import pytest

from src.domain.entities.workflow import Workflow
from src.domain.exceptions import NotFoundError
from src.infrastructure.persistence.repositories.workflow.core import WorkflowRepository
from tests.fixtures import make_workflow_def


class TestWorkflowRepositoryCRUD:
    async def test_save_and_retrieve(self, db_session) -> None:
        repo = WorkflowRepository(db_session)
        workflow = Workflow(definition=make_workflow_def())

        saved = await repo.save_workflow(workflow)
        assert saved.id is not None
        assert saved.definition.name == "Test Workflow"

        retrieved = await repo.get_workflow_by_id(saved.id)
        assert retrieved.definition.id == "test-workflow"
        assert len(retrieved.definition.tasks) == 1

    async def test_update_existing(self, db_session) -> None:
        repo = WorkflowRepository(db_session)
        saved = await repo.save_workflow(Workflow(definition=make_workflow_def()))

        updated_def = make_workflow_def(name="Updated Name")
        updated = Workflow(
            id=saved.id,
            definition=updated_def,
            created_at=saved.created_at,
        )
        result = await repo.save_workflow(updated)
        assert result.definition.name == "Updated Name"
        assert result.id == saved.id

    async def test_delete_returns_true(self, db_session) -> None:
        repo = WorkflowRepository(db_session)
        saved = await repo.save_workflow(Workflow(definition=make_workflow_def()))

        deleted = await repo.delete_workflow(saved.id)
        assert deleted is True

    async def test_delete_nonexistent_returns_false(self, db_session) -> None:
        repo = WorkflowRepository(db_session)

        deleted = await repo.delete_workflow(99999)
        assert deleted is False

    async def test_get_nonexistent_raises(self, db_session) -> None:
        repo = WorkflowRepository(db_session)

        with pytest.raises(NotFoundError):
            await repo.get_workflow_by_id(99999)


class TestWorkflowRepositoryTemplates:
    async def test_list_includes_templates_by_default(self, db_session) -> None:
        repo = WorkflowRepository(db_session)
        await repo.save_workflow(
            Workflow(definition=make_workflow_def("wf1"), is_template=True)
        )
        await repo.save_workflow(
            Workflow(definition=make_workflow_def("wf2"), is_template=False)
        )

        all_workflows = await repo.list_workflows()
        assert len(all_workflows) == 2

    async def test_list_excludes_templates(self, db_session) -> None:
        repo = WorkflowRepository(db_session)
        await repo.save_workflow(
            Workflow(definition=make_workflow_def("wf1"), is_template=True)
        )
        await repo.save_workflow(
            Workflow(definition=make_workflow_def("wf2"), is_template=False)
        )

        user_workflows = await repo.list_workflows(include_templates=False)
        assert len(user_workflows) == 1
        assert user_workflows[0].is_template is False

    async def test_source_template_lookup(self, db_session) -> None:
        repo = WorkflowRepository(db_session)
        await repo.save_workflow(
            Workflow(
                definition=make_workflow_def("builtin"),
                is_template=True,
                source_template="builtin",
            )
        )

        found = await repo.get_workflow_by_source_template("builtin")
        assert found is not None
        assert found.source_template == "builtin"

    async def test_source_template_not_found(self, db_session) -> None:
        repo = WorkflowRepository(db_session)

        found = await repo.get_workflow_by_source_template("nonexistent")
        assert found is None

    async def test_source_template_unique_constraint(self, db_session) -> None:
        repo = WorkflowRepository(db_session)
        await repo.save_workflow(
            Workflow(
                definition=make_workflow_def("a"),
                is_template=True,
                source_template="key1",
            )
        )
        # Flushing a duplicate source_template should raise
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            await repo.save_workflow(
                Workflow(
                    definition=make_workflow_def("b"),
                    is_template=True,
                    source_template="key1",
                )
            )
