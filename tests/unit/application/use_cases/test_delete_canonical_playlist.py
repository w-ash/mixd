"""Unit tests for DeleteCanonicalPlaylistUseCase.

Tests playlist deletion: existence validation, external connection warnings,
force delete, and atomic transaction management.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.use_cases.delete_canonical_playlist import (
    DeleteCanonicalPlaylistCommand,
    DeleteCanonicalPlaylistResult,
    DeleteCanonicalPlaylistUseCase,
)
from src.domain.entities.playlist import Playlist
from src.domain.entities.track import Artist, Track


def _make_playlist(
    playlist_id: int = 1,
    name: str = "Test Playlist",
    connector_ids: dict[str, str] | None = None,
) -> Playlist:
    """Create a test playlist with optional connector IDs."""
    tracks = [Track(id=1, title="Song 1", artists=[Artist(name="Artist")])]
    playlist = Playlist.from_tracklist(
        name=name,
        tracklist=tracks,
        connector_playlist_identifiers=connector_ids or {},
    )
    return playlist.with_id(playlist_id)


@pytest.fixture
def mock_uow():
    """Mock UnitOfWork with playlist repository."""
    uow = AsyncMock()
    playlist_repo = AsyncMock()
    playlist_repo.delete_playlist.return_value = True
    uow.get_playlist_repository = MagicMock(return_value=playlist_repo)
    return uow


@pytest.mark.unit
class TestDeleteCanonicalPlaylistCommand:
    """Test command construction and validation."""

    def test_valid_command(self):
        """Test creating a valid delete command."""
        cmd = DeleteCanonicalPlaylistCommand(playlist_id="42")
        assert cmd.playlist_id == "42"
        assert cmd.force_delete is False

    def test_force_delete_flag(self):
        """Test command with force delete enabled."""
        cmd = DeleteCanonicalPlaylistCommand(playlist_id="42", force_delete=True)
        assert cmd.force_delete is True

    def test_empty_id_rejected(self):
        """Test that empty playlist ID is rejected."""
        with pytest.raises(ValueError):
            DeleteCanonicalPlaylistCommand(playlist_id="")

    def test_command_is_frozen(self):
        """Test command immutability."""
        cmd = DeleteCanonicalPlaylistCommand(playlist_id="1")
        with pytest.raises(AttributeError):
            cmd.playlist_id = "2"


@pytest.mark.unit
class TestDeleteCanonicalPlaylistUseCase:
    """Test use case execution paths."""

    async def test_happy_path_deletes_playlist(self, mock_uow):
        """Test successful deletion of a playlist."""
        playlist = _make_playlist(42, "To Delete")
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = playlist

        command = DeleteCanonicalPlaylistCommand(playlist_id="42")
        use_case = DeleteCanonicalPlaylistUseCase()

        result = await use_case.execute(command, mock_uow)

        assert isinstance(result, DeleteCanonicalPlaylistResult)
        assert result.deleted_playlist_id == 42
        assert result.deleted_playlist_name == "To Delete"
        assert result.tracks_count == 1
        mock_uow.commit.assert_called_once()

    async def test_playlist_not_found_raises(self, mock_uow):
        """Test that deleting non-existent playlist raises ValueError."""
        # _get_playlist first tries int() which succeeds for "999",
        # then calls get_playlist_by_id which should raise
        playlist_repo = mock_uow.get_playlist_repository()
        playlist_repo.get_playlist_by_id.side_effect = ValueError(
            "Playlist with ID 999 not found"
        )
        # Also mock the connector fallback to raise
        playlist_repo.get_playlist_by_connector.return_value = None

        command = DeleteCanonicalPlaylistCommand(playlist_id="999")
        use_case = DeleteCanonicalPlaylistUseCase()

        with pytest.raises(ValueError):
            await use_case.execute(command, mock_uow)

        mock_uow.rollback.assert_called_once()

    async def test_external_connections_generate_warnings(self, mock_uow):
        """Test that playlists with connector IDs produce warnings."""
        playlist = _make_playlist(1, "Connected", {"spotify": "sp_123"})
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = playlist

        command = DeleteCanonicalPlaylistCommand(playlist_id="1")
        use_case = DeleteCanonicalPlaylistUseCase()

        result = await use_case.execute(command, mock_uow)

        assert len(result.warnings) == 1
        assert "external services" in result.warnings[0]
        # Still deletes (proceeds with warning)
        mock_uow.commit.assert_called_once()

    async def test_force_delete_suppresses_warnings(self, mock_uow):
        """Test that force_delete=True skips external connection warnings."""
        playlist = _make_playlist(1, "Connected", {"spotify": "sp_123"})
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = playlist

        command = DeleteCanonicalPlaylistCommand(playlist_id="1", force_delete=True)
        use_case = DeleteCanonicalPlaylistUseCase()

        result = await use_case.execute(command, mock_uow)

        assert len(result.warnings) == 0

    async def test_deletion_failure_raises(self, mock_uow):
        """Test that delete_playlist returning False raises ValueError."""
        playlist = _make_playlist(42, "Phantom")
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = playlist
        mock_uow.get_playlist_repository().delete_playlist.return_value = False

        command = DeleteCanonicalPlaylistCommand(playlist_id="42")
        use_case = DeleteCanonicalPlaylistUseCase()

        with pytest.raises(ValueError, match="Failed to delete"):
            await use_case.execute(command, mock_uow)

        mock_uow.rollback.assert_called_once()

    async def test_result_includes_execution_time(self, mock_uow):
        """Test that result includes non-negative execution time."""
        playlist = _make_playlist(1)
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = playlist

        command = DeleteCanonicalPlaylistCommand(playlist_id="1")
        use_case = DeleteCanonicalPlaylistUseCase()

        result = await use_case.execute(command, mock_uow)

        assert result.execution_time_ms >= 0
