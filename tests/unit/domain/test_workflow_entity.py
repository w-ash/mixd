"""Unit tests for the Workflow domain entity.

Tests construction, defaults, and embedding of WorkflowDef.
"""

from src.domain.entities.workflow import Workflow, WorkflowDef, WorkflowTaskDef


class TestWorkflowConstruction:
    """Workflow entity construction and defaults."""

    def test_defaults(self) -> None:
        workflow = Workflow()

        assert workflow.id is None
        assert workflow.definition.id == ""
        assert workflow.definition.name == ""
        assert workflow.definition.tasks == []
        assert workflow.is_template is False
        assert workflow.source_template is None
        assert workflow.created_at is None
        assert workflow.updated_at is None

    def test_with_definition(self) -> None:
        wf_def = WorkflowDef(
            id="test",
            name="Test Workflow",
            description="A test",
            tasks=[
                WorkflowTaskDef(id="step1", type="source.liked_tracks"),
            ],
        )
        workflow = Workflow(
            id=1, definition=wf_def, is_template=True, source_template="test"
        )

        assert workflow.id == 1
        assert workflow.definition.name == "Test Workflow"
        assert workflow.definition.description == "A test"
        assert len(workflow.definition.tasks) == 1
        assert workflow.definition.tasks[0].id == "step1"
        assert workflow.is_template is True
        assert workflow.source_template == "test"

    def test_workflow_def_embedded_not_flattened(self) -> None:
        """WorkflowDef is a nested field, not flattened into Workflow."""
        wf_def = WorkflowDef(id="wf1", name="Name", tasks=[])
        workflow = Workflow(definition=wf_def)

        assert isinstance(workflow.definition, WorkflowDef)
        assert workflow.definition is wf_def
