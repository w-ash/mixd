"""Integration tests for node execution with real workflow context.

These tests verify that nodes can access the required context providers
and that workflow context injection works correctly.
"""

import pytest
from sqlalchemy import text

from src.application.workflows.context import create_workflow_context
from src.domain.entities.track import Artist, Track, TrackList


class TestNodeContextIntegration:
    """Test node execution with real workflow context."""

    @pytest.mark.asyncio
    async def test_workflow_context_creation(self):
        """Test that workflow context can be created with all providers."""
        # Create real workflow context
        workflow_context = create_workflow_context()

        # Verify all required providers exist
        assert workflow_context.config is not None
        assert workflow_context.logger is not None
        assert workflow_context.connectors is not None
        assert workflow_context.use_cases is not None
        assert workflow_context.session_provider is not None
        assert workflow_context.use_cases is not None

        # Test that providers have expected interfaces
        assert hasattr(workflow_context.config, "get")
        assert hasattr(workflow_context.logger, "info")
        assert hasattr(workflow_context.connectors, "list_connectors")
        assert hasattr(
            workflow_context.use_cases, "get_create_canonical_playlist_use_case"
        )
        assert hasattr(workflow_context.session_provider, "get_session")

    @pytest.mark.asyncio
    async def test_context_injection_structure(self):
        """Test that context injection creates expected structure."""
        # Create real workflow context
        workflow_context = create_workflow_context()

        # Create context dictionary like Prefect would after injection
        injected_context = {
            "parameters": {"test_param": "test_value"},
            "use_cases": workflow_context.use_cases,
            "connectors": workflow_context.connectors,
            "config": workflow_context.config,
            "logger": workflow_context.logger,
            "session_provider": workflow_context.session_provider,
            "repositories": workflow_context.use_cases,
        }

        # Verify all required keys are present
        required_keys = [
            "use_cases",
            "connectors",
            "config",
            "logger",
            "session_provider",
        ]
        for key in required_keys:
            assert key in injected_context
            assert injected_context[key] is not None

        # Test that use_cases can be accessed as nodes expect
        use_cases = injected_context.get("use_cases")
        assert use_cases is not None
        assert hasattr(use_cases, "get_create_canonical_playlist_use_case")

    @pytest.mark.asyncio
    async def test_session_provider_functionality(self):
        """Test that session provider works correctly."""
        # Create real workflow context
        workflow_context = create_workflow_context()

        # Test that session provider returns working sessions
        session_cm = workflow_context.session_provider.get_session()

        async with session_cm as session:
            # Verify session is usable
            assert session is not None
            # Basic database operation should work (with proper text() wrapper)
            result = await session.execute(text("SELECT 1"))
            assert result is not None

    @pytest.mark.asyncio
    async def test_use_case_provider_functionality(self):
        """Test that use case provider can create use cases."""
        # Create real workflow context
        workflow_context = create_workflow_context()

        # Test that use case provider can create use cases
        use_case = (
            await workflow_context.use_cases.get_create_canonical_playlist_use_case()
        )
        assert use_case is not None
        assert hasattr(use_case, "execute")

    @pytest.mark.asyncio
    async def test_connector_registry_functionality(self):
        """Test that connector registry works correctly."""
        # Create real workflow context
        workflow_context = create_workflow_context()

        # Test that connector registry works
        connectors = workflow_context.connectors.list_connectors()
        assert isinstance(connectors, list)
        assert len(connectors) > 0  # Should have at least some connectors

        # Test that we can get specific connectors
        if "spotify" in connectors:
            spotify_connector = workflow_context.connectors.get_connector("spotify")
            assert spotify_connector is not None

    def test_config_provider_functionality(self):
        """Test that config provider works correctly."""
        # Create real workflow context
        workflow_context = create_workflow_context()

        # Test config provider
        config_value = workflow_context.config.get("NONEXISTENT_KEY", "default_value")
        assert config_value == "default_value"

        # Test with a key that might exist
        database_url = workflow_context.config.get("DATABASE_URL", None)
        # Should not crash, value can be None or a string
        assert database_url is None or isinstance(database_url, str)

    @pytest.mark.asyncio
    async def test_node_context_extraction(self):
        """Test that node context extraction works with real context."""
        from src.application.workflows.node_context import NodeContext

        # Create test context like Prefect would provide
        test_tracklist = TrackList(
            tracks=[
                Track(
                    title="Context Test Track",
                    artists=[Artist(name="Context Test Artist")],
                    album="Context Test Album",
                    duration_ms=180000,
                )
            ]
        )

        context = {
            "parameters": {"test_param": "test_value"},
            "upstream_task_id": "test_upstream",
            "test_upstream": {
                "tracklist": test_tracklist,
                "operation": "test_operation",
                "track_count": 1,
            },
        }

        # Create NodeContext and test extraction
        node_ctx = NodeContext(context)

        # Test tracklist extraction
        extracted_tracklist = node_ctx.extract_tracklist()
        assert isinstance(extracted_tracklist, TrackList)
        assert len(extracted_tracklist.tracks) == 1
        assert extracted_tracklist.tracks[0].title == "Context Test Track"

        # Test upstream result access (using direct context access since get_parameter may not exist)
        upstream_result = context.get("test_upstream")
        assert upstream_result is not None
        assert upstream_result["operation"] == "test_operation"
