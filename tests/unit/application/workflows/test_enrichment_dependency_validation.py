"""Tests for static enrichment dependency validation.

Verifies that _validate_enrichment_dependencies correctly warns when
filter/sorter nodes reference metrics without a corresponding upstream enricher.
"""

from src.application.workflows.validation import _validate_enrichment_dependencies
from src.domain.entities.workflow import WorkflowDef, WorkflowTaskDef


def _make_def(tasks: list[WorkflowTaskDef]) -> WorkflowDef:
    return WorkflowDef(id="test", name="Test", tasks=tasks)


class TestValidateEnrichmentDependencies:
    def test_no_warnings_when_enricher_upstream(self):
        """Filter referencing lastfm_user_playcount with upstream enricher.lastfm — no warning."""
        tasks = [
            WorkflowTaskDef(id="src", type="source.liked_tracks", config={}),
            WorkflowTaskDef(
                id="enrich",
                type="enricher.lastfm",
                config={},
                upstream=["src"],
            ),
            WorkflowTaskDef(
                id="filter",
                type="filter.by_metric",
                config={"metric_name": "lastfm_user_playcount"},
                upstream=["enrich"],
            ),
        ]
        warnings = _validate_enrichment_dependencies(_make_def(tasks))
        assert warnings == []

    def test_warns_when_no_enricher_upstream(self):
        """Filter referencing metric with no upstream enricher — should warn."""
        tasks = [
            WorkflowTaskDef(id="src", type="source.liked_tracks", config={}),
            WorkflowTaskDef(
                id="filter",
                type="filter.by_metric",
                config={"metric_name": "lastfm_user_playcount"},
                upstream=["src"],
            ),
        ]
        warnings = _validate_enrichment_dependencies(_make_def(tasks))
        assert len(warnings) == 1
        assert warnings[0]["task_id"] == "filter"
        assert "lastfm_user_playcount" in warnings[0]["message"]
        assert "no upstream enricher" in warnings[0]["message"]

    def test_warns_when_wrong_enricher_upstream(self):
        """Spotify enricher upstream but filter references lastfm metric — should warn."""
        tasks = [
            WorkflowTaskDef(id="src", type="source.liked_tracks", config={}),
            WorkflowTaskDef(
                id="enrich",
                type="enricher.spotify",
                config={},
                upstream=["src"],
            ),
            WorkflowTaskDef(
                id="sort",
                type="sorter.by_metric",
                config={"metric_name": "lastfm_user_playcount"},
                upstream=["enrich"],
            ),
        ]
        warnings = _validate_enrichment_dependencies(_make_def(tasks))
        assert len(warnings) == 1
        assert "lastfm_user_playcount" in warnings[0]["message"]

    def test_no_warning_for_play_history_metrics(self):
        """play_history enricher provides total_plays — no warning for filter using it."""
        tasks = [
            WorkflowTaskDef(id="src", type="source.liked_tracks", config={}),
            WorkflowTaskDef(
                id="enrich",
                type="enricher.play_history",
                config={},
                upstream=["src"],
            ),
            WorkflowTaskDef(
                id="filter",
                type="filter.by_metric",
                config={"metric_name": "total_plays"},
                upstream=["enrich"],
            ),
        ]
        warnings = _validate_enrichment_dependencies(_make_def(tasks))
        assert warnings == []

    def test_no_warning_for_non_metric_nodes(self):
        """Non-metric filter nodes should not trigger warnings."""
        tasks = [
            WorkflowTaskDef(id="src", type="source.liked_tracks", config={}),
            WorkflowTaskDef(
                id="filter",
                type="filter.deduplicate",
                config={},
                upstream=["src"],
            ),
        ]
        warnings = _validate_enrichment_dependencies(_make_def(tasks))
        assert warnings == []

    def test_transitive_upstream_enricher_detected(self):
        """Enricher two levels up should still be detected."""
        tasks = [
            WorkflowTaskDef(id="src", type="source.liked_tracks", config={}),
            WorkflowTaskDef(
                id="enrich",
                type="enricher.lastfm",
                config={},
                upstream=["src"],
            ),
            WorkflowTaskDef(
                id="dedup",
                type="filter.deduplicate",
                config={},
                upstream=["enrich"],
            ),
            WorkflowTaskDef(
                id="sort",
                type="sorter.by_metric",
                config={"metric_name": "lastfm_user_playcount"},
                upstream=["dedup"],
            ),
        ]
        warnings = _validate_enrichment_dependencies(_make_def(tasks))
        assert warnings == []
