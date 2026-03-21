"""Tests for workflow validation and topological sort.

Tests structural validation (required config, upstream references, node types)
and DAG ordering with cycle detection.
"""

import pytest

import src.application.workflows.node_catalog  # noqa: F401 — triggers node registration
from src.application.workflows.validation import (
    ConnectorNotAvailableError,
    _validate_node_config,
    compute_parallel_levels,
    extract_required_connectors,
    validate_connector_availability,
    validate_workflow_def,
)
from src.domain.entities.workflow import WorkflowDef, WorkflowTaskDef


class TestValidateWorkflowDef:
    """Tests for validate_workflow_def structural checks."""

    def test_empty_workflow_raises(self):
        """Workflow with no tasks raises ValueError."""
        with pytest.raises(ValueError, match="Workflow has no tasks"):
            validate_workflow_def(WorkflowDef(id="empty", name="empty"))

    def test_duplicate_task_ids_raises(self):
        """Duplicate task IDs raise ValueError with clear message."""
        wf = WorkflowDef(
            id="test",
            name="test",
            tasks=[
                WorkflowTaskDef(id="dup", type="source.playlist", config={"playlist_id": "1"}),
                WorkflowTaskDef(id="dup", type="source.playlist", config={"playlist_id": "2"}),
            ],
        )
        with pytest.raises(ValueError, match="Duplicate task IDs"):
            validate_workflow_def(wf)

    def test_unknown_upstream_raises(self):
        """Upstream reference to nonexistent task raises ValueError."""
        wf = WorkflowDef(
            id="test",
            name="test",
            tasks=[
                WorkflowTaskDef(
                    id="src_1",
                    type="source.playlist",
                    config={"playlist_id": "test-123"},
                ),
                WorkflowTaskDef(
                    id="dest_1",
                    type="destination.create_playlist",
                    config={"name": "Test"},
                    upstream=["nonexistent"],
                ),
            ],
        )
        with pytest.raises(ValueError, match="unknown upstream 'nonexistent'"):
            validate_workflow_def(wf)

    def test_unknown_node_type_raises(self):
        """Unregistered node type raises ValueError."""
        wf = WorkflowDef(
            id="test",
            name="test",
            tasks=[WorkflowTaskDef(id="src_1", type="totally.fake")],
        )
        with pytest.raises(ValueError, match=r"unknown node type 'totally\.fake'"):
            validate_workflow_def(wf)

    def test_valid_workflow_passes(self):
        """Well-formed workflow definition passes validation."""
        validate_workflow_def(
            WorkflowDef(
                id="test",
                name="test",
                tasks=[
                    WorkflowTaskDef(
                        id="src_1",
                        type="source.playlist",
                        config={"playlist_id": "test-123"},
                    ),
                    WorkflowTaskDef(
                        id="dest_1",
                        type="destination.create_playlist",
                        config={"name": "Test Playlist"},
                        upstream=["src_1"],
                    ),
                ],
            )
        )

    def test_missing_required_config_raises(self):
        """Node with missing required config key raises ValueError."""
        wf = WorkflowDef(
            id="test",
            name="test",
            tasks=[WorkflowTaskDef(id="src_1", type="source.playlist", config={})],
        )
        with pytest.raises(ValueError, match=r"missing required config.*playlist_id"):
            validate_workflow_def(wf)

    def test_wrong_type_config_value_raises(self):
        """Config value with wrong type (int instead of str) raises ValueError."""
        wf = WorkflowDef(
            id="test",
            name="test",
            tasks=[
                WorkflowTaskDef(
                    id="src_1",
                    type="source.playlist",
                    config={"playlist_id": 123},
                )
            ],
        )
        with pytest.raises(ValueError, match=r"must be str.*got int"):
            validate_workflow_def(wf)

    def test_empty_string_config_value_raises(self):
        """Empty string for required string config key raises ValueError."""
        wf = WorkflowDef(
            id="test",
            name="test",
            tasks=[
                WorkflowTaskDef(
                    id="src_1",
                    type="source.playlist",
                    config={"playlist_id": "  "},
                )
            ],
        )
        with pytest.raises(ValueError, match="must not be empty"):
            validate_workflow_def(wf)

    def test_numeric_percentage_accepted(self):
        """Percentage config accepts both int and float values."""
        _validate_node_config(
            "selector.percentage", {"percentage": 50}, task_id="sel_1"
        )
        _validate_node_config(
            "selector.percentage", {"percentage": 33.3}, task_id="sel_1"
        )

    def test_string_as_number_config_raises(self):
        """String value for numeric config key raises ValueError."""
        with pytest.raises(ValueError, match=r"must be int \| float.*got str"):
            _validate_node_config(
                "selector.percentage", {"percentage": "50"}, task_id="sel_1"
            )

    def test_optional_config_keys_not_required(self):
        """Nodes without required config (e.g., filters with defaults) pass."""
        validate_workflow_def(
            WorkflowDef(
                id="test",
                name="test",
                tasks=[
                    WorkflowTaskDef(id="src_1", type="source.liked_tracks"),
                    WorkflowTaskDef(
                        id="filter_1",
                        type="filter.deduplicate",
                        upstream=["src_1"],
                    ),
                ],
            )
        )


class TestNodeExecutionRecord:
    """Tests for NodeExecutionRecord domain entity."""

    def test_frozen_immutability(self):
        """Frozen record raises on attribute mutation."""
        from src.domain.entities.workflow import NodeExecutionRecord

        record = NodeExecutionRecord(
            node_id="src_1",
            node_type="source.playlist",
            execution_order=1,
            status="completed",
            duration_ms=150,
            output_track_count=10,
        )
        with pytest.raises(AttributeError):
            record.status = "failed"  # type: ignore[misc]

    def test_defaults(self):
        """Default values for optional fields."""
        from src.domain.entities.workflow import NodeExecutionRecord

        record = NodeExecutionRecord(
            node_id="t1",
            node_type="filter.by_metric",
            execution_order=2,
            status="completed",
        )
        assert record.duration_ms == 0
        assert record.input_track_count is None
        assert record.output_track_count is None
        assert record.error_message is None


class TestWorkflowDefConstruction:
    """Tests for WorkflowDef and WorkflowTaskDef attrs entities."""

    def test_defaults(self):
        """Default values applied correctly."""
        wf = WorkflowDef(id="test", name="Test")
        assert wf.description == ""
        assert wf.version == "1.0"
        assert wf.tasks == []

    def test_task_defaults(self):
        """WorkflowTaskDef defaults for config, upstream, result_key."""
        task = WorkflowTaskDef(id="t1", type="source.playlist")
        assert task.config == {}
        assert task.upstream == []
        assert task.result_key is None

    def test_frozen_immutability(self):
        """Frozen entities raise on attribute mutation."""
        wf = WorkflowDef(id="test", name="Test")
        with pytest.raises(AttributeError):
            wf.name = "Changed"  # type: ignore[misc]

    def test_full_construction(self):
        """Full construction with all fields."""
        wf = WorkflowDef(
            id="my_wf",
            name="My Workflow",
            description="Does things",
            version="2.0",
            tasks=[
                WorkflowTaskDef(
                    id="src",
                    type="source.playlist",
                    config={"playlist_id": "abc"},
                    upstream=[],
                    result_key="source_result",
                ),
            ],
        )
        assert wf.id == "my_wf"
        assert len(wf.tasks) == 1
        assert wf.tasks[0].result_key == "source_result"


class TestExtractRequiredConnectors:
    """Tests for extract_required_connectors pre-flight check."""

    def test_explicit_connector_in_config(self):
        """Config connector field is extracted."""
        wf = WorkflowDef(
            id="test",
            name="test",
            tasks=[
                WorkflowTaskDef(
                    id="src_1",
                    type="source.playlist",
                    config={"playlist_id": "abc", "connector": "spotify"},
                ),
            ],
        )
        assert extract_required_connectors(wf) == {"spotify"}

    def test_implicit_enricher_connector(self):
        """Enricher type name implies connector requirement."""
        wf = WorkflowDef(
            id="test",
            name="test",
            tasks=[
                WorkflowTaskDef(id="e1", type="enricher.lastfm"),
                WorkflowTaskDef(id="e2", type="enricher.spotify"),
            ],
        )
        assert extract_required_connectors(wf) == {"lastfm", "spotify"}

    def test_no_connectors_needed(self):
        """Workflow with only DB-backed nodes needs no connectors."""
        wf = WorkflowDef(
            id="test",
            name="test",
            tasks=[
                WorkflowTaskDef(id="src_1", type="source.liked_tracks"),
                WorkflowTaskDef(id="f1", type="filter.deduplicate", upstream=["src_1"]),
            ],
        )
        assert extract_required_connectors(wf) == set()

    def test_enricher_spotify_liked_status_extracts_spotify(self):
        """enricher.spotify_liked_status requires 'spotify', not 'spotify_liked_status'."""
        wf = WorkflowDef(
            id="test",
            name="test",
            tasks=[
                WorkflowTaskDef(id="e1", type="enricher.spotify_liked_status"),
            ],
        )
        assert extract_required_connectors(wf) == {"spotify"}

    def test_enricher_play_history_needs_no_connector(self):
        """enricher.play_history is DB-only — no connector needed."""
        wf = WorkflowDef(
            id="test",
            name="test",
            tasks=[
                WorkflowTaskDef(id="e1", type="enricher.play_history"),
            ],
        )
        assert extract_required_connectors(wf) == set()

    def test_deduplicates_same_connector(self):
        """Multiple nodes using same connector produce single entry."""
        wf = WorkflowDef(
            id="test",
            name="test",
            tasks=[
                WorkflowTaskDef(
                    id="src_1",
                    type="source.playlist",
                    config={"playlist_id": "abc", "connector": "spotify"},
                ),
                WorkflowTaskDef(
                    id="dest_1",
                    type="destination.update_playlist",
                    config={"playlist_id": "xyz", "connector": "spotify"},
                    upstream=["src_1"],
                ),
            ],
        )
        assert extract_required_connectors(wf) == {"spotify"}


class TestValidateConnectorAvailability:
    """Tests for validate_connector_availability."""

    def test_all_present(self):
        """No missing connectors returns empty list."""
        assert (
            validate_connector_availability(
                {"spotify", "lastfm"}, ["spotify", "lastfm", "musicbrainz"]
            )
            == []
        )

    def test_missing_connectors(self):
        """Missing connectors returned sorted."""
        result = validate_connector_availability(
            {"spotify", "apple_music"}, ["spotify", "lastfm"]
        )
        assert result == ["apple_music"]

    def test_empty_required(self):
        """No requirements always passes."""
        assert validate_connector_availability(set(), ["spotify"]) == []


class TestComputeParallelLevels:
    """Tests for compute_parallel_levels BFS level grouping."""

    def test_linear_chain_produces_single_task_levels(self):
        """A→B→C produces 3 levels of 1 task each."""
        tasks = [
            WorkflowTaskDef(id="A", type="x"),
            WorkflowTaskDef(id="B", type="x", upstream=["A"]),
            WorkflowTaskDef(id="C", type="x", upstream=["B"]),
        ]
        levels = compute_parallel_levels(tasks)
        level_ids = [[t.id for t in level] for level in levels]
        assert level_ids == [["A"], ["B"], ["C"]]

    def test_independent_sources_grouped_in_one_level(self):
        """Two independent sources + a combiner produces [[A,B], [C]]."""
        tasks = [
            WorkflowTaskDef(id="A", type="x"),
            WorkflowTaskDef(id="B", type="x"),
            WorkflowTaskDef(id="C", type="x", upstream=["A", "B"]),
        ]
        levels = compute_parallel_levels(tasks)
        assert len(levels) == 2
        assert sorted(t.id for t in levels[0]) == ["A", "B"]
        assert [t.id for t in levels[1]] == ["C"]

    def test_diamond_dag(self):
        """Diamond: A→(B,C)→D produces [[A], [B,C], [D]]."""
        tasks = [
            WorkflowTaskDef(id="A", type="x"),
            WorkflowTaskDef(id="B", type="x", upstream=["A"]),
            WorkflowTaskDef(id="C", type="x", upstream=["A"]),
            WorkflowTaskDef(id="D", type="x", upstream=["B", "C"]),
        ]
        levels = compute_parallel_levels(tasks)
        assert len(levels) == 3
        assert [t.id for t in levels[0]] == ["A"]
        assert sorted(t.id for t in levels[1]) == ["B", "C"]
        assert [t.id for t in levels[2]] == ["D"]

    def test_cycle_detection_raises(self):
        """Mutual dependency A→B→A raises ValueError."""
        tasks = [
            WorkflowTaskDef(id="A", type="x", upstream=["B"]),
            WorkflowTaskDef(id="B", type="x", upstream=["A"]),
        ]
        with pytest.raises(ValueError, match="Cycle detected"):
            compute_parallel_levels(tasks)

    def test_single_task(self):
        """Single task produces one level with one task."""
        tasks = [WorkflowTaskDef(id="A", type="x")]
        levels = compute_parallel_levels(tasks)
        assert len(levels) == 1
        assert [t.id for t in levels[0]] == ["A"]

    def test_all_independent_tasks(self):
        """All independent tasks are in a single level."""
        tasks = [
            WorkflowTaskDef(id="A", type="x"),
            WorkflowTaskDef(id="B", type="x"),
            WorkflowTaskDef(id="C", type="x"),
        ]
        levels = compute_parallel_levels(tasks)
        assert len(levels) == 1
        assert sorted(t.id for t in levels[0]) == ["A", "B", "C"]

    def test_preserves_all_tasks(self):
        """All tasks appear exactly once across all levels."""
        tasks = [
            WorkflowTaskDef(id="src1", type="x"),
            WorkflowTaskDef(id="src2", type="x"),
            WorkflowTaskDef(id="enrich1", type="x", upstream=["src1"]),
            WorkflowTaskDef(id="enrich2", type="x", upstream=["src2"]),
            WorkflowTaskDef(id="combine", type="x", upstream=["enrich1", "enrich2"]),
        ]
        levels = compute_parallel_levels(tasks)
        all_ids = [t.id for level in levels for t in level]
        assert sorted(all_ids) == sorted(t.id for t in tasks)


class TestConnectorNotAvailableError:
    """Tests for ConnectorNotAvailableError."""

    def test_carries_missing_list(self):
        """Error provides missing connector names."""
        err = ConnectorNotAvailableError(["apple_music", "tidal"])
        assert err.missing_connectors == ["apple_music", "tidal"]
        assert "apple_music" in str(err)
        assert "tidal" in str(err)
