"""Interface contract tests for connector registry.

These tests prevent runtime failures by validating that connectors returned by
get_connector() have the expected methods and interfaces, catching mismatches
before they reach production workflows.

Purpose: Catch interface mismatches like 'SpotifyConnector' object has no attribute '_connector'
"""

import pytest

from src.application.workflows.context import ConnectorRegistryImpl


class TestConnectorContracts:
    """Test that connectors have expected interfaces."""

    def test_connector_registry_provides_valid_spotify_connector(self):
        """Test that Spotify connector has required methods for workflows.

        Prevents: AttributeError: 'SpotifyConnector' object has no attribute '_connector'
        """
        registry = ConnectorRegistryImpl()

        # Get the actual connector instance
        connector = registry.get_connector("spotify")

        # Verify it has required methods for destination nodes
        assert hasattr(connector, "create_playlist"), (
            "Spotify connector must have create_playlist method for destination nodes"
        )
        assert hasattr(connector, "update_playlist"), (
            "Spotify connector must have update_playlist method for destination nodes"
        )
        assert callable(connector.create_playlist), "create_playlist must be callable"
        assert callable(connector.update_playlist), "update_playlist must be callable"

    def test_connector_registry_provides_valid_lastfm_connector(self):
        """Test that Last.fm connector has required methods for enrichment.

        Prevents: Missing methods needed by enricher nodes
        """
        registry = ConnectorRegistryImpl()

        # Get the actual connector instance
        connector = registry.get_connector("lastfm")

        # Verify it has required methods for enricher nodes
        assert hasattr(connector, "get_lastfm_track_info"), (
            "Last.fm connector must have get_lastfm_track_info method for enrichment"
        )
        assert callable(connector.get_lastfm_track_info), (
            "get_lastfm_track_info must be callable"
        )

    def test_connector_registry_get_connector_returns_direct_instance(self):
        """Test that get_connector returns connector directly, not wrapped.

        Prevents: Code expecting ._connector attribute on returned objects
        """
        registry = ConnectorRegistryImpl()

        # Test all available connectors
        for connector_name in registry.list_connectors():
            connector = registry.get_connector(connector_name)

            # Verify it's a direct instance, not a wrapper
            assert not hasattr(connector, "_connector"), (
                f"{connector_name} connector should be returned directly, "
                "not wrapped with ._connector attribute"
            )

    def test_all_connectors_discoverable_and_creatable(self):
        """Test that all registered connectors can be instantiated.

        Prevents: Runtime errors when workflow tries to get unknown connectors
        """
        registry = ConnectorRegistryImpl()

        available_connectors = registry.list_connectors()

        # Should have the core connectors
        assert "spotify" in available_connectors, "Spotify connector must be available"
        assert "lastfm" in available_connectors, "Last.fm connector must be available"

        # All connectors should be instantiable
        for connector_name in available_connectors:
            connector = registry.get_connector(connector_name)
            assert connector is not None, (
                f"{connector_name} connector should be instantiable"
            )

    def test_connector_registry_error_handling(self):
        """Test proper error handling for unknown connectors.

        Prevents: Unclear error messages for unknown connectors
        """
        registry = ConnectorRegistryImpl()

        with pytest.raises(ValueError, match="Unknown connector: nonexistent"):
            registry.get_connector("nonexistent")


class TestSpotifyConnectorContract:
    """Detailed contract tests for Spotify connector interface."""

    @pytest.fixture
    def spotify_connector(self):
        """Get real Spotify connector instance."""
        registry = ConnectorRegistryImpl()
        return registry.get_connector("spotify")

    def test_spotify_create_playlist_signature(self, spotify_connector):
        """Test create_playlist has expected signature.

        Prevents: TypeError when destination nodes call create_playlist
        """
        # Check method exists
        assert hasattr(spotify_connector, "create_playlist")

        # Check it's async (this is critical for workflow nodes)
        import inspect

        assert inspect.iscoroutinefunction(spotify_connector.create_playlist), (
            "create_playlist must be async for workflow compatibility"
        )

    def test_spotify_update_playlist_signature(self, spotify_connector):
        """Test update_playlist has expected signature.

        Prevents: TypeError when destination nodes call update_playlist
        """
        # Check method exists
        assert hasattr(spotify_connector, "update_playlist")

        # Check it's async
        import inspect

        assert inspect.iscoroutinefunction(spotify_connector.update_playlist), (
            "update_playlist must be async for workflow compatibility"
        )

    def test_spotify_get_playlist_method_exists(self, spotify_connector):
        """Test get_playlist method exists for workflow compatibility.

        Prevents: AttributeError when source nodes call get_playlist()
        """
        assert hasattr(spotify_connector, "get_playlist")

        import inspect

        assert inspect.iscoroutinefunction(spotify_connector.get_playlist), (
            "get_playlist must be async for workflow compatibility"
        )

    def test_spotify_convert_track_to_connector_method_exists(self, spotify_connector):
        """Test convert_track_to_connector method exists for workflow compatibility.

        Prevents: AttributeError when source nodes call convert_track_to_connector()
        """
        assert hasattr(spotify_connector, "convert_track_to_connector")

        # Should be synchronous conversion method
        import inspect

        assert not inspect.iscoroutinefunction(
            spotify_connector.convert_track_to_connector
        ), "convert_track_to_connector should be synchronous"


class TestLastFmConnectorContract:
    """Detailed contract tests for Last.fm connector interface."""

    @pytest.fixture
    def lastfm_connector(self):
        """Get real Last.fm connector instance."""
        registry = ConnectorRegistryImpl()
        return registry.get_connector("lastfm")

    def test_lastfm_extractor_availability(self):
        """Test that Last.fm provides extractors for enrichment.

        Prevents: Extractor type mismatches in enricher nodes
        """
        from src.infrastructure.connectors.lastfm import get_connector_config

        config = get_connector_config()
        extractors = config.get("extractors", {})

        # Check key extractors are available
        assert "lastfm_user_playcount" in extractors, (
            "lastfm_user_playcount extractor must be available"
        )
        assert "lastfm_global_playcount" in extractors, (
            "lastfm_global_playcount extractor must be available"
        )

        # Check extractors are callable
        for name, extractor in extractors.items():
            assert callable(extractor), f"Extractor {name} must be callable"
