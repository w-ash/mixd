"""Characterization tests for NodeContext.

Locks down extract_tracklist, collect_tracklists, extract_workflow_context,
extract_use_cases, and get_connector behavior.
"""

from unittest.mock import MagicMock

import pytest

from src.application.workflows.node_context import NodeContext
from src.domain.entities.track import Artist, Track, TrackList


class TestExtractTracklist:
    """Tests for NodeContext.extract_tracklist."""

    def test_from_upstream_task(self, sample_tracklist):
        """Extract tracklist from upstream task result."""
        context = {
            "upstream_task_id": "src_1",
            "src_1": {"tracklist": sample_tracklist},
        }
        ctx = NodeContext(context)
        result = ctx.extract_tracklist()
        assert result is sample_tracklist

    def test_from_direct_context(self, sample_tracklist):
        """Extract tracklist from direct context (testing mode)."""
        context = {"tracklist": sample_tracklist}
        ctx = NodeContext(context)
        result = ctx.extract_tracklist()
        assert result is sample_tracklist

    def test_missing_raises(self):
        """Missing tracklist raises ValueError."""
        ctx = NodeContext({})
        with pytest.raises(ValueError, match="Missing required tracklist"):
            ctx.extract_tracklist()

    def test_upstream_takes_priority(self, sample_tracklist):
        """When both upstream and direct tracklist exist, upstream wins."""
        other_tl = TrackList(tracks=[])
        context = {
            "upstream_task_id": "src_1",
            "src_1": {"tracklist": sample_tracklist},
            "tracklist": other_tl,
        }
        ctx = NodeContext(context)
        result = ctx.extract_tracklist()
        assert result is sample_tracklist

    def test_upstream_missing_result_falls_through(self, sample_tracklist):
        """If upstream ID present but no result, falls through to direct."""
        context = {
            "upstream_task_id": "missing_task",
            "tracklist": sample_tracklist,
        }
        ctx = NodeContext(context)
        result = ctx.extract_tracklist()
        assert result is sample_tracklist


class TestCollectTracklists:
    """Tests for NodeContext.collect_tracklists."""

    def test_collects_from_multiple_tasks(self, sample_tracklist):
        """Collect tracklists from multiple task IDs."""
        tl2 = TrackList(
            tracks=[Track(id=3, title="Song C", artists=[Artist(name="A3")])]
        )
        context = {
            "task_a": {"tracklist": sample_tracklist},
            "task_b": {"tracklist": tl2},
        }
        ctx = NodeContext(context)
        result = ctx.collect_tracklists(["task_a", "task_b"])

        assert len(result) == 2
        assert result[0] is sample_tracklist
        assert result[1] is tl2

    def test_skips_missing_tasks(self, sample_tracklist):
        """Missing task IDs are skipped with warning."""
        context = {"task_a": {"tracklist": sample_tracklist}}
        ctx = NodeContext(context)
        result = ctx.collect_tracklists(["task_a", "missing_task"])

        assert len(result) == 1

    def test_skips_dict_without_tracklist_key(self, sample_tracklist):
        """Dict results missing 'tracklist' key are skipped with warning."""
        context = {
            "task_a": {"tracklist": sample_tracklist},
            "task_b": {"some_other_key": "value"},
        }
        ctx = NodeContext(context)
        result = ctx.collect_tracklists(["task_a", "task_b"])

        assert len(result) == 1
        assert result[0] is sample_tracklist

    def test_all_dicts_missing_tracklist_key_raises(self):
        """All dicts missing 'tracklist' key raises ValueError."""
        context = {
            "task_a": {"other": "data"},
            "task_b": {"also_not_tracklist": 42},
        }
        ctx = NodeContext(context)
        with pytest.raises(ValueError, match="No valid tracklists"):
            ctx.collect_tracklists(["task_a", "task_b"])

    def test_no_valid_tracklists_raises(self):
        """No valid tracklists raises ValueError."""
        ctx = NodeContext({})
        with pytest.raises(ValueError, match="No valid tracklists"):
            ctx.collect_tracklists(["missing_a", "missing_b"])


class TestExtractWorkflowContext:
    """Tests for NodeContext.extract_workflow_context."""

    def test_extracts_workflow_context(self):
        """Returns workflow context from data."""
        wf_ctx = MagicMock()
        ctx = NodeContext({"workflow_context": wf_ctx})
        assert ctx.extract_workflow_context() is wf_ctx

    def test_missing_raises(self):
        """Missing workflow context raises ValueError."""
        ctx = NodeContext({})
        with pytest.raises(ValueError, match="Workflow context not found"):
            ctx.extract_workflow_context()


class TestExtractUseCases:
    """Tests for NodeContext.extract_use_cases."""

    def test_extracts_use_cases(self):
        """Returns use case provider via workflow context."""
        uc = MagicMock()
        wf_ctx = MagicMock()
        wf_ctx.use_cases = uc
        ctx = NodeContext({"workflow_context": wf_ctx})
        assert ctx.extract_use_cases() is uc

    def test_missing_raises(self):
        """Missing workflow context raises ValueError."""
        ctx = NodeContext({})
        with pytest.raises(ValueError, match="Workflow context not found"):
            ctx.extract_use_cases()


class TestGetConnector:
    """Tests for NodeContext.get_connector."""

    def test_returns_connector(self):
        """Returns connector instance via workflow context's registry."""
        mock_connector = MagicMock()
        mock_registry = MagicMock()
        mock_registry.list_connectors.return_value = ["spotify", "lastfm"]
        mock_registry.get_connector.return_value = mock_connector

        wf_ctx = MagicMock()
        wf_ctx.connectors = mock_registry
        ctx = NodeContext({"workflow_context": wf_ctx})
        result = ctx.get_connector("spotify")

        assert result is mock_connector

    def test_missing_workflow_context_raises(self):
        """Missing workflow context raises ValueError."""
        ctx = NodeContext({})
        with pytest.raises(ValueError, match="Workflow context not found"):
            ctx.get_connector("spotify")

    def test_unsupported_connector_raises(self):
        """Unsupported connector name raises ValueError."""
        mock_registry = MagicMock()
        mock_registry.list_connectors.return_value = ["spotify"]

        wf_ctx = MagicMock()
        wf_ctx.connectors = mock_registry
        ctx = NodeContext({"workflow_context": wf_ctx})
        with pytest.raises(ValueError, match="Unsupported connector: lastfm"):
            ctx.get_connector("lastfm")
