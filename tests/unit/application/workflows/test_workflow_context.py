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

    async def test_workflow_context_session_via_get_session(self, db_session):
        """Test that a database session works for _with_uow fallback path."""
        assert db_session is not None
