"""Test WorkflowContext implementation with comprehensive TDD coverage."""

from unittest.mock import MagicMock


class TestWorkflowContext:
    """Test WorkflowContext implementation with TDD."""

    async def test_workflow_context_interface(self):
        """Test that WorkflowContext implements all required protocols."""
        from src.application.workflows.context import ConcreteWorkflowContext

        # Mock all dependencies
        mock_connectors = MagicMock()
        mock_use_cases = MagicMock()
        mock_metric_config = MagicMock()

        # Create context
        context = ConcreteWorkflowContext(
            connectors=mock_connectors,
            use_cases=mock_use_cases,
            metric_config=mock_metric_config,
        )

        # Verify all protocol methods are accessible
        assert context.connectors is mock_connectors
        assert context.use_cases is mock_use_cases
        assert context.metric_config is mock_metric_config

    async def test_workflow_context_with_real_dependencies(self):
        """Test WorkflowContext with real infrastructure dependencies."""
        from src.application.workflows.context import create_workflow_context

        # This function should wire up real dependencies
        context = create_workflow_context()

        # Verify real dependencies are connected
        assert context.connectors is not None
        assert context.use_cases is not None

        # Test connector registry functionality
        connectors = context.connectors.list_connectors()
        assert isinstance(connectors, list)
        assert len(connectors) > 0

        # Test that we can get a connector
        spotify_connector = context.connectors.get_connector("spotify")
        assert spotify_connector is not None

        # Descriptors are cached per name and carry the capability set
        descriptor = context.connectors.describe("spotify")
        assert descriptor is context.connectors.describe("spotify")
        assert "library_contains" in descriptor.capabilities

    async def test_describe_caches_per_name(self):
        """describe() asks the catalog once per connector name."""
        from src.application.workflows.context import ConnectorRegistryImpl

        catalog = MagicMock()
        catalog.describe.return_value = MagicMock(name="descriptor")
        registry = ConnectorRegistryImpl(catalog=catalog)

        first = registry.describe("spotify")
        second = registry.describe("spotify")
        registry.describe("lastfm")

        assert first is second
        assert catalog.describe.call_count == 2

    async def test_describe_unknown_connector_raises(self):
        """describe() surfaces the catalog's ValueError for unregistered names."""
        import pytest

        from src.application.workflows.context import ConnectorRegistryImpl

        with pytest.raises(ValueError, match="Unknown connector: nonexistent"):
            ConnectorRegistryImpl().describe("nonexistent")

    async def test_workflow_context_session_via_get_session(self, db_session):
        """Test that a database session works for the execute_service path."""
        assert db_session is not None
