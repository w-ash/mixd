"""Tests for workflow validation and topological sort.

Tests structural validation (required config, upstream references, node types)
and DAG ordering with cycle detection.
"""

import pytest

from src.application.workflows.definition.validation import (
    ConnectorNotAvailableError,
    _check_node_config,
    _enricher_emitted_metrics,
    compute_parallel_levels,
    extract_required_connectors,
    validate_connector_availability,
    validate_workflow_def,
    validate_workflow_def_detailed,
)
import src.application.workflows.nodes.catalog as _catalog
from src.application.workflows.nodes.config_fields import (
    DEFAULT_PLAY_HISTORY_METRICS,
)
from src.domain.entities.workflow import WorkflowDef, WorkflowTaskDef

# Importing the node catalog above triggers @node() registration as a side effect.
# This reference keeps F401 from flagging the import as unused under ruff
# configurations that autofix noqa comments.
_CATALOG_MODULE = _catalog.__name__


def _def(tasks: list[WorkflowTaskDef]) -> WorkflowDef:
    return WorkflowDef(id="test", name="Test", tasks=tasks)


def _errors_for(field: str, errors: list[dict[str, str]]) -> list[dict[str, str]]:
    return [e for e in errors if e["field"] == field]


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
                WorkflowTaskDef(
                    id="dup", type="source.playlist", config={"playlist_id": "1"}
                ),
                WorkflowTaskDef(
                    id="dup", type="source.playlist", config={"playlist_id": "2"}
                ),
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
        assert _check_node_config(
            "selector.percentage", {"percentage": 50}, "sel_1"
        ) == (None, [])
        assert _check_node_config(
            "selector.percentage", {"percentage": 33.3}, "sel_1"
        ) == (None, [])

    def test_string_as_number_config_raises(self):
        """String value for numeric config key is reported."""
        message, warnings = _check_node_config(
            "selector.percentage", {"percentage": "50"}, "sel_1"
        )
        assert message is not None
        assert "must be int | float" in message
        assert "got str" in message
        assert warnings == []

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


class TestPrimaryInputValidation:
    """primary_input must name an actual upstream, else the executor silently
    falls back to upstream[0] and produces plausible-but-wrong output."""

    def _multi_upstream(self, primary: str) -> WorkflowDef:
        return _def([
            WorkflowTaskDef(id="a", type="source.liked_tracks"),
            WorkflowTaskDef(id="b", type="source.liked_tracks"),
            WorkflowTaskDef(
                id="merge",
                type="combiner.merge_playlists",
                config={"primary_input": primary},
                upstream=["a", "b"],
            ),
        ])

    def test_blocking_rejects_primary_input_not_in_upstream(self):
        with pytest.raises(ValueError, match="primary_input 'ghost'"):
            validate_workflow_def(self._multi_upstream("ghost"))

    def test_detailed_reports_primary_input_not_in_upstream(self):
        errors = validate_workflow_def_detailed(self._multi_upstream("ghost"))
        hits = _errors_for("config.primary_input", errors)
        assert len(hits) == 1
        assert hits[0]["task_id"] == "merge"

    def test_primary_input_in_upstream_passes(self):
        validate_workflow_def(self._multi_upstream("a"))  # no raise
        assert validate_workflow_def_detailed(self._multi_upstream("a")) == []


class TestTaskRefValidation:
    """Every task_ref field (not only primary_input) must name a real upstream."""

    def _exclusion(self, exclusion_source: str) -> WorkflowDef:
        return _def([
            WorkflowTaskDef(id="candidates", type="source.liked_tracks"),
            WorkflowTaskDef(id="already_heard", type="source.liked_tracks"),
            WorkflowTaskDef(
                id="exclude",
                type="filter.by_tracks",
                config={"exclusion_source": exclusion_source},
                upstream=["candidates", "already_heard"],
            ),
        ])

    def test_exclusion_source_not_in_upstream_is_an_error(self):
        items = validate_workflow_def_detailed(self._exclusion("ghost"))
        hits = _errors_for("config.exclusion_source", items)
        assert len(hits) == 1
        assert hits[0]["task_id"] == "exclude"
        assert "severity" not in hits[0]
        assert "not one of its upstream tasks" in hits[0]["message"]
        with pytest.raises(ValueError, match="exclusion_source 'ghost'"):
            validate_workflow_def(self._exclusion("ghost"))

    def test_exclusion_source_in_upstream_passes(self):
        assert validate_workflow_def_detailed(self._exclusion("already_heard")) == []


class TestConstraintWarnings:
    """options/min/max are advisory: they warn but never block save or execute."""

    def _selector(self, node_type: str, config: dict[str, object]) -> WorkflowDef:
        return _def([
            WorkflowTaskDef(id="src", type="source.liked_tracks"),
            WorkflowTaskDef(id="sel", type=node_type, config=config, upstream=["src"]),
        ])

    def _warnings_for(self, field: str, wf: WorkflowDef) -> list[dict[str, str]]:
        return [
            item
            for item in _errors_for(field, validate_workflow_def_detailed(wf))
            if item.get("severity") == "warning"
        ]

    def test_select_value_outside_options_warns(self):
        wf = self._selector("selector.limit_tracks", {"count": 5, "method": "middle"})
        hits = self._warnings_for("config.method", wf)
        assert len(hits) == 1
        assert "should be one of" in hits[0]["message"]
        validate_workflow_def(wf)  # warning only — still saves

    def test_number_outside_range_warns(self):
        wf = self._selector("selector.percentage", {"percentage": 500})
        hits = self._warnings_for("config.percentage", wf)
        assert len(hits) == 1
        assert "should be between 1 and 100" in hits[0]["message"]
        validate_workflow_def(wf)

    def test_multi_select_bad_element_warns(self):
        wf = self._selector(
            "enricher.play_history", {"metrics": ["total_plays", "bogus"]}
        )
        hits = self._warnings_for("config.metrics", wf)
        assert len(hits) == 1
        assert "['bogus']" in hits[0]["message"]
        validate_workflow_def(wf)

    def test_multi_select_wrong_type_is_an_error_when_required(self):
        # multi_select maps to list; a bare string on a required field is an error.
        message, _ = _check_node_config(
            "filter.by_tracks", {"exclusion_source": ["a"]}, "t"
        )
        assert message is not None
        assert "must be str" in message

    def test_in_range_values_are_clean(self):
        wf = self._selector("selector.limit_tracks", {"count": 5, "method": "last"})
        assert validate_workflow_def_detailed(wf) == []

    def test_boolean_config_values_are_not_flagged(self):
        """Seeds write JSON booleans for reverse/ascending/is_liked."""
        wf = _def([
            WorkflowTaskDef(id="src", type="source.liked_tracks"),
            WorkflowTaskDef(
                id="liked",
                type="filter.by_liked_status",
                config={"service": "spotify", "is_liked": False},
                upstream=["src"],
            ),
            WorkflowTaskDef(
                id="by_added",
                type="sorter.by_added_at",
                config={"ascending": False},
                upstream=["liked"],
            ),
            WorkflowTaskDef(
                id="by_release",
                type="sorter.by_release_date",
                config={"reverse": True},
                upstream=["by_added"],
            ),
        ])
        assert validate_workflow_def_detailed(wf) == []


class TestUnsetValues:
    """null (and [] on a multi_select) is absent at save time as at run time."""

    def _chain(self, node_type: str, config: dict[str, object]) -> WorkflowDef:
        return _def([
            WorkflowTaskDef(id="src", type="source.liked_tracks"),
            WorkflowTaskDef(id="t", type=node_type, config=config, upstream=["src"]),
        ])

    def test_null_optional_values_validate_clean(self):
        """The executor fills these from the declared defaults, so no issue."""
        wf = self._chain("selector.limit_tracks", {"count": None, "method": None})
        assert validate_workflow_def_detailed(wf) == []
        validate_workflow_def(wf)

    def test_null_required_value_is_missing_not_a_type_error(self):
        message, warnings = _check_node_config(
            "source.playlist", {"playlist_id": None}, "src_1"
        )
        assert message is not None
        assert "missing required config: ['playlist_id']" in message
        assert warnings == []

    def test_empty_metrics_list_validates_clean(self):
        wf = self._chain("enricher.play_history", {"metrics": []})
        assert validate_workflow_def_detailed(wf) == []
        validate_workflow_def(wf)

    def test_empty_metrics_list_emits_the_declared_defaults(self):
        """The validator's emitted-metrics view matches what the node will run with."""
        for config in ({}, {"metrics": []}, {"metrics": None}):
            task = WorkflowTaskDef(id="e", type="enricher.play_history", config=config)
            assert _enricher_emitted_metrics(task) == set(DEFAULT_PLAY_HISTORY_METRICS)
        explicit = WorkflowTaskDef(
            id="e", type="enricher.play_history", config={"metrics": ["total_plays"]}
        )
        assert _enricher_emitted_metrics(explicit) == {"total_plays"}


class TestOptionalTypeWarnings:
    """A wrong-typed optional value is ignored at run time, so it warns, not blocks."""

    def _chain(self, node_type: str, config: dict[str, object]) -> WorkflowDef:
        return _def([
            WorkflowTaskDef(id="src", type="source.liked_tracks"),
            WorkflowTaskDef(id="t", type=node_type, config=config, upstream=["src"]),
        ])

    def _warnings_for(self, field: str, wf: WorkflowDef) -> list[dict[str, str]]:
        return [
            item
            for item in _errors_for(field, validate_workflow_def_detailed(wf))
            if item.get("severity") == "warning"
        ]

    def test_string_for_boolean_warns(self):
        wf = self._chain(
            "sorter.by_metric", {"metric_name": "total_plays", "reverse": "yes"}
        )
        hits = self._warnings_for("config.reverse", wf)
        assert len(hits) == 1
        assert "must be bool, got str: 'yes'" in hits[0]["message"]
        assert "ignored" in hits[0]["message"]
        validate_workflow_def(wf)  # warning only — still saves and runs

    def test_string_for_number_warns(self):
        wf = self._chain(
            "enricher.play_history",
            {"metrics": ["period_plays"], "period_days": "30"},
        )
        hits = self._warnings_for("config.period_days", wf)
        assert len(hits) == 1
        assert "must be int | float" in hits[0]["message"]
        validate_workflow_def(wf)

    def test_wrong_type_skips_the_constraint_check(self):
        """A constraint cannot be judged on a mistyped value: one warning, not two."""
        message, warnings = _check_node_config(
            "selector.limit_tracks", {"count": "5"}, "t"
        )
        assert message is None
        assert len(warnings) == 1
        assert warnings[0][0] == "count"

    def test_required_wrong_type_still_blocks(self):
        message, _ = _check_node_config("source.playlist", {"playlist_id": 123}, "t")
        assert message is not None
        assert "must be str" in message

    def test_range_warning_renders_bounds_without_exponent(self):
        wf = _def([
            WorkflowTaskDef(
                id="src",
                type="source.preferred_tracks",
                config={"state": "star", "limit": 5_000_000},
            )
        ])
        hits = self._warnings_for("config.limit", wf)
        assert len(hits) == 1
        assert "between 1 and 1000000" in hits[0]["message"]
        assert "e+06" not in hits[0]["message"]


class TestResultKeyValidation:
    """result_key is stored as an alias beside task ids; a key equal to another
    task's id (or duplicated across tasks) silently overwrites a result."""

    def test_blocking_rejects_result_key_colliding_with_task_id(self):
        wf = _def([
            WorkflowTaskDef(id="a", type="source.liked_tracks"),
            WorkflowTaskDef(
                id="b",
                type="filter.deduplicate",
                upstream=["a"],
                result_key="a",  # collides with task "a"
            ),
        ])
        with pytest.raises(ValueError, match="collides with the id"):
            validate_workflow_def(wf)

    def test_blocking_rejects_duplicate_result_keys(self):
        wf = _def([
            WorkflowTaskDef(id="a", type="source.liked_tracks", result_key="shared"),
            WorkflowTaskDef(id="b", type="source.liked_tracks", result_key="shared"),
        ])
        with pytest.raises(ValueError, match="duplicates the one on task 'a'"):
            validate_workflow_def(wf)

    def test_detailed_attributes_collision_to_offending_task(self):
        wf = _def([
            WorkflowTaskDef(id="a", type="source.liked_tracks"),
            WorkflowTaskDef(
                id="b", type="filter.deduplicate", upstream=["a"], result_key="a"
            ),
        ])
        hits = _errors_for("result_key", validate_workflow_def_detailed(wf))
        assert len(hits) == 1
        assert hits[0]["task_id"] == "b"

    def test_result_key_matching_own_id_is_allowed(self):
        # Storing under one's own id is a harmless self-overwrite, not a collision.
        wf = _def([
            WorkflowTaskDef(id="a", type="source.liked_tracks", result_key="a"),
        ])
        validate_workflow_def(wf)  # no raise

    def test_distinct_result_key_passes(self):
        wf = _def([
            WorkflowTaskDef(id="a", type="source.liked_tracks", result_key="liked"),
        ])
        validate_workflow_def(wf)
        assert validate_workflow_def_detailed(wf) == []


class TestSourcePlacementValidation:
    """Only source nodes may sit at level 0 (no upstream). A non-source there
    passes the topological sort but empties or errors at runtime."""

    def test_blocking_rejects_non_source_without_upstream(self):
        wf = _def([
            WorkflowTaskDef(id="dedup", type="filter.deduplicate"),  # no upstream
        ])
        with pytest.raises(ValueError, match="only source nodes may run without input"):
            validate_workflow_def(wf)

    def test_detailed_reports_non_source_without_upstream(self):
        wf = _def([WorkflowTaskDef(id="dedup", type="filter.deduplicate")])
        hits = _errors_for("upstream", validate_workflow_def_detailed(wf))
        assert any(h["task_id"] == "dedup" for h in hits)

    def test_source_without_upstream_passes(self):
        wf = _def([WorkflowTaskDef(id="src", type="source.liked_tracks")])
        validate_workflow_def(wf)
        assert validate_workflow_def_detailed(wf) == []


class TestValidatorParity:
    """The save-guard and the editor share one rule set: both must reject
    duplicate ids AND cycles. Before unification each caught only one — a
    cyclic workflow could be saved, a duplicate-id workflow passed the editor."""

    def _cycle(self) -> WorkflowDef:
        # A→B→A with real, config-free nodes so the cycle is the only problem.
        return _def([
            WorkflowTaskDef(id="a", type="filter.deduplicate", upstream=["b"]),
            WorkflowTaskDef(id="b", type="filter.deduplicate", upstream=["a"]),
        ])

    def _duplicate_ids(self) -> WorkflowDef:
        return _def([
            WorkflowTaskDef(id="dup", type="source.liked_tracks"),
            WorkflowTaskDef(id="dup", type="source.liked_tracks"),
        ])

    def test_blocking_rejects_cycle(self):
        # Regression: a cyclic workflow used to pass the save-guard and only
        # blow up at execution time.
        with pytest.raises(ValueError, match="Cycle detected"):
            validate_workflow_def(self._cycle())

    def test_detailed_reports_cycle(self):
        hits = _errors_for("tasks", validate_workflow_def_detailed(self._cycle()))
        assert any("Cycle detected" in h["message"] for h in hits)

    def test_detailed_reports_duplicate_task_ids(self):
        # Regression: a duplicate-id workflow used to pass the editor's validate.
        hits = _errors_for(
            "tasks", validate_workflow_def_detailed(self._duplicate_ids())
        )
        assert any("Duplicate task IDs" in h["message"] for h in hits)

    def test_blocking_rejects_duplicate_ids(self):
        with pytest.raises(ValueError, match="Duplicate task IDs"):
            validate_workflow_def(self._duplicate_ids())

    def test_duplicate_ids_suppress_misleading_cycle_message(self):
        # With duplicate ids the DAG is ambiguous; we don't also emit a cycle
        # error that would confuse the editor.
        hits = _errors_for(
            "tasks", validate_workflow_def_detailed(self._duplicate_ids())
        )
        assert all("Cycle detected" not in h["message"] for h in hits)
