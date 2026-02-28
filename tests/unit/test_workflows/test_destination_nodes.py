"""Characterization tests for destination nodes.

Locks down current behavior of create_playlist and update_playlist
before workflow cleanup.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_context():
    """Mock workflow context dict for destination nodes."""
    wf_ctx = AsyncMock()
    wf_ctx.use_cases = MagicMock()

    result = MagicMock()
    result.playlist = MagicMock()
    result.playlist.id = "pl-1"
    result.playlist.name = "Test"
    wf_ctx.execute_use_case = AsyncMock(return_value=result)

    return {"workflow_context": wf_ctx}


class TestCreatePlaylist:
    """Tests for create_playlist destination node."""

    @pytest.mark.asyncio
    async def test_create_without_connector(self, sample_tracklist, mock_context):
        """Create canonical playlist without connector."""
        from src.application.workflows.destination_nodes import create_playlist

        config = {"name": "My Playlist", "description": "Test desc"}
        result = await create_playlist(sample_tracklist, config, mock_context)

        assert "tracklist" in result
        wf = mock_context["workflow_context"]
        wf.execute_use_case.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_with_connector(self, sample_tracklist, mock_context):
        """Create playlist with connector delegates to connector use case."""
        from src.application.workflows.destination_nodes import create_playlist

        result_mock = MagicMock()
        result_mock.playlist = MagicMock()
        result_mock.playlist.id = "pl-2"
        result_mock.playlist.name = "Spotify Playlist"
        result_mock.external_playlist_id = "sp-123"
        mock_context["workflow_context"].execute_use_case = AsyncMock(
            return_value=result_mock
        )

        config = {
            "name": "My Playlist",
            "description": "Test",
            "connector": "spotify",
        }
        result = await create_playlist(sample_tracklist, config, mock_context)

        assert "tracklist" in result

    @pytest.mark.asyncio
    async def test_missing_name_raises(self, sample_tracklist, mock_context):
        """Missing name raises ValueError."""
        from src.application.workflows.destination_nodes import create_playlist

        with pytest.raises(ValueError, match="name"):
            await create_playlist(sample_tracklist, {}, mock_context)

    @pytest.mark.asyncio
    async def test_template_rendering(self, sample_tracklist, mock_context):
        """Template strings in name/description are rendered."""
        from src.application.workflows.destination_nodes import create_playlist

        config = {
            "name": "Playlist ({track_count} tracks)",
            "description": "Updated {date}",
        }
        # The function renders templates before checking name, so this should work
        result = await create_playlist(sample_tracklist, config, mock_context)
        assert "tracklist" in result


class TestUpdatePlaylist:
    """Tests for update_playlist destination node."""

    @pytest.mark.asyncio
    async def test_update_canonical(self, sample_tracklist, mock_context):
        """Update canonical playlist without connector."""
        from src.application.workflows.destination_nodes import update_playlist

        config = {"playlist_id": "pl-1"}
        result = await update_playlist(sample_tracklist, config, mock_context)

        assert "tracklist" in result

    @pytest.mark.asyncio
    async def test_update_with_connector(self, sample_tracklist, mock_context):
        """Update with connector delegates to connector use case."""
        from src.application.workflows.destination_nodes import update_playlist

        result_mock = MagicMock()
        result_mock.playlist_id = "pl-1"
        result_mock.operations_performed = 1
        result_mock.tracks_added = 2
        result_mock.tracks_removed = 0
        result_mock.tracks_moved = 0
        mock_context["workflow_context"].execute_use_case = AsyncMock(
            return_value=result_mock
        )

        config = {"playlist_id": "sp-123", "connector": "spotify"}
        result = await update_playlist(sample_tracklist, config, mock_context)

        assert "tracklist" in result

    @pytest.mark.asyncio
    async def test_missing_playlist_id_raises(self, sample_tracklist, mock_context):
        """Missing playlist_id raises ValueError."""
        from src.application.workflows.destination_nodes import update_playlist

        with pytest.raises(ValueError, match="playlist_id"):
            await update_playlist(sample_tracklist, {}, mock_context)
