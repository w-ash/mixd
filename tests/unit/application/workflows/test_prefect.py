"""Tests for Prefect workflow execution.

Tests that workflow result extraction, metrics aggregation, and node
timeout configuration work correctly.
Validation and topological sort tests have moved to test_validation.py.
"""

import pytest

from src.application.workflows.prefect import _get_node_timeout
from src.config.constants import WorkflowConstants
from src.domain.entities.track import TrackList
from src.domain.entities.workflow import WorkflowDef, WorkflowTaskDef


class TestExtractWorkflowResult:
    """Tests for extract_workflow_result with typed task results."""

    def test_extracts_destination_tracklist(self, sample_tracklist):
        """Destination task's tracklist becomes the final result tracks."""
        from src.application.workflows.prefect import extract_workflow_result

        workflow_def = WorkflowDef(
            id="test",
            name="test_workflow",
            tasks=[
                WorkflowTaskDef(id="src_1", type="source.playlist"),
                WorkflowTaskDef(
                    id="dest_1",
                    type="destination.playlist",
                    upstream=["src_1"],
                ),
            ],
        )

        task_results = {
            "src_1": {"tracklist": sample_tracklist},
            "dest_1": {"tracklist": sample_tracklist},
        }

        result = extract_workflow_result(workflow_def, task_results, 1.0)

        assert result.tracks == sample_tracklist.tracks
        assert result.operation_name == "test_workflow"

    def test_extracts_metrics_from_task_results(self, sample_tracklist):
        """Metrics should be extracted from task results that have tracklist entries."""
        from src.application.workflows.prefect import extract_workflow_result

        tracklist_with_metrics = TrackList(
            tracks=sample_tracklist.tracks,
            metadata={"metrics": {"lastfm_plays": {1: 42, 2: 10}}},
        )

        workflow_def = WorkflowDef(
            id="metrics_test",
            name="metrics_test",
            tasks=[
                WorkflowTaskDef(id="src_1", type="source.playlist"),
                WorkflowTaskDef(
                    id="enricher_1",
                    type="enricher.lastfm",
                    upstream=["src_1"],
                ),
                WorkflowTaskDef(
                    id="dest_1",
                    type="destination.playlist",
                    upstream=["enricher_1"],
                ),
            ],
        )

        task_results = {
            "src_1": {"tracklist": sample_tracklist},
            "enricher_1": {"tracklist": tracklist_with_metrics},
            "dest_1": {"tracklist": tracklist_with_metrics},
        }

        result = extract_workflow_result(workflow_def, task_results, 1.0)

        assert "lastfm_plays" in result.metrics
        assert result.metrics["lastfm_plays"][1] == 42


class TestAggregateWorkflowMetrics:
    """Tests for _aggregate_workflow_metrics helper."""

    def test_aggregates_metrics_across_tasks(self, sample_tracklist):
        """Metrics from multiple tasks are merged."""
        from src.application.workflows.prefect import _aggregate_workflow_metrics

        tl1 = TrackList(
            tracks=sample_tracklist.tracks,
            metadata={"metrics": {"plays": {1: 10}}},
        )
        tl2 = TrackList(
            tracks=sample_tracklist.tracks,
            metadata={"metrics": {"plays": {2: 20}, "popularity": {1: 80}}},
        )

        task_results = {
            "task_a": {"tracklist": tl1},
            "task_b": {"tracklist": tl2},
        }

        result = _aggregate_workflow_metrics(task_results)

        assert result["plays"] == {1: 10, 2: 20}
        assert result["popularity"] == {1: 80}

    def test_empty_metrics_handled(self, sample_tracklist):
        """Tasks without metrics in metadata produce empty result."""
        from src.application.workflows.prefect import _aggregate_workflow_metrics

        task_results = {
            "task_1": {"tracklist": sample_tracklist},
        }

        result = _aggregate_workflow_metrics(task_results)
        assert result == {}


class TestGetNodeTimeout:
    """Tests for _get_node_timeout category-based timeout mapping."""

    def test_source_timeout(self):
        """Source nodes get the external API timeout."""
        assert _get_node_timeout("source.playlist") == WorkflowConstants.SOURCE_TIMEOUT_SECONDS

    def test_enricher_timeout(self):
        """Enricher nodes get the external API timeout."""
        assert _get_node_timeout("enricher.spotify") == WorkflowConstants.ENRICHER_TIMEOUT_SECONDS

    def test_destination_timeout(self):
        """Destination nodes get the external API timeout."""
        assert _get_node_timeout("destination.create_playlist") == WorkflowConstants.DESTINATION_TIMEOUT_SECONDS

    def test_filter_defaults_to_transform(self):
        """Filter nodes get the transform (pure) timeout."""
        assert _get_node_timeout("filter.by_metric") == WorkflowConstants.TRANSFORM_TIMEOUT_SECONDS

    def test_unknown_defaults_to_transform(self):
        """Unknown categories default to transform timeout."""
        assert _get_node_timeout("unknown.thing") == WorkflowConstants.TRANSFORM_TIMEOUT_SECONDS
