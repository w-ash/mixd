"""Test node factory functions with comprehensive coverage."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain.entities.track import Artist, Track, TrackList


@pytest.fixture
def sample_tracklist():
    """Create a sample tracklist for testing."""
    tracks = [
        Track(
            title="Test Song 1",
            artists=[Artist(name="Artist 1")],
            album="Test Album",
            duration_ms=180000,
        ),
        Track(
            title="Test Song 2",
            artists=[Artist(name="Artist 2")],
            album="Test Album 2",
            duration_ms=200000,
        ),
    ]
    return TrackList(tracks=tracks)


@pytest.mark.unit
class TestNodeFactories:
    """Test node factory functions."""

    def test_make_node_creates_functions(self):
        """Test make_node returns callable functions."""
        from src.application.workflows.node_factories import make_node

        # Test with actual categories and types from transform registry
        node_configs = [
            ("filter", "deduplicate"),
            ("sorter", "by_metric"),
            ("selector", "limit_tracks"),
        ]

        for category, node_type in node_configs:
            node_func = make_node(category, node_type)
            assert callable(node_func)

    def test_make_node_invalid_category(self):
        """Test make_node with invalid category raises error."""
        from src.application.workflows.node_factories import make_node

        with pytest.raises(ValueError, match="Unknown node category: invalid_category"):
            make_node("invalid_category", "some_type")

    def test_make_node_invalid_type(self):
        """Test make_node with invalid type in valid category raises error."""
        from src.application.workflows.node_factories import make_node

        with pytest.raises(
            ValueError, match="Unknown node type: invalid_type in category filter"
        ):
            make_node("filter", "invalid_type")


class TestDestinationNodeFactory:
    """Test destination node creation."""

    def test_create_destination_node_valid_types(self):
        """Test creating destination nodes for valid types."""
        from src.application.workflows.node_factories import create_destination_node

        valid_types = ["create_playlist", "update_playlist"]

        for dest_type in valid_types:
            node_func = create_destination_node(dest_type)
            assert callable(node_func)

    def test_create_destination_node_invalid_type(self):
        """Test creating destination node with invalid type."""
        from src.application.workflows.node_factories import create_destination_node

        with pytest.raises(
            ValueError, match="Unsupported destination type: invalid_destination"
        ):
            create_destination_node("invalid_destination")

    @patch("src.application.workflows.node_factories.NodeContext")
    async def test_destination_node_execution(
        self, mock_node_context_class, sample_tracklist
    ):
        """Test destination node execution flow."""
        from src.application.workflows.node_factories import create_destination_node

        # Mock NodeContext
        mock_ctx = MagicMock()
        mock_ctx.extract_tracklist.return_value = sample_tracklist
        mock_node_context_class.return_value = mock_ctx

        # Mock handler result
        async def mock_handler(tracklist, config, context):
            return {"operation": "test_operation", "track_count": len(tracklist.tracks)}

        # Patch the handlers registry
        with patch(
            "src.application.workflows.node_factories.DESTINATION_HANDLERS",
            {"test_dest": mock_handler},
        ):
            node_func = create_destination_node("test_dest")

            context = {"test": "context"}
            config = {"test": "config"}

            result = await node_func(context, config)

            assert result["operation"] == "test_operation"
            assert result["track_count"] == 2
            mock_ctx.extract_tracklist.assert_called_once()


class TestEnricherNodeFactory:
    """Test enricher node creation."""

    @patch("src.application.workflows.node_factories.NodeContext")
    async def test_create_enricher_node_basic(
        self, mock_node_context_class, sample_tracklist
    ):
        """Test basic enricher node creation and execution."""
        from src.application.workflows.node_factories import create_enricher_node

        # Mock NodeContext
        mock_ctx = MagicMock()
        mock_ctx.extract_tracklist.return_value = sample_tracklist
        mock_ctx.get_connector.return_value = AsyncMock()
        mock_ctx.extract_use_cases.return_value = MagicMock()
        mock_ctx.extract_workflow_context.return_value = AsyncMock()
        mock_node_context_class.return_value = mock_ctx

        # Mock use case execution
        mock_workflow_context = AsyncMock()
        mock_result = MagicMock()
        mock_result.enriched_tracklist = sample_tracklist
        mock_result.metrics_added = {"test_metric": [1, 2]}
        mock_result.errors = []
        mock_workflow_context.execute_use_case.return_value = mock_result
        mock_ctx.extract_workflow_context.return_value = mock_workflow_context

        # Mock format_enrichment_result
        with patch(
            "src.application.workflows.node_factories.NodeContext.format_enrichment_result"
        ) as mock_format:
            mock_format.return_value = {"operation": "test_enrichment", "success": True}

            config = {"connector": "lastfm"}
            node_func = create_enricher_node(config)

            context = {"test": "context"}
            node_config = {"max_age_hours": 24}

            result = await node_func(context, node_config)

            assert result["operation"] == "test_enrichment"
            assert result["success"] is True

    def test_create_enricher_node_missing_connector(self):
        """Test enricher node creation with missing connector."""
        from src.application.workflows.node_factories import create_enricher_node

        config = {}  # Missing connector

        with pytest.raises(
            ValueError, match="Enricher configuration must specify a 'connector' type"
        ):
            create_enricher_node(config)

    @patch("src.application.workflows.node_factories.NodeContext")
    async def test_create_play_history_enricher_node(
        self, mock_node_context_class, sample_tracklist
    ):
        """Test play history enricher node creation."""
        from src.application.workflows.node_factories import (
            create_play_history_enricher_node,
        )

        # Mock NodeContext
        mock_ctx = MagicMock()
        mock_ctx.extract_tracklist.return_value = sample_tracklist
        mock_ctx.extract_use_cases.return_value = MagicMock()
        mock_ctx.extract_workflow_context.return_value = AsyncMock()
        mock_node_context_class.return_value = mock_ctx

        # Mock use case execution
        mock_workflow_context = AsyncMock()
        mock_result = MagicMock()
        mock_result.enriched_tracklist = sample_tracklist
        mock_result.metrics_added = {"total_plays": [5, 10]}
        mock_result.errors = []
        mock_workflow_context.execute_use_case.return_value = mock_result
        mock_ctx.extract_workflow_context.return_value = mock_workflow_context

        # Mock format_enrichment_result
        with patch(
            "src.application.workflows.node_factories.NodeContext.format_enrichment_result"
        ) as mock_format:
            mock_format.return_value = {
                "operation": "play_history_enrichment",
                "success": True,
            }

            node_func = create_play_history_enricher_node()

            context = {"test": "context"}
            config = {"metrics": ["total_plays"], "period_days": 30}

            result = await node_func(context, config)

            assert result["operation"] == "play_history_enrichment"
            assert result["success"] is True


class TestHelperFunctions:
    """Test helper functions."""

    def test_get_connector_metric_names_lastfm(self):
        """Test _get_connector_metric_names for lastfm."""
        from src.application.workflows.node_factories import _get_connector_metric_names

        # Mock the metrics registry
        with patch(
            "src.infrastructure.connectors._shared.metrics.get_connector_metrics"
        ) as mock_get_metrics:
            mock_get_metrics.return_value = [
                "lastfm_user_playcount",
                "lastfm_global_playcount",
                "lastfm_listeners",
            ]

            metric_names = _get_connector_metric_names("lastfm", ["user_playcount"])

            assert "lastfm_user_playcount" in metric_names
            assert isinstance(metric_names, list)
            assert all(isinstance(name, str) for name in metric_names)

    def test_get_connector_metric_names_unknown(self):
        """Test _get_connector_metric_names for unknown connector."""
        from src.application.workflows.node_factories import _get_connector_metric_names

        # Mock empty metrics registry for unknown connector
        with patch(
            "src.infrastructure.connectors._shared.metrics.get_connector_metrics"
        ) as mock_get_metrics:
            mock_get_metrics.return_value = []

            metric_names = _get_connector_metric_names(
                "unknown_connector", ["test_attr"]
            )

            assert metric_names == []  # Should return empty list for unknown connector

    def test_get_connector_metric_names_spotify(self):
        """Test _get_connector_metric_names for spotify with exact matches."""
        from src.application.workflows.node_factories import _get_connector_metric_names

        # Mock the metrics registry for spotify
        with patch(
            "src.infrastructure.connectors._shared.metrics.get_connector_metrics"
        ) as mock_get_metrics:
            mock_get_metrics.return_value = ["spotify_popularity", "explicit_flag"]

            metric_names = _get_connector_metric_names("spotify", ["popularity"])

            assert (
                "spotify_popularity" in metric_names
            )  # Should map popularity -> spotify_popularity


class TestWorkflowNodeFactory:
    """Test WorkflowNodeFactory class."""

    def test_workflow_node_factory_make_node(self):
        """Test WorkflowNodeFactory.make_node method."""
        from src.application.workflows.node_factories import WorkflowNodeFactory

        mock_context = MagicMock()
        factory = WorkflowNodeFactory(mock_context)

        node_func = factory.make_node("filter", "deduplicate")
        assert callable(node_func)
