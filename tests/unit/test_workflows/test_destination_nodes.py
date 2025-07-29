"""Test destination workflow nodes with current implementation."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.entities.track import Artist, Track, TrackList


class TestDestinationNodes:
    """Test destination workflow nodes."""

    @pytest.fixture
    def sample_tracklist(self):
        """Create a sample tracklist for testing."""
        tracks = [
            Track(
                title="Test Song 1",
                artists=[Artist(name="Artist 1")],
                album="Test Album",
                duration_ms=180000,
            ),
            Track(
                title="Test Song 2",
                artists=[Artist(name="Artist 2")],
                album="Test Album 2",
                duration_ms=200000,
            ),
        ]
        return TrackList(tracks=tracks)

    @pytest.fixture
    def mock_node_context(self):
        """Mock NodeContext for testing."""

        # Create context with workflow context
        context = {
            "workflow_context": MagicMock(),
            "tracklist": TrackList(tracks=[]),
        }

        # Mock execute_use_case method
        async def mock_execute_use_case(use_case_getter, command):
            mock_result = MagicMock()
            mock_result.playlist = MagicMock()
            mock_result.playlist.id = "test_canonical_id"
            mock_result.playlist.name = "Test Playlist"
            mock_result.external_playlist_id = "test_external_id"
            mock_result.playlist_id = "test_canonical_id"  # For connector updates
            mock_result.operations_performed = ["create"]
            mock_result.tracks_added = 2
            mock_result.tracks_removed = 0
            mock_result.tracks_moved = 0
            return mock_result

        context["workflow_context"].execute_use_case = mock_execute_use_case

        # Mock use cases getter
        use_cases = MagicMock()
        use_cases.get_create_canonical_playlist_use_case = AsyncMock()
        use_cases.get_create_connector_playlist_use_case = AsyncMock()
        use_cases.get_update_canonical_playlist_use_case = AsyncMock()
        use_cases.get_update_connector_playlist_use_case = AsyncMock()
        context["workflow_context"].use_cases = use_cases

        return context


class TestCreatePlaylist(TestDestinationNodes):
    """Test create_playlist destination node."""

    async def test_create_canonical_only(self, sample_tracklist, mock_node_context):
        """Test creating canonical playlist only (no connector)."""
        from src.application.workflows.destination_nodes import create_playlist

        config = {
            "name": "Test Canonical Playlist",
            "description": "Test description",
        }

        result = await create_playlist(sample_tracklist, config, mock_node_context)

        assert result["operation"] == "create_playlist"
        assert result["playlist_name"] == "Test Playlist"
        assert result["playlist_id"] == "test_canonical_id"
        assert result["track_count"] == 2
        assert "connector" not in result
        assert "external_playlist_id" not in result

    async def test_create_with_connector(self, sample_tracklist, mock_node_context):
        """Test creating playlist with connector sync."""
        from src.application.workflows.destination_nodes import create_playlist

        config = {
            "name": "Test Connector Playlist",
            "description": "Test description",
            "connector": "spotify",
        }

        result = await create_playlist(sample_tracklist, config, mock_node_context)

        assert result["operation"] == "create_playlist"
        assert result["playlist_name"] == "Test Playlist"
        assert result["playlist_id"] == "test_canonical_id"
        assert result["connector"] == "spotify"
        assert result["external_playlist_id"] == "test_external_id"
        assert result["track_count"] == 2

    async def test_create_missing_name(self, sample_tracklist, mock_node_context):
        """Test error handling for missing playlist name."""
        from src.application.workflows.destination_nodes import create_playlist

        config = {}  # Missing name

        with pytest.raises(
            ValueError, match="Missing required 'name' for create_playlist operation"
        ):
            await create_playlist(sample_tracklist, config, mock_node_context)


class TestUpdatePlaylist(TestDestinationNodes):
    """Test update_playlist destination node."""

    async def test_update_canonical_only(self, sample_tracklist, mock_node_context):
        """Test updating canonical playlist only (no connector)."""
        from src.application.workflows.destination_nodes import update_playlist

        config = {
            "playlist_id": "canonical_playlist_123",
            "append": True,
            "name": "Updated Name",
        }

        result = await update_playlist(sample_tracklist, config, mock_node_context)

        assert result["operation"] == "update_playlist"
        assert result["playlist_name"] == "Test Playlist"
        assert result["playlist_id"] == "test_canonical_id"
        assert result["append_mode"] is True
        assert result["track_count"] == 2
        assert "connector" not in result

    async def test_update_with_connector(self, sample_tracklist, mock_node_context):
        """Test updating playlist with connector sync."""
        from src.application.workflows.destination_nodes import update_playlist

        config = {
            "playlist_id": "spotify_playlist_456",
            "connector": "spotify",
            "append": False,
            "description": "Updated description",
        }

        result = await update_playlist(sample_tracklist, config, mock_node_context)

        assert result["operation"] == "update_playlist"
        assert result["connector"] == "spotify"
        assert result["playlist_id"] == "test_canonical_id"
        assert result["append_mode"] is False
        assert result["track_count"] == 2
        assert result["operations_performed"] == ["create"]
        assert result["tracks_added"] == 2

    async def test_update_missing_playlist_id(
        self, sample_tracklist, mock_node_context
    ):
        """Test error handling for missing playlist_id."""
        from src.application.workflows.destination_nodes import update_playlist

        config = {}  # Missing playlist_id

        with pytest.raises(
            ValueError,
            match="Missing required 'playlist_id' for update_playlist operation",
        ):
            await update_playlist(sample_tracklist, config, mock_node_context)


class TestDestinationHandlers(TestDestinationNodes):
    """Test destination handler registry."""

    def test_destination_handlers_registry(self):
        """Test that destination handlers are properly registered."""
        from src.application.workflows.destination_nodes import DESTINATION_HANDLERS

        assert "create_playlist" in DESTINATION_HANDLERS
        assert "update_playlist" in DESTINATION_HANDLERS
        assert callable(DESTINATION_HANDLERS["create_playlist"])
        assert callable(DESTINATION_HANDLERS["update_playlist"])
