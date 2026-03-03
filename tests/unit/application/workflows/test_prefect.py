"""Tests for Prefect workflow execution.

Tests that workflow result extraction, metrics aggregation, and topological
sort (including cycle detection) work correctly.
"""

import pytest

from src.domain.entities.track import TrackList


class TestValidateWorkflowDef:
    """Tests for validate_workflow_def structural checks."""

    def test_empty_workflow_raises(self):
        """Workflow with no tasks raises ValueError."""
        from src.application.workflows.prefect import validate_workflow_def

        with pytest.raises(ValueError, match="Workflow has no tasks"):
            validate_workflow_def({"tasks": []})

    def test_missing_task_type_raises(self):
        """Task without 'type' field raises ValueError."""
        from src.application.workflows.prefect import validate_workflow_def

        with pytest.raises(ValueError, match="missing required 'type'"):
            validate_workflow_def({"tasks": [{"id": "src_1"}]})

    def test_missing_task_id_raises(self):
        """Task without 'id' field raises ValueError."""
        from src.application.workflows.prefect import validate_workflow_def

        with pytest.raises(ValueError, match="missing required 'id'"):
            validate_workflow_def({"tasks": [{"type": "source.playlist"}]})

    def test_unknown_upstream_raises(self):
        """Upstream reference to nonexistent task raises ValueError."""
        from src.application.workflows.prefect import validate_workflow_def

        with pytest.raises(ValueError, match="unknown upstream 'nonexistent'"):
            validate_workflow_def({
                "tasks": [
                    {"id": "src_1", "type": "source.playlist"},
                    {
                        "id": "dest_1",
                        "type": "destination.create_playlist",
                        "upstream": ["nonexistent"],
                    },
                ]
            })

    def test_unknown_node_type_raises(self):
        """Unregistered node type raises ValueError."""
        from src.application.workflows.prefect import validate_workflow_def

        with pytest.raises(ValueError, match=r"unknown node type 'totally\.fake'"):
            validate_workflow_def({"tasks": [{"id": "src_1", "type": "totally.fake"}]})

    def test_valid_workflow_passes(self):
        """Well-formed workflow definition passes validation."""
        from src.application.workflows.prefect import validate_workflow_def

        # Use real registered node types with required config keys
        validate_workflow_def({
            "tasks": [
                {
                    "id": "src_1",
                    "type": "source.playlist",
                    "config": {"playlist_id": "test-123"},
                },
                {
                    "id": "dest_1",
                    "type": "destination.create_playlist",
                    "config": {"name": "Test Playlist"},
                    "upstream": ["src_1"],
                },
            ]
        })

    def test_missing_required_config_raises(self):
        """Node with missing required config key raises ValueError."""
        from src.application.workflows.prefect import validate_workflow_def

        with pytest.raises(ValueError, match=r"missing required config.*playlist_id"):
            validate_workflow_def({
                "tasks": [
                    {"id": "src_1", "type": "source.playlist", "config": {}},
                ]
            })

    def test_wrong_type_config_value_raises(self):
        """Config value with wrong type (int instead of str) raises ValueError."""
        from src.application.workflows.prefect import validate_workflow_def

        with pytest.raises(ValueError, match=r"must be str.*got int"):
            validate_workflow_def({
                "tasks": [
                    {"id": "src_1", "type": "source.playlist", "config": {"playlist_id": 123}},
                ]
            })

    def test_empty_string_config_value_raises(self):
        """Empty string for required string config key raises ValueError."""
        from src.application.workflows.prefect import validate_workflow_def

        with pytest.raises(ValueError, match="must not be empty"):
            validate_workflow_def({
                "tasks": [
                    {"id": "src_1", "type": "source.playlist", "config": {"playlist_id": "  "}},
                ]
            })

    def test_numeric_percentage_accepted(self):
        """Percentage config accepts both int and float values."""
        from src.application.workflows.prefect import _validate_node_config

        # int should pass
        _validate_node_config("selector.percentage", {"percentage": 50}, task_id="sel_1")
        # float should pass
        _validate_node_config("selector.percentage", {"percentage": 33.3}, task_id="sel_1")

    def test_string_as_number_config_raises(self):
        """String value for numeric config key raises ValueError."""
        from src.application.workflows.prefect import _validate_node_config

        with pytest.raises(ValueError, match=r"must be int \| float.*got str"):
            _validate_node_config(
                "selector.percentage", {"percentage": "50"}, task_id="sel_1"
            )

    def test_optional_config_keys_not_required(self):
        """Nodes without required config (e.g., filters with defaults) pass."""
        from src.application.workflows.prefect import validate_workflow_def

        validate_workflow_def({
            "tasks": [
                {
                    "id": "src_1",
                    "type": "source.liked_tracks",
                },
                {
                    "id": "filter_1",
                    "type": "filter.deduplicate",
                    "upstream": ["src_1"],
                },
            ]
        })


class TestExtractWorkflowResult:
    """Tests for extract_workflow_result with typed task results."""

    def test_extracts_destination_tracklist(self, sample_tracklist):
        """Destination task's tracklist becomes the final result tracks."""
        from src.application.workflows.prefect import extract_workflow_result

        workflow_def = {
            "name": "test_workflow",
            "tasks": [
                {"id": "dest_1", "type": "destination.playlist", "upstream": ["src_1"]},
                {"id": "src_1", "type": "source.playlist"},
            ],
        }

        task_results = {
            "src_1": {"tracklist": sample_tracklist},
            "dest_1": {"tracklist": sample_tracklist},
        }

        result = extract_workflow_result(workflow_def, task_results, "test-run", 1.0)

        assert result.tracks == sample_tracklist.tracks
        assert result.operation_name == "test_workflow"

    def test_extracts_metrics_from_task_results(self, sample_tracklist):
        """Metrics should be extracted from task results that have tracklist entries."""
        from src.application.workflows.prefect import extract_workflow_result

        tracklist_with_metrics = TrackList(
            tracks=sample_tracklist.tracks,
            metadata={"metrics": {"lastfm_plays": {1: 42, 2: 10}}},
        )

        workflow_def = {
            "name": "metrics_test",
            "tasks": [
                {"id": "enricher_1", "type": "enricher.lastfm", "upstream": ["src_1"]},
                {
                    "id": "dest_1",
                    "type": "destination.playlist",
                    "upstream": ["enricher_1"],
                },
                {"id": "src_1", "type": "source.playlist"},
            ],
        }

        task_results = {
            "src_1": {"tracklist": sample_tracklist},
            "enricher_1": {"tracklist": tracklist_with_metrics},
            "dest_1": {"tracklist": tracklist_with_metrics},
        }

        result = extract_workflow_result(workflow_def, task_results, "test-run", 1.0)

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


class TestTopologicalSort:
    """Tests for topological_sort with cycle detection."""

    def test_cycle_detection_raises(self):
        """Mutual dependency A→B→A raises ValueError."""
        from src.application.workflows.prefect import topological_sort

        tasks = [
            {"id": "A", "upstream": ["B"]},
            {"id": "B", "upstream": ["A"]},
        ]
        with pytest.raises(ValueError, match="Cycle detected"):
            topological_sort(tasks)

    def test_self_referencing_task_raises(self):
        """Self-referencing task A→A raises ValueError."""
        from src.application.workflows.prefect import topological_sort

        tasks = [{"id": "A", "upstream": ["A"]}]
        with pytest.raises(ValueError, match="Cycle detected"):
            topological_sort(tasks)

    def test_valid_dag_sorts_correctly(self):
        """Linear chain C→B→A produces correct order [A, B, C]."""
        from src.application.workflows.prefect import topological_sort

        tasks = [
            {"id": "C", "upstream": ["B"]},
            {"id": "B", "upstream": ["A"]},
            {"id": "A", "upstream": []},
        ]
        result = topological_sort(tasks)
        ids = [t["id"] for t in result]
        # A must come before B, B must come before C
        assert ids.index("A") < ids.index("B") < ids.index("C")

    def test_no_upstream_tasks(self):
        """Tasks with no dependencies are sorted without error."""
        from src.application.workflows.prefect import topological_sort

        tasks = [
            {"id": "A"},
            {"id": "B"},
        ]
        result = topological_sort(tasks)
        assert len(result) == 2

    def test_dangling_upstream_reference(self):
        """Upstream reference to nonexistent task is handled gracefully."""
        from src.application.workflows.prefect import topological_sort

        tasks = [{"id": "A", "upstream": ["nonexistent"]}]
        # graph.get("nonexistent", []) returns [] — no crash
        result = topological_sort(tasks)
        assert len(result) == 1
