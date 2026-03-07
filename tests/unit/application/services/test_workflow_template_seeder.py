"""Unit tests for workflow template seeder.

Tests idempotent seeding: first run creates, second run updates.
"""

from unittest.mock import patch

from src.application.services.workflow_template_seeder import seed_workflow_templates
from src.domain.entities.workflow import WorkflowDef, WorkflowTaskDef
from tests.fixtures import make_mock_uow, make_mock_workflow_repo, make_workflow


def _sample_defs() -> list[WorkflowDef]:
    return [
        WorkflowDef(
            id="wf1",
            name="Workflow 1",
            tasks=[WorkflowTaskDef(id="s1", type="source.liked_tracks")],
        ),
        WorkflowDef(
            id="wf2",
            name="Workflow 2",
            tasks=[WorkflowTaskDef(id="s2", type="source.liked_tracks")],
        ),
    ]


class TestSeedWorkflowTemplates:
    async def test_first_run_creates_templates(self) -> None:
        repo = make_mock_workflow_repo()
        # No existing templates
        repo.get_workflow_by_source_template.return_value = None
        uow = make_mock_uow(workflow_repo=repo)

        with patch(
            "src.application.workflows.workflow_loader.list_workflow_defs",
            return_value=_sample_defs(),
        ):
            count = await seed_workflow_templates(uow)

        assert count == 2
        assert repo.save_workflow.call_count == 2

    async def test_second_run_updates_existing(self) -> None:
        existing = make_workflow(id=10, is_template=True, source_template="wf1")
        repo = make_mock_workflow_repo()
        # First def exists, second doesn't
        repo.get_workflow_by_source_template.side_effect = [existing, None]
        uow = make_mock_uow(workflow_repo=repo)

        with patch(
            "src.application.workflows.workflow_loader.list_workflow_defs",
            return_value=_sample_defs(),
        ):
            count = await seed_workflow_templates(uow)

        assert count == 2
        assert repo.save_workflow.call_count == 2

    async def test_no_definitions_returns_zero(self) -> None:
        uow = make_mock_uow()

        with patch(
            "src.application.workflows.workflow_loader.list_workflow_defs",
            return_value=[],
        ):
            count = await seed_workflow_templates(uow)

        assert count == 0
