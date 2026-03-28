"""Unit tests for the Workflow domain entity.

Tests construction, defaults, and embedding of WorkflowDef.
"""

from uuid import UUID, uuid7

from src.domain.entities.workflow import (
    Workflow,
    WorkflowDef,
    WorkflowRun,
    WorkflowRunNode,
    WorkflowTaskDef,
)


class TestWorkflowConstruction:
    """Workflow entity construction and defaults."""

    def test_defaults(self) -> None:
        workflow = Workflow()

        assert isinstance(workflow.id, UUID)
        assert workflow.definition.id == ""
        assert workflow.definition.name == ""
        assert workflow.definition.tasks == []
        assert workflow.is_template is False
        assert workflow.source_template is None
        assert workflow.created_at is None
        assert workflow.updated_at is None

    def test_with_definition(self) -> None:
        wf_id = uuid7()
        wf_def = WorkflowDef(
            id="test",
            name="Test Workflow",
            description="A test",
            tasks=[
                WorkflowTaskDef(id="step1", type="source.liked_tracks"),
            ],
        )
        workflow = Workflow(
            id=wf_id, definition=wf_def, is_template=True, source_template="test"
        )

        assert workflow.id == wf_id
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


class TestWorkflowRunConstruction:
    """WorkflowRun and WorkflowRunNode entity construction."""

    def test_run_defaults(self) -> None:
        run = WorkflowRun()
        assert isinstance(run.id, UUID)
        assert isinstance(run.workflow_id, UUID)
        assert run.status == "pending"
        assert run.definition_snapshot.id == ""
        assert run.nodes == []
        assert run.started_at is None
        assert run.completed_at is None
        assert run.duration_ms is None
        assert run.error_message is None

    def test_run_with_nodes(self) -> None:
        run_id = uuid7()
        wf_id = uuid7()
        nodes = [
            WorkflowRunNode(
                node_id="step1", node_type="source.liked_tracks", execution_order=1
            ),
            WorkflowRunNode(
                node_id="step2", node_type="filter.by_metric", execution_order=2
            ),
        ]
        run = WorkflowRun(
            id=run_id,
            workflow_id=wf_id,
            status="running",
            nodes=nodes,
        )
        assert run.id == run_id
        assert run.workflow_id == wf_id
        assert run.status == "running"
        assert len(run.nodes) == 2
        assert run.nodes[0].node_id == "step1"
        assert run.nodes[1].execution_order == 2

    def test_run_node_defaults(self) -> None:
        node = WorkflowRunNode()
        assert isinstance(node.id, UUID)
        assert isinstance(node.run_id, UUID)
        assert node.status == "pending"
        assert node.duration_ms == 0
        assert node.input_track_count is None
        assert node.output_track_count is None
        assert node.error_message is None

    def test_run_snapshot_frozen_at_creation(self) -> None:
        """definition_snapshot captures a WorkflowDef — proves it stores the definition."""
        wf_def = WorkflowDef(
            id="test",
            name="Test",
            tasks=[WorkflowTaskDef(id="s1", type="source.liked_tracks")],
        )
        run = WorkflowRun(definition_snapshot=wf_def)
        assert run.definition_snapshot.name == "Test"
        assert len(run.definition_snapshot.tasks) == 1
