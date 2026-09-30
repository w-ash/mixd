"""Test WorkflowContext implementation with comprehensive TDD coverage."""

from unittest.mock import MagicMock, call


class TestWorkflowContext:
    """Test WorkflowContext implementation with TDD."""

    async def test_workflow_context_with_real_dependencies(self):
        """create_workflow_context wires the real connector registry.

        Prevents: 'No connector registry available' errors.
        """
        from src.application.workflows.context import create_workflow_context

        context = create_workflow_context()

        assert {"spotify", "lastfm"} <= set(context.connectors.list_connectors())

        # Instances are cached, so repeated lookups share one connection pool
        spotify_connector = context.connectors.get_connector("spotify")
        assert context.connectors.get_connector("spotify") is spotify_connector

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
        assert catalog.describe.call_args_list == [call("spotify"), call("lastfm")]

    async def test_describe_unknown_connector_raises(self):
        """describe() surfaces the catalog's ValueError for unregistered names."""
        import pytest

        from src.application.workflows.context import ConnectorRegistryImpl

        with pytest.raises(ValueError, match="Unknown connector: nonexistent"):
            ConnectorRegistryImpl().describe("nonexistent")
