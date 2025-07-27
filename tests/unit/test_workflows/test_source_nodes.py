"""Test source workflow nodes with current implementation."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain.entities.track import Artist, Track


class TestSourceNodes:
    """Test source workflow nodes."""

    @pytest.fixture
    def sample_tracks(self):
        """Create sample tracks for testing."""
        return [
            Track(
                title="Test Song 1",
                artists=[Artist(name="Artist 1")],
                album="Test Album",
                duration_ms=180000,
                connector_track_ids={"spotify": "spotify_track_1"},
            ),
            Track(
                title="Test Song 2", 
                artists=[Artist(name="Artist 2")],
                album="Test Album 2",
                duration_ms=200000,
                connector_track_ids={"spotify": "spotify_track_2"},
            ),
        ]

    @pytest.fixture
    def mock_node_context(self, sample_tracks):
        """Mock NodeContext for testing."""
        context = {
            "workflow_context": MagicMock(),
        }
        
        # Mock execute_use_case method
        async def mock_execute_use_case(use_case_getter, command):
            mock_result = MagicMock()
            mock_result.playlist = MagicMock()
            mock_result.playlist.id = "test_canonical_id"
            mock_result.playlist.name = "Test Playlist"
            mock_result.playlist.tracks = sample_tracks
            return mock_result
            
        context["workflow_context"].execute_use_case = mock_execute_use_case
        
        # Mock use cases
        use_cases = MagicMock()
        use_cases.get_read_canonical_playlist_use_case = AsyncMock()
        use_cases.get_create_canonical_playlist_use_case = AsyncMock()
        use_cases.get_update_canonical_playlist_use_case = AsyncMock()
        context["workflow_context"].use_cases = use_cases
        
        # Mock connector
        mock_connector = AsyncMock()
        mock_connector.get_playlist = AsyncMock()
        mock_connector.get_tracks_by_ids = AsyncMock()
        mock_connector.convert_track_to_connector = MagicMock()
        
        # Mock NodeContext class
        with patch('src.application.workflows.source_nodes.NodeContext') as MockNodeContext:
            mock_ctx = MagicMock()
            mock_ctx.extract_workflow_context.return_value = context["workflow_context"]
            mock_ctx.extract_use_cases.return_value = use_cases
            mock_ctx.get_connector.return_value = mock_connector
            MockNodeContext.return_value = mock_ctx
            
            context["mock_connector"] = mock_connector
            context["mock_ctx"] = mock_ctx
            
            yield context


class TestPlaylistSource(TestSourceNodes):
    """Test playlist_source node."""

    async def test_canonical_playlist_source(self, mock_node_context, sample_tracks):
        """Test reading from canonical playlist (no connector)."""
        from src.application.workflows.source_nodes import playlist_source
        
        config = {"playlist_id": "canonical_123"}
        context = {}
        
        result = await playlist_source(context, config)
        
        assert result["operation"] == "playlist_source"
        assert result["source"] == "canonical"
        assert result["playlist_id"] == "test_canonical_id"
        assert result["playlist_name"] == "Test Playlist"
        assert result["track_count"] == 2
        assert len(result["tracklist"].tracks) == 2

    async def test_connector_playlist_source_empty(self, mock_node_context):
        """Test connector playlist source with empty playlist."""
        from src.application.workflows.source_nodes import playlist_source
        
        # Mock empty playlist
        mock_connector = mock_node_context["mock_connector"]
        mock_playlist = MagicMock()
        mock_playlist.name = "Empty Playlist"
        mock_playlist.items = []
        mock_connector.get_playlist.return_value = mock_playlist
        
        config = {
            "playlist_id": "spotify_empty_123",
            "connector": "spotify"
        }
        context = {}
        
        result = await playlist_source(context, config)
        
        assert result["operation"] == "playlist_source"
        assert result["source"] == "spotify"
        assert result["playlist_name"] == "Empty Playlist"
        assert result["track_count"] == 0
        assert len(result["tracklist"].tracks) == 0

    async def test_connector_playlist_source_not_found(self, mock_node_context):
        """Test connector playlist source with playlist not found."""
        from src.application.workflows.source_nodes import playlist_source
        
        # Mock playlist not found
        mock_connector = mock_node_context["mock_connector"]
        mock_connector.get_playlist.return_value = None
        
        config = {
            "playlist_id": "spotify_missing_123", 
            "connector": "spotify"
        }
        context = {}
        
        result = await playlist_source(context, config)
        
        assert result["operation"] == "playlist_source"
        assert result["source"] == "spotify"
        assert result["playlist_name"] == "Unknown"
        assert result["track_count"] == 0
        assert result["playlist_id"] is None

    async def test_connector_playlist_source_create_new(self, mock_node_context, sample_tracks):
        """Test connector playlist source creating new canonical playlist."""
        from src.application.workflows.source_nodes import playlist_source
        from src.domain.entities.track import ConnectorTrack
        
        # Mock successful playlist fetch
        mock_connector = mock_node_context["mock_connector"]
        mock_playlist = MagicMock()
        mock_playlist.name = "Spotify Test Playlist"
        mock_playlist.description = "Test description"
        mock_playlist.items = ["item1", "item2"]
        mock_playlist.track_ids = ["spotify_track_1", "spotify_track_2"]
        mock_connector.get_playlist.return_value = mock_playlist
        
        # Mock track data
        track_data = {
            "spotify_track_1": {"name": "Song 1", "artists": [{"name": "Artist 1"}]},
            "spotify_track_2": {"name": "Song 2", "artists": [{"name": "Artist 2"}]},
        }
        mock_connector.get_tracks_by_ids.return_value = track_data
        
        # Mock connector track conversion
        def mock_convert(track_data):
            connector_track = ConnectorTrack(
                title=track_data["name"],
                artists=[Artist(name=artist["name"]) for artist in track_data["artists"]],
                album="Test Album",
                duration_ms=180000,
                connector_name="spotify",
                connector_track_id=next(iter(track_data.keys())) if isinstance(track_data, dict) else "test_id",
            )
            return connector_track
            
        mock_connector.convert_track_to_connector.side_effect = mock_convert
        
        # Override the mock to simulate no existing playlist (will create new)
        call_count = 0
        
        async def mock_execute_use_case(use_case_getter, command):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # First call (read existing) should raise error
                raise ValueError("Not found")
            else:  # Second call (create) should succeed
                mock_playlist = MagicMock()
                mock_playlist.id = "new_canonical_id"
                mock_playlist.name = "Spotify Test Playlist"
                mock_playlist.tracks = sample_tracks
                return MagicMock(playlist=mock_playlist)
        
        mock_node_context["workflow_context"].execute_use_case = mock_execute_use_case
        
        config = {
            "playlist_id": "spotify_new_123",
            "connector": "spotify"
        }
        context = {}
        
        result = await playlist_source(context, config)
        
        assert result["operation"] == "playlist_source"
        assert result["source"] == "spotify"
        assert result["playlist_name"] == "Spotify Test Playlist"
        assert result["action"] == "created"
        assert result["track_count"] == 2

    async def test_missing_playlist_id(self, mock_node_context):
        """Test error handling for missing playlist_id."""
        from src.application.workflows.source_nodes import playlist_source
        
        config = {}  # Missing playlist_id
        context = {}
        
        with pytest.raises(ValueError, match="Missing required config parameter: playlist_id"):
            await playlist_source(context, config)


class TestHelperFunctions(TestSourceNodes):
    """Test helper functions."""

    def test_convert_connector_track_to_domain(self):
        """Test connector track to domain conversion."""
        from src.application.workflows.source_nodes import (
            _convert_connector_track_to_domain,
        )
        from src.domain.entities.track import ConnectorTrack
        
        connector_track = ConnectorTrack(
            title="Test Song",
            artists=[Artist(name="Test Artist")],
            album="Test Album",
            duration_ms=180000,
            release_date="2023-01-01",
            isrc="TEST123456789",
            connector_name="spotify",
            connector_track_id="spotify_123",
            raw_metadata={"popularity": 85, "preview_url": "https://preview.url"},
        )
        
        domain_track = _convert_connector_track_to_domain(connector_track)
        
        assert domain_track.title == "Test Song"
        assert domain_track.artists == [Artist(name="Test Artist")]
        assert domain_track.album == "Test Album"
        assert domain_track.duration_ms == 180000
        assert domain_track.connector_track_ids == {"spotify": "spotify_123"}
        assert domain_track.connector_metadata["spotify"]["popularity"] is None
        assert domain_track.connector_metadata["spotify"]["preview_url"] is None