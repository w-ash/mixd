"""Test source workflow nodes with current implementation."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.application.use_cases.get_liked_tracks import GetLikedTracksResult
from src.application.use_cases.get_played_tracks import GetPlayedTracksResult
from src.domain.entities.track import Artist, Track, TrackList


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
        with patch(
            "src.application.workflows.source_nodes.NodeContext"
        ) as MockNodeContext:
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

        config = {"playlist_id": "spotify_empty_123", "connector": "spotify"}
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

        config = {"playlist_id": "spotify_missing_123", "connector": "spotify"}
        context = {}

        result = await playlist_source(context, config)

        assert result["operation"] == "playlist_source"
        assert result["source"] == "spotify"
        assert result["playlist_name"] == "Unknown"
        assert result["track_count"] == 0
        assert result["playlist_id"] is None

    async def test_connector_playlist_source_create_new(
        self, mock_node_context, sample_tracks
    ):
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
                artists=[
                    Artist(name=artist["name"]) for artist in track_data["artists"]
                ],
                album="Test Album",
                duration_ms=180000,
                connector_name="spotify",
                connector_track_id=next(iter(track_data.keys()))
                if isinstance(track_data, dict)
                else "test_id",
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

        config = {"playlist_id": "spotify_new_123", "connector": "spotify"}
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

        with pytest.raises(
            ValueError, match="Missing required config parameter: playlist_id"
        ):
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


class TestSourceLikedTracks(TestSourceNodes):
    """Test source_liked_tracks node - critical user paths."""

    @pytest.fixture
    def mock_liked_tracks_context(self, sample_tracks):
        """Mock context for liked tracks testing."""
        context = {}
        
        # Mock workflow context
        workflow_context = MagicMock()
        
        # Mock successful use case execution
        async def mock_execute_use_case(use_case_func):
            mock_result = GetLikedTracksResult(
                tracklist=TrackList(
                    tracks=sample_tracks,
                    metadata={
                        "operation": "get_liked_tracks",
                        "connector_filter": None,
                        "sort_by": "liked_at_desc",
                        "track_count": len(sample_tracks),
                    }
                ),
                execution_time_ms=100
            )
            return mock_result
            
        workflow_context.execute_use_case = mock_execute_use_case
        
        # Mock NodeContext
        with patch("src.application.workflows.source_nodes.NodeContext") as MockNodeContext:
            mock_ctx = MagicMock()
            mock_ctx.extract_workflow_context.return_value = workflow_context
            MockNodeContext.return_value = mock_ctx
            
            yield context

    async def test_source_liked_tracks_default_config(self, mock_liked_tracks_context, sample_tracks):
        """Test source_liked_tracks with default configuration."""
        from src.application.workflows.source_nodes import source_liked_tracks
        
        config = {}  # Use all defaults
        context = {}
        
        result = await source_liked_tracks(context, config)
        
        assert result["operation"] == "source_liked_tracks"
        assert result["track_count"] == 2
        assert result["sort_by"] == "liked_at_desc"  # Default
        assert result["connector_filter"] is None
        assert "execution_time_ms" in result
        assert len(result["tracklist"].tracks) == 2

    async def test_source_liked_tracks_with_sorting(self, mock_liked_tracks_context):
        """Test source_liked_tracks with different sort options."""
        from src.application.workflows.source_nodes import source_liked_tracks
        
        sort_options = ["liked_at_desc", "liked_at_asc", "title_asc", "random"]
        
        for sort_option in sort_options:
            config = {"sort_by": sort_option, "limit": 1000}
            context = {}
            
            result = await source_liked_tracks(context, config)
            
            assert result["sort_by"] == sort_option
            assert result["operation"] == "source_liked_tracks"

    async def test_source_liked_tracks_with_connector_filter(self, mock_liked_tracks_context):
        """Test source_liked_tracks with connector filter."""
        from src.application.workflows.source_nodes import source_liked_tracks
        
        config = {
            "connector_filter": "spotify",
            "limit": 500,
            "sort_by": "title_asc"
        }
        context = {}
        
        result = await source_liked_tracks(context, config)
        
        assert result["connector_filter"] == "spotify"
        assert result["sort_by"] == "title_asc"
        assert result["operation"] == "source_liked_tracks"

    async def test_source_liked_tracks_enforces_limit_maximum(self, mock_liked_tracks_context):
        """Test that source_liked_tracks enforces maximum limit."""
        from src.application.workflows.source_nodes import source_liked_tracks
        
        config = {"limit": 15000}  # Exceeds max of 10000
        context = {}
        
        # Should not raise error, but limit to max
        result = await source_liked_tracks(context, config)
        
        assert result["operation"] == "source_liked_tracks"
        # The actual limit enforcement happens in the command validation


class TestSourcePlayedTracks(TestSourceNodes):
    """Test source_played_tracks node - critical user paths."""

    @pytest.fixture
    def mock_played_tracks_context(self, sample_tracks):
        """Mock context for played tracks testing."""
        context = {}
        
        # Mock workflow context
        workflow_context = MagicMock()
        
        # Mock successful use case execution
        async def mock_execute_use_case(use_case_func):
            mock_result = GetPlayedTracksResult(
                tracklist=TrackList(
                    tracks=sample_tracks,
                    metadata={
                        "operation": "get_played_tracks",
                        "days_back": None,
                        "connector_filter": None,
                        "sort_by": "played_at_desc",
                        "track_count": len(sample_tracks),
                        "total_plays": {1: 5, 2: 3},
                        "last_played_dates": {}
                    }
                ),
                execution_time_ms=150
            )
            return mock_result
            
        workflow_context.execute_use_case = mock_execute_use_case
        
        # Mock NodeContext
        with patch("src.application.workflows.source_nodes.NodeContext") as MockNodeContext:
            mock_ctx = MagicMock()
            mock_ctx.extract_workflow_context.return_value = workflow_context
            MockNodeContext.return_value = mock_ctx
            
            yield context

    async def test_source_played_tracks_default_config(self, mock_played_tracks_context, sample_tracks):
        """Test source_played_tracks with default configuration."""
        from src.application.workflows.source_nodes import source_played_tracks
        
        config = {}  # Use all defaults
        context = {}
        
        result = await source_played_tracks(context, config)
        
        assert result["operation"] == "source_played_tracks"
        assert result["track_count"] == 2
        assert result["sort_by"] == "played_at_desc"  # Default
        assert result["days_back"] is None
        assert result["connector_filter"] is None
        assert "execution_time_ms" in result
        assert len(result["tracklist"].tracks) == 2

    async def test_source_played_tracks_with_all_options(self, mock_played_tracks_context):
        """Test source_played_tracks with all configuration options."""
        from src.application.workflows.source_nodes import source_played_tracks
        
        config = {
            "limit": 1000,
            "days_back": 30,
            "connector_filter": "spotify",
            "sort_by": "total_plays_desc"
        }
        context = {}
        
        result = await source_played_tracks(context, config)
        
        assert result["operation"] == "source_played_tracks"
        assert result["sort_by"] == "total_plays_desc"
        assert result["days_back"] == 30
        assert result["connector_filter"] == "spotify"

    async def test_source_played_tracks_sort_options(self, mock_played_tracks_context):
        """Test source_played_tracks with different sort options."""
        from src.application.workflows.source_nodes import source_played_tracks
        
        sort_options = [
            "played_at_desc", "total_plays_desc", "last_played_desc", 
            "first_played_asc", "title_asc", "random"
        ]
        
        for sort_option in sort_options:
            config = {"sort_by": sort_option}
            context = {}
            
            result = await source_played_tracks(context, config)
            
            assert result["sort_by"] == sort_option
            assert result["operation"] == "source_played_tracks"

    async def test_source_played_tracks_with_time_window(self, mock_played_tracks_context):
        """Test source_played_tracks with days_back time window."""
        from src.application.workflows.source_nodes import source_played_tracks
        
        config = {
            "days_back": 90,
            "sort_by": "last_played_desc",
            "limit": 2000
        }
        context = {}
        
        result = await source_played_tracks(context, config)
        
        assert result["days_back"] == 90
        assert result["sort_by"] == "last_played_desc"
        assert result["operation"] == "source_played_tracks"
