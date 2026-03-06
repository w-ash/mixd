"""Tests for destination nodes.

Validates create_playlist and update_playlist behavior with standard
(context, config) node signatures.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_context(sample_tracklist):
    """Mock workflow context dict for destination nodes.

    Includes upstream tracklist following the standard node contract.
    """
    wf_ctx = AsyncMock()
    wf_ctx.use_cases = MagicMock()

    result = MagicMock()
    result.playlist = MagicMock()
    result.playlist.id = "pl-1"
    result.playlist.name = "Test"
    wf_ctx.execute_use_case = AsyncMock(return_value=result)

    return {
        "workflow_context": wf_ctx,
        "upstream_task_id": "src_1",
        "src_1": {"tracklist": sample_tracklist},
    }


class TestCreatePlaylist:
    """Tests for create_playlist destination node."""

    async def test_create_without_connector(self, mock_context):
        """Create canonical playlist without connector."""
        from src.application.workflows.destination_nodes import create_playlist

        config = {"name": "My Playlist", "description": "Test desc"}
        result = await create_playlist(mock_context, config)

        assert "tracklist" in result
        wf = mock_context["workflow_context"]
        wf.execute_use_case.assert_called_once()

    async def test_create_with_connector(self, mock_context):
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
        result = await create_playlist(mock_context, config)

        assert "tracklist" in result

    async def test_missing_name_raises(self, mock_context):
        """Missing name raises ValueError."""
        from src.application.workflows.destination_nodes import create_playlist

        with pytest.raises(ValueError, match="name"):
            await create_playlist(mock_context, {})

    async def test_template_rendering(self, mock_context):
        """Template strings in name/description are rendered."""
        from src.application.workflows.destination_nodes import create_playlist

        config = {
            "name": "Playlist ({track_count} tracks)",
            "description": "Updated {date}",
        }
        result = await create_playlist(mock_context, config)
        assert "tracklist" in result


    async def test_create_dry_run_returns_tracklist_without_write(self, mock_context):
        """Dry-run mode skips external write and returns tracklist."""
        from src.application.workflows.destination_nodes import create_playlist

        mock_context["dry_run"] = True
        config = {"name": "My Playlist"}
        result = await create_playlist(mock_context, config)

        assert "tracklist" in result
        mock_context["workflow_context"].execute_use_case.assert_not_called()


class TestUpdatePlaylist:
    """Tests for update_playlist destination node."""

    async def test_update_canonical(self, mock_context):
        """Update canonical playlist without connector."""
        from src.application.workflows.destination_nodes import update_playlist

        config = {"playlist_id": "pl-1"}
        result = await update_playlist(mock_context, config)

        assert "tracklist" in result

    async def test_update_with_connector(self, mock_context):
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
        result = await update_playlist(mock_context, config)

        assert "tracklist" in result

    async def test_missing_playlist_id_raises(self, mock_context):
        """Missing playlist_id raises ValueError."""
        from src.application.workflows.destination_nodes import update_playlist

        with pytest.raises(ValueError, match="playlist_id"):
            await update_playlist(mock_context, {})

    async def test_update_dry_run_returns_tracklist_without_write(self, mock_context):
        """Dry-run mode skips external write and returns tracklist."""
        from src.application.workflows.destination_nodes import update_playlist

        mock_context["dry_run"] = True
        config = {"playlist_id": "pl-1"}
        result = await update_playlist(mock_context, config)

        assert "tracklist" in result
        mock_context["workflow_context"].execute_use_case.assert_not_called()
