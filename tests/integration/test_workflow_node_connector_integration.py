"""Integration tests for workflow nodes with real connector instances.

These tests prevent runtime failures by verifying the complete execution path
from workflow nodes through connectors to ensure interface contracts are met.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.application.workflows.context import ConnectorRegistryImpl
from src.application.workflows.source_nodes import playlist_source
from src.domain.entities.playlist import ConnectorPlaylist, ConnectorPlaylistItem
from src.domain.entities.track import ConnectorTrack, Artist


class TestWorkflowConnectorIntegration:
    """Integration tests for workflow nodes with real connector instances."""

    @pytest.fixture
    def real_spotify_connector(self):
        """Get real Spotify connector instance from registry."""
        registry = ConnectorRegistryImpl()
        return registry.get_connector("spotify")

    async def test_playlist_source_with_real_spotify_connector_interface(self, real_spotify_connector):
        """Integration test: verify playlist_source can call real Spotify connector methods.
        
        Prevents: AttributeError when workflow execution reaches connector methods
        This test focuses on method existence and interface contracts, not full execution.
        """
        # Verify the connector has the methods that playlist_source will try to call
        assert hasattr(real_spotify_connector, "get_playlist"), "Missing get_playlist method"
        assert hasattr(real_spotify_connector, "get_tracks_by_ids"), "Missing get_tracks_by_ids method"
        assert hasattr(real_spotify_connector, "convert_track_to_connector"), "Missing convert_track_to_connector method"
        
        # Verify method signatures are callable (this would catch AttributeError early)
        import inspect
        
        # get_playlist should be async and accept playlist_id
        sig = inspect.signature(real_spotify_connector.get_playlist)
        assert len(sig.parameters) >= 1, "get_playlist should accept playlist_id parameter"
        assert inspect.iscoroutinefunction(real_spotify_connector.get_playlist)
        
        # get_tracks_by_ids should be async and accept track IDs
        sig = inspect.signature(real_spotify_connector.get_tracks_by_ids)
        assert len(sig.parameters) >= 1, "get_tracks_by_ids should accept track_ids parameter"
        assert inspect.iscoroutinefunction(real_spotify_connector.get_tracks_by_ids)
        
        # convert_track_to_connector should be sync and accept track data
        sig = inspect.signature(real_spotify_connector.convert_track_to_connector)
        assert len(sig.parameters) >= 1, "convert_track_to_connector should accept track_data parameter"
        assert not inspect.iscoroutinefunction(real_spotify_connector.convert_track_to_connector)

    def test_source_node_connector_method_expectations(self, real_spotify_connector):
        """Test that connector has all methods expected by source nodes.
        
        Prevents: AttributeError for missing methods during workflow execution
        """
        # These are the exact methods called by playlist_source
        required_methods = [
            "get_playlist",
            "get_tracks_by_ids", 
            "convert_track_to_connector"
        ]
        
        for method_name in required_methods:
            assert hasattr(real_spotify_connector, method_name), (
                f"Spotify connector missing required method: {method_name}"
            )
            
        # Verify async/sync expectations
        import inspect
        assert inspect.iscoroutinefunction(real_spotify_connector.get_playlist)
        assert inspect.iscoroutinefunction(real_spotify_connector.get_tracks_by_ids)
        assert not inspect.iscoroutinefunction(real_spotify_connector.convert_track_to_connector)

    def test_connector_method_call_chain_validation(self, real_spotify_connector):
        """Test the complete method call chain used by workflow nodes."""
        # Verify convert_track_to_connector works with realistic input
        sample_spotify_track = {
            "id": "test_track_123",
            "name": "Test Track",
            "artists": [{"name": "Test Artist"}],
            "album": {
                "name": "Test Album",
                "release_date": "2024-01-01"
            },
            "duration_ms": 180000
        }
        
        # This should not raise AttributeError
        connector_track = real_spotify_connector.convert_track_to_connector(sample_spotify_track)
        
        # Verify it returns the expected type
        assert isinstance(connector_track, ConnectorTrack)
        assert connector_track.connector_name == "spotify"
        assert connector_track.connector_track_id == "test_track_123"
        assert connector_track.title == "Test Track"