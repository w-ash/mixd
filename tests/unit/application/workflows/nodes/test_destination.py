"""Tests for destination nodes.

Validates create_playlist and update_playlist behavior with standard
(context, config) node signatures, including idempotent create_playlist
(re-runs update existing playlists instead of creating duplicates).
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid7

import pytest

_PLAYLIST_ID = str(uuid7())


@pytest.fixture
def mock_context(sample_tracklist):
    """Mock workflow context dict for destination nodes.

    Includes upstream tracklist following the standard node contract.
    execute_service returns None by default (no existing playlist found).
    """
    wf_ctx = AsyncMock()
    wf_ctx.use_cases = MagicMock()

    result = MagicMock()
    result.playlist = MagicMock()
    result.playlist.id = _PLAYLIST_ID
    result.playlist.name = "Test"
    wf_ctx.execute_use_case = AsyncMock(return_value=result)

    # Default: no existing playlist found (execute_service calls the lambda with uow)
    wf_ctx.execute_service = AsyncMock(return_value=None)

    return {
        "workflow_context": wf_ctx,
        "upstream_task_id": "src_1",
        "src_1": {"tracklist": sample_tracklist},
    }


class TestCreatePlaylist:
    """Tests for create_playlist destination node."""

    async def test_create_without_connector(self, mock_context):
        """Create canonical playlist without connector."""
        from src.application.workflows.nodes.destination import create_playlist

        config = {"name": "My Playlist", "description": "Test desc"}
        result = await create_playlist(mock_context, config)

        assert "tracklist" in result
        wf = mock_context["workflow_context"]
        wf.execute_use_case.assert_called_once()

    async def test_create_with_connector(self, mock_context):
        """Create playlist with connector delegates to connector use case."""
        from src.application.workflows.nodes.destination import create_playlist

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
        from src.application.workflows.nodes.destination import create_playlist

        with pytest.raises(ValueError, match="name"):
            await create_playlist(mock_context, {})

    async def test_template_rendering(self, mock_context):
        """Template strings in name/description are rendered."""
        from src.application.workflows.nodes.destination import create_playlist

        config = {
            "name": "Playlist ({track_count} tracks)",
            "description": "Updated {date}",
        }
        result = await create_playlist(mock_context, config)
        assert "tracklist" in result

    async def test_create_dry_run_returns_tracklist_without_write(self, mock_context):
        """Dry-run mode skips external write and returns tracklist."""
        from src.application.workflows.nodes.destination import create_playlist

        mock_context["dry_run"] = True
        config = {"name": "My Playlist"}
        result = await create_playlist(mock_context, config)

        assert "tracklist" in result
        mock_context["workflow_context"].execute_use_case.assert_not_called()


class TestUpdatePlaylist:
    """Tests for update_playlist destination node."""

    async def test_update_canonical(self, mock_context):
        """Update playlist without connector."""
        from src.application.workflows.nodes.destination import update_playlist

        config = {"playlist_id": _PLAYLIST_ID}
        result = await update_playlist(mock_context, config)

        assert "tracklist" in result

    async def test_update_with_connector(self, mock_context):
        """Update with connector delegates to connector use case."""
        from src.application.workflows.nodes.destination import update_playlist

        result_mock = MagicMock()
        result_mock.connector_playlist_identifier = "sp-123"
        result_mock.operations_performed = 1
        result_mock.tracks_added = 2
        result_mock.tracks_removed = 0
        result_mock.tracks_moved = 0
        mock_context["workflow_context"].execute_use_case = AsyncMock(
            return_value=result_mock
        )

        config = {
            "playlist_id": "sp-123",
            "connector": "spotify",
        }
        result = await update_playlist(mock_context, config)

        assert "tracklist" in result

    async def test_missing_playlist_id_raises(self, mock_context):
        """Missing playlist_id raises ValueError."""
        from src.application.workflows.nodes.destination import update_playlist

        with pytest.raises(ValueError, match="playlist_id"):
            await update_playlist(mock_context, {})

    async def test_update_dry_run_returns_tracklist_without_write(self, mock_context):
        """Dry-run mode skips external write and returns tracklist."""
        from src.application.workflows.nodes.destination import update_playlist

        mock_context["dry_run"] = True
        config = {"playlist_id": _PLAYLIST_ID}
        result = await update_playlist(mock_context, config)

        assert "tracklist" in result
        mock_context["workflow_context"].execute_use_case.assert_not_called()

    async def test_overwrite_with_zero_tracks_refuses_to_wipe_playlist(
        self, mock_context
    ):
        """Overwrite (append=False) with an empty tracklist raises rather than
        wiping the playlist — the run fails loudly with data intact."""
        from src.application.workflows.nodes.destination import update_playlist
        from src.domain.entities.track import TrackList
        from src.domain.exceptions import EmptyOverwriteError

        mock_context["src_1"] = {"tracklist": TrackList()}  # 0 tracks
        config = {"playlist_id": _PLAYLIST_ID}  # append defaults to overwrite

        with pytest.raises(EmptyOverwriteError, match="0 tracks"):
            await update_playlist(mock_context, config)
        mock_context["workflow_context"].execute_use_case.assert_not_called()

    async def test_append_with_zero_tracks_is_a_noop_not_an_error(self, mock_context):
        """Appending nothing is harmless — must not trip the overwrite guard."""
        from src.application.workflows.nodes.destination import update_playlist
        from src.domain.entities.track import TrackList

        mock_context["src_1"] = {"tracklist": TrackList()}
        config = {"playlist_id": _PLAYLIST_ID, "append": True}

        result = await update_playlist(mock_context, config)
        assert "tracklist" in result

    async def test_empty_overwrite_preview_does_not_raise(self, mock_context):
        """A dry-run preview of a 0-track overwrite shows the empty result
        without raising — only a real write is guarded."""
        from src.application.workflows.nodes.destination import update_playlist
        from src.domain.entities.track import TrackList

        mock_context["dry_run"] = True
        mock_context["src_1"] = {"tracklist": TrackList()}
        config = {"playlist_id": _PLAYLIST_ID}

        result = await update_playlist(mock_context, config)
        assert "tracklist" in result


class TestCreatePlaylistContract:
    """``destination.create_playlist`` always creates a fresh playlist — no
    name-based dedup. Workflows that need idempotent updates against an
    existing connector playlist use ``destination.update_playlist`` with an
    explicit ``connector_playlist_identifier`` (the natural-identity lookup
    against ``playlist_mappings``).
    """

    async def test_create_playlist_does_not_consult_name_dedup(self, mock_context):
        """The node should not perform any name-based lookup via execute_service.
        That hack was removed because names are not a stable identity for
        connector-paired playlists (templated dates, renames, collisions).
        """
        from src.application.workflows.nodes.destination import create_playlist

        config = {"name": "Anything"}
        result = await create_playlist(mock_context, config)

        assert "tracklist" in result
        # No name-dedup query — `_find_existing_playlist_by_name` was deleted.
        mock_context["workflow_context"].execute_service.assert_not_called()
        # The create use case is invoked directly.
        mock_context["workflow_context"].execute_use_case.assert_called_once()

    async def test_dry_run_skips_idempotency_check(self, mock_context):
        """Dry-run mode doesn't check for existing playlists."""
        from src.application.workflows.nodes.destination import create_playlist

        mock_context["dry_run"] = True
        config = {"name": "My Playlist"}
        result = await create_playlist(mock_context, config)

        assert "tracklist" in result
        # execute_service never called in dry-run
        mock_context["workflow_context"].execute_service.assert_not_called()
