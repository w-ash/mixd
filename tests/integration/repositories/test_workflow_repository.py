"""Integration tests for WorkflowRepository with real database operations.

Tests CRUD and user-scoping behavior.
"""

from contextlib import suppress
from uuid import uuid7

import pytest

from src.domain.entities.workflow import Workflow
from src.domain.exceptions import NotFoundError
from src.infrastructure.persistence.repositories.workflow.core import WorkflowRepository
from tests.fixtures import make_workflow_def


class TestWorkflowRepositoryCRUD:
    async def test_save_and_retrieve(self, db_session) -> None:
        repo = WorkflowRepository(db_session)
        workflow = Workflow(user_id="default", definition=make_workflow_def())

        saved = await repo.save_workflow(workflow)
        assert saved.id is not None
        assert saved.definition.name == "Test Workflow"

        retrieved = await repo.get_workflow_by_id(saved.id, user_id="default")
        assert retrieved.definition.id == "test-workflow"
        assert len(retrieved.definition.tasks) == 1

    async def test_update_existing(self, db_session) -> None:
        repo = WorkflowRepository(db_session)
        saved = await repo.save_workflow(
            Workflow(user_id="default", definition=make_workflow_def())
        )

        updated_def = make_workflow_def(name="Updated Name")
        updated = Workflow(
            id=saved.id,
            user_id="default",
            definition=updated_def,
            created_at=saved.created_at,
        )
        result = await repo.save_workflow(updated)
        assert result.definition.name == "Updated Name"
        assert result.id == saved.id

    async def test_delete_removes_only_the_target_and_returns_true(
        self, db_session
    ) -> None:
        repo = WorkflowRepository(db_session)
        saved = await repo.save_workflow(
            Workflow(user_id="default", definition=make_workflow_def("wf-gone"))
        )
        kept = await repo.save_workflow(
            Workflow(user_id="default", definition=make_workflow_def("wf-kept"))
        )

        deleted = await repo.delete_workflow(saved.id, user_id="default")

        assert deleted is True
        with pytest.raises(NotFoundError):
            await repo.get_workflow_by_id(saved.id, user_id="default")
        survivor = await repo.get_workflow_by_id(kept.id, user_id="default")
        assert survivor.definition.id == "wf-kept"

    async def test_delete_nonexistent_returns_false(self, db_session) -> None:
        repo = WorkflowRepository(db_session)

        deleted = await repo.delete_workflow(uuid7(), user_id="default")
        assert deleted is False

    async def test_get_nonexistent_raises(self, db_session) -> None:
        repo = WorkflowRepository(db_session)

        with pytest.raises(NotFoundError):
            await repo.get_workflow_by_id(uuid7(), user_id="default")


class TestWorkflowRepositoryScoping:
    async def test_list_is_user_scoped(self, db_session) -> None:
        repo = WorkflowRepository(db_session)
        await repo.save_workflow(
            Workflow(user_id="user-a", definition=make_workflow_def("wf1"))
        )
        await repo.save_workflow(
            Workflow(user_id="user-b", definition=make_workflow_def("wf2"))
        )

        a_workflows = await repo.list_workflows(user_id="user-a")
        assert len(a_workflows) == 1
        assert a_workflows[0].definition.id == "wf1"

    async def test_get_other_users_workflow_raises(self, db_session) -> None:
        repo = WorkflowRepository(db_session)
        saved = await repo.save_workflow(
            Workflow(user_id="user-a", definition=make_workflow_def("wf1"))
        )

        with pytest.raises(NotFoundError):
            await repo.get_workflow_by_id(saved.id, user_id="user-b")

    @pytest.mark.xfail(
        strict=True,
        reason="save_workflow has no user filter — cross-tenant overwrite; see backlog",
    )
    async def test_save_with_another_users_id_leaves_their_workflow_unchanged(
        self, db_session
    ) -> None:
        """User B saving under user A's workflow id must not rewrite A's workflow.

        ``save_workflow`` finds the existing row by id alone, so B's entity
        updates A's row in place. RLS does not stop it: the app role bypasses
        RLS in production, and the test container connects as a superuser.
        """
        repo = WorkflowRepository(db_session)
        owned = await repo.save_workflow(
            Workflow(
                user_id="user-a",
                definition=make_workflow_def("wf-a", name="A's workflow"),
            )
        )

        # A fix may refuse the write outright; the contract is A's row, below.
        with suppress(NotFoundError):
            _ = await repo.save_workflow(
                Workflow(
                    id=owned.id,
                    user_id="user-b",
                    definition=make_workflow_def("wf-b", name="B's takeover"),
                )
            )

        as_owner = await repo.get_workflow_by_id(owned.id, user_id="user-a")
        assert as_owner.definition.id == "wf-a"
        assert as_owner.definition.name == "A's workflow"
