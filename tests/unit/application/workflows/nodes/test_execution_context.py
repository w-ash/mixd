"""Characterization tests for NodeContext.

Locks down extract_tracklist, collect_tracklists, extract_workflow_context,
and get_connector behavior.
"""

from unittest.mock import MagicMock

import pytest

from src.application.connector_protocols import (
    LibraryContainsConnector,
    TrackMetadataConnector,
)
from src.application.workflows.nodes.execution_context import NodeContext
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
        tl2 = TrackList(tracks=[Track(title="Song C", artists=[Artist(name="A3")])])
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


def _make_ctx(
    registry: MagicMock, *, capabilities: frozenset[str] = frozenset()
) -> NodeContext:
    """Wrap a mock registry (with a descriptor stub) in a NodeContext."""
    registry.describe.return_value = MagicMock(capabilities=capabilities)
    registry.list_connectors.return_value = ["spotify", "lastfm"]
    wf_ctx = MagicMock()
    wf_ctx.connectors = registry
    return NodeContext({"workflow_context": wf_ctx})


class TestGetConnector:
    """Tests for NodeContext.get_connector — capability and protocol gating."""

    def test_returns_connector(self):
        """Declared capability + implemented protocol returns the instance."""
        mock_connector = MagicMock(spec=LibraryContainsConnector)
        mock_registry = MagicMock()
        mock_registry.list_connectors.return_value = ["spotify", "lastfm"]
        mock_registry.get_connector.return_value = mock_connector

        ctx = _make_ctx(mock_registry, capabilities=frozenset({"library_contains"}))
        result = ctx.get_connector(
            "spotify", capability="library_contains", protocol=LibraryContainsConnector
        )

        assert result is mock_connector
        mock_registry.describe.assert_called_once_with("spotify")

    def test_missing_workflow_context_raises(self):
        """Missing workflow context raises ValueError."""
        ctx = NodeContext({})
        with pytest.raises(ValueError, match="Workflow context not found"):
            ctx.get_connector(
                "spotify",
                capability="library_contains",
                protocol=LibraryContainsConnector,
            )

    def test_unsupported_connector_raises(self):
        """Unregistered connector name raises ValueError with the available list."""
        mock_registry = MagicMock()
        mock_registry.list_connectors.return_value = ["spotify"]
        mock_registry.describe.side_effect = ValueError("not registered")

        wf_ctx = MagicMock()
        wf_ctx.connectors = mock_registry
        ctx = NodeContext({"workflow_context": wf_ctx})
        with pytest.raises(
            ValueError,
            match=r"Unsupported connector: lastfm\. Available: \['spotify'\]",
        ):
            ctx.get_connector(
                "lastfm", capability="track_enrichment", protocol=TrackMetadataConnector
            )
        mock_registry.get_connector.assert_not_called()

    def test_undeclared_capability_raises_value_error(self):
        """A registered connector without the capability is a caller error."""
        mock_registry = MagicMock()
        ctx = _make_ctx(mock_registry, capabilities=frozenset({"track_enrichment"}))

        with pytest.raises(
            ValueError, match="Connector 'lastfm' does not declare 'library_contains'"
        ):
            ctx.get_connector(
                "lastfm",
                capability="library_contains",
                protocol=LibraryContainsConnector,
            )
        mock_registry.get_connector.assert_not_called()

    def test_registered_connector_failure_is_not_rewrapped(self):
        """The 'Unsupported connector' wrapping applies only to unknown names."""
        mock_registry = MagicMock()
        ctx = _make_ctx(mock_registry, capabilities=frozenset())

        with pytest.raises(ValueError) as exc_info:
            ctx.get_connector(
                "spotify",
                capability="library_contains",
                protocol=LibraryContainsConnector,
            )
        assert "Unsupported connector" not in str(exc_info.value)

    def test_protocol_mismatch_raises_type_error(self):
        """A declared capability whose class lacks the protocol is an adapter bug."""
        mock_registry = MagicMock()
        mock_registry.get_connector.return_value = object()
        ctx = _make_ctx(mock_registry, capabilities=frozenset({"library_contains"}))

        with pytest.raises(
            TypeError,
            match="declares 'library_contains' but does not implement "
            "LibraryContainsConnector",
        ):
            ctx.get_connector(
                "spotify",
                capability="library_contains",
                protocol=LibraryContainsConnector,
            )
