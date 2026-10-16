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

    def test_every_playlist_sync_connector_implements_the_protocol(self):
        """Declaring ``playlist_sync`` means implementing ``PlaylistConnector``.

        Prevents: the playlist resolver raising ``TypeError`` at run time
        because a descriptor and its class drifted apart (e.g. a renamed
        ``create_playlist``). Checks every registered connector.
        """
        from src.application.connector_protocols import PlaylistConnector

        registry = ConnectorRegistryImpl()
        declaring = [
            name
            for name in registry.list_connectors()
            if "playlist_sync" in registry.describe(name).capabilities
        ]

        assert "spotify" in declaring
        for name in declaring:
            assert isinstance(registry.get_connector(name), PlaylistConnector), name

    def test_every_registered_connector_constructs_once_per_registry(self):
        """Every registered connector builds, and a second lookup reuses it.

        Prevents: runtime errors when a workflow asks for a connector whose
        factory is broken, and a fresh httpx2 pool per lookup.
        """
        registry = ConnectorRegistryImpl()

        for connector_name in registry.list_connectors():
            first = registry.get_connector(connector_name)
            assert registry.get_connector(connector_name) is first, connector_name

    def test_spotify_declares_library_contains_and_implements_it(self):
        """The registry capability and the runtime protocol agree for Spotify.

        Prevents: enricher.spotify_liked_status failing the NodeContext gate
        because the descriptor or the class drifted.
        """
        from src.application.connector_protocols import LibraryContainsConnector

        registry = ConnectorRegistryImpl()

        assert "library_contains" in registry.describe("spotify").capabilities
        assert isinstance(registry.get_connector("spotify"), LibraryContainsConnector)

    def test_every_track_enrichment_connector_implements_the_protocol(self):
        """Declaring ``track_enrichment`` means implementing ``TrackMetadataConnector``.

        Prevents: ``NodeContext.get_connector`` raising ``TypeError`` at run
        time because a descriptor and its class drifted apart. Checks every
        registered connector, so a new one cannot declare the capability
        without the method.
        """
        from src.application.connector_protocols import TrackMetadataConnector

        registry = ConnectorRegistryImpl()
        declaring = [
            name
            for name in registry.list_connectors()
            if "track_enrichment" in registry.describe(name).capabilities
        ]

        assert {"spotify", "lastfm"} <= set(declaring)
        for name in declaring:
            assert isinstance(registry.get_connector(name), TrackMetadataConnector), (
                name
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
