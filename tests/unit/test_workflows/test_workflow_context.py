"""Test WorkflowContext implementation with comprehensive TDD coverage."""

from unittest.mock import MagicMock

import pytest


@pytest.mark.unit
class TestWorkflowContext:
    """Test WorkflowContext implementation with TDD."""

    async def test_workflow_context_interface(self):
        """Test that WorkflowContext implements all required protocols."""
        # This will fail initially - TDD RED phase
        from src.application.workflows.context import ConcreteWorkflowContext

        # Mock all dependencies
        mock_logger = MagicMock()
        mock_connectors = MagicMock()
        mock_session_provider = MagicMock()
        mock_use_cases = MagicMock()

        # Create context
        context = ConcreteWorkflowContext(
            logger=mock_logger,
            connectors=mock_connectors,
            session_provider=mock_session_provider,
            use_cases=mock_use_cases,
        )

        # Verify all protocol methods are accessible
        assert context.logger is mock_logger
        assert context.connectors is mock_connectors
        assert context.session_provider is mock_session_provider

    async def test_workflow_context_with_real_dependencies(self):
        """Test WorkflowContext with real infrastructure dependencies."""
        from src.application.workflows.context import create_workflow_context

        # This function should wire up real dependencies
        context = create_workflow_context()

        # Verify real dependencies are connected
        assert context.logger is not None
        assert context.connectors is not None
        # repositories attribute removed in Clean Architecture migration
        assert context.session_provider is not None

        # Test connector registry functionality
        connectors = context.connectors.list_connectors()
        assert isinstance(connectors, list)
        assert len(connectors) > 0

        # Test that we can get a connector
        spotify_connector = context.connectors.get_connector("spotify")
        assert spotify_connector is not None

    async def test_workflow_context_logger_functionality(self):
        """Test that logger in WorkflowContext works correctly."""
        from src.application.workflows.context import create_workflow_context

        context = create_workflow_context()

        # Test logger methods (should not raise exceptions)
        context.logger.info("Test info message")
        context.logger.debug("Test debug message")
        context.logger.warning("Test warning message")
        context.logger.error("Test error message")

    async def test_workflow_context_session_provider(self):
        """Test session provider functionality."""
        from src.application.workflows.context import create_workflow_context

        context = create_workflow_context()

        # Test session creation
        async with context.session_provider.get_session() as session:
            assert session is not None
            # Session should be usable for database operations
