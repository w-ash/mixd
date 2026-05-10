"""Unit tests for the personal workflow seeder.

Mirrors ``test_workflow_template_seeder.py`` but exercises the user-owned,
non-template path: existing workflows are matched by ``definition.id`` per
user (not by ``source_template``), and rows are written with ``is_template=False``.
"""

from unittest.mock import patch

from src.application.services.personal_workflow_seeder import seed_personal_workflows
from src.domain.entities.workflow import WorkflowDef, WorkflowTaskDef
from tests.fixtures import make_mock_uow, make_mock_workflow_repo, make_workflow


def _sample_defs() -> list[WorkflowDef]:
    return [
        WorkflowDef(
            id="personal_a",
            name="Personal A",
            tasks=[WorkflowTaskDef(id="s1", type="source.liked_tracks")],
        ),
        WorkflowDef(
            id="personal_b",
            name="Personal B",
            tasks=[WorkflowTaskDef(id="s2", type="source.liked_tracks")],
        ),
    ]


class TestSeedPersonalWorkflows:
    async def test_first_run_creates_personal_workflows(self) -> None:
        repo = make_mock_workflow_repo(list_workflows=[])
        uow = make_mock_uow(workflow_repo=repo)

        with patch(
            "src.application.services.personal_workflow_seeder.list_workflow_defs",
            return_value=_sample_defs(),
        ):
            count = await seed_personal_workflows(uow, user_id="u1")

        assert count == 2
        assert repo.save_workflow.call_count == 2
        for call in repo.save_workflow.await_args_list:
            saved = call.args[0]
            assert saved.user_id == "u1"
            assert saved.is_template is False
            assert saved.source_template is None

    async def test_second_run_updates_existing(self) -> None:
        existing = make_workflow(
            user_id="u1",
            is_template=False,
            definition=WorkflowDef(id="personal_a", name="Personal A (old)"),
        )
        repo = make_mock_workflow_repo(list_workflows=[existing])
        uow = make_mock_uow(workflow_repo=repo)

        with patch(
            "src.application.services.personal_workflow_seeder.list_workflow_defs",
            return_value=_sample_defs(),
        ):
            count = await seed_personal_workflows(uow, user_id="u1")

        assert count == 2
        assert repo.save_workflow.call_count == 2
        # First saved workflow is the update — same id as existing
        updated = repo.save_workflow.await_args_list[0].args[0]
        assert updated.id == existing.id
        assert updated.definition.name == "Personal A"
        # Second is a new insert (no matching slug in existing)
        inserted = repo.save_workflow.await_args_list[1].args[0]
        assert inserted.id != existing.id

    async def test_no_definitions_returns_zero(self) -> None:
        repo = make_mock_workflow_repo()
        uow = make_mock_uow(workflow_repo=repo)

        with patch(
            "src.application.services.personal_workflow_seeder.list_workflow_defs",
            return_value=[],
        ):
            count = await seed_personal_workflows(uow, user_id="u1")

        assert count == 0
        repo.save_workflow.assert_not_awaited()

    async def test_lookup_scoped_to_user_id(self) -> None:
        repo = make_mock_workflow_repo(list_workflows=[])
        uow = make_mock_uow(workflow_repo=repo)

        with patch(
            "src.application.services.personal_workflow_seeder.list_workflow_defs",
            return_value=_sample_defs(),
        ):
            await seed_personal_workflows(uow, user_id="u1")

        repo.list_workflows.assert_awaited_once_with(
            user_id="u1", include_templates=False
        )
