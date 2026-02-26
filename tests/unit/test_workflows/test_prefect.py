"""Tests for Prefect workflow execution.

Regression tests to ensure runtime guards are not removed during type cleanup.
"""

from unittest.mock import MagicMock

import pytest

from src.domain.entities import Artist
from src.domain.entities.track import Track, TrackList


@pytest.fixture
def sample_tracklist():
    """Create a sample tracklist for testing."""
    tracks = [
        Track(id=1, title="Track A", artists=[Artist(name="Artist 1")]),
        Track(id=2, title="Track B", artists=[Artist(name="Artist 2")]),
    ]
    return TrackList(tracks=tracks)


class TestExtractWorkflowResult:
    """Tests for extract_workflow_result handling mixed context dicts."""

    @pytest.mark.asyncio
    async def test_ignores_non_task_context_entries(self, sample_tracklist):
        """extract_workflow_result must tolerate non-NodeResult entries in context.

        The context dict passed at runtime contains infrastructure objects
        ("parameters", "use_cases", "connectors", etc.) alongside actual task
        results. The metrics loop must skip entries that aren't NodeResult dicts.

        Regression test: a previous type-cleanup agent removed the isinstance guard
        from the metrics loop, causing KeyError on non-NodeResult entries.
        """
        from src.application.workflows.prefect import extract_workflow_result

        workflow_def = {
            "name": "test_workflow",
            "tasks": [
                {"id": "dest_1", "type": "destination.playlist", "upstream": ["src_1"]},
                {"id": "src_1", "type": "source.playlist"},
            ],
        }

        # Context matches real workflow shape: mix of infrastructure and task results
        context = {
            "parameters": {"playlist_name": "test"},
            "use_cases": MagicMock(),
            "connectors": MagicMock(),
            "config": {"some": "config"},
            "logger": MagicMock(),
            "workflow_name": "test_workflow",
            "src_1": {"tracklist": sample_tracklist},
            "dest_1": {"tracklist": sample_tracklist},
        }

        # Must NOT raise KeyError — the core regression being tested
        result = await extract_workflow_result.fn(
            workflow_def, context, "test-run", 1.0
        )

        assert result.tracks == sample_tracklist.tracks
        assert result.operation_name == "test_workflow"

    @pytest.mark.asyncio
    async def test_extracts_metrics_from_task_results(self, sample_tracklist):
        """Metrics should be extracted from task results that have tracklist entries."""
        from src.application.workflows.prefect import extract_workflow_result

        # Tracklist with metrics in metadata
        tracklist_with_metrics = TrackList(
            tracks=sample_tracklist.tracks,
            metadata={"metrics": {"lastfm_plays": {1: 42, 2: 10}}},
        )

        workflow_def = {
            "name": "metrics_test",
            "tasks": [
                {"id": "enricher_1", "type": "enricher.lastfm", "upstream": ["src_1"]},
                {"id": "dest_1", "type": "destination.playlist", "upstream": ["enricher_1"]},
                {"id": "src_1", "type": "source.playlist"},
            ],
        }

        context = {
            "parameters": {},
            "src_1": {"tracklist": sample_tracklist},
            "enricher_1": {"tracklist": tracklist_with_metrics},
            "dest_1": {"tracklist": tracklist_with_metrics},
        }

        result = await extract_workflow_result.fn(
            workflow_def, context, "test-run", 1.0
        )

        assert "lastfm_plays" in result.metrics
        assert result.metrics["lastfm_plays"][1] == 42
