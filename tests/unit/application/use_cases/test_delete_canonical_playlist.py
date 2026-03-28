"""Unit tests for DeleteCanonicalPlaylistUseCase.

Tests playlist deletion: existence validation, external connection warnings,
force delete, and atomic transaction management.
"""

from uuid import uuid7

import pytest

from src.application.use_cases.delete_canonical_playlist import (
    DeleteCanonicalPlaylistCommand,
    DeleteCanonicalPlaylistResult,
    DeleteCanonicalPlaylistUseCase,
)
from tests.fixtures import make_playlist
from tests.fixtures.mocks import make_mock_uow


@pytest.fixture
def mock_uow():
    """Mock UnitOfWork with playlist repository."""
    return make_mock_uow()


class TestDeleteCanonicalPlaylistCommand:
    """Test command construction and validation."""

    def test_valid_command(self):
        """Test creating a valid delete command."""
        cmd = DeleteCanonicalPlaylistCommand(playlist_id="some-id")
        assert cmd.playlist_id == "some-id"
        assert cmd.force_delete is False

    def test_force_delete_flag(self):
        """Test command with force delete enabled."""
        cmd = DeleteCanonicalPlaylistCommand(playlist_id="some-id", force_delete=True)
        assert cmd.force_delete is True

    def test_empty_id_rejected(self):
        """Test that empty playlist ID is rejected."""
        with pytest.raises(ValueError):
            DeleteCanonicalPlaylistCommand(playlist_id="")

    def test_command_is_frozen(self):
        """Test command immutability."""
        cmd = DeleteCanonicalPlaylistCommand(playlist_id="some-id")
        with pytest.raises(AttributeError):
            cmd.playlist_id = "other-id"


class TestDeleteCanonicalPlaylistUseCase:
    """Test use case execution paths."""

    async def test_happy_path_deletes_playlist(self, mock_uow):
        """Test successful deletion of a playlist."""
        playlist = make_playlist(name="To Delete")
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = playlist

        command = DeleteCanonicalPlaylistCommand(playlist_id=str(playlist.id))
        use_case = DeleteCanonicalPlaylistUseCase()

        result = await use_case.execute(command, mock_uow)

        assert isinstance(result, DeleteCanonicalPlaylistResult)
        assert result.deleted_playlist_id == playlist.id
        assert result.deleted_playlist_name == "To Delete"
        assert result.tracks_count == 1
        mock_uow.commit.assert_called_once()

    async def test_playlist_not_found_raises(self, mock_uow):
        """Test that deleting non-existent playlist raises NotFoundError."""
        from src.domain.exceptions import NotFoundError

        playlist_repo = mock_uow.get_playlist_repository()
        playlist_repo.get_playlist_by_id.side_effect = NotFoundError(
            "Playlist not found"
        )
        playlist_repo.get_playlist_by_connector.return_value = None

        fake_id = uuid7()
        command = DeleteCanonicalPlaylistCommand(playlist_id=str(fake_id))
        use_case = DeleteCanonicalPlaylistUseCase()

        with pytest.raises(NotFoundError):
            await use_case.execute(command, mock_uow)

        mock_uow.rollback.assert_called_once()

    async def test_external_connections_generate_warnings(self, mock_uow):
        """Test that playlists with connector IDs produce warnings."""
        playlist = make_playlist(
            name="Connected", connector_playlist_identifiers={"spotify": "sp_123"}
        )
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = playlist

        command = DeleteCanonicalPlaylistCommand(playlist_id=str(playlist.id))
        use_case = DeleteCanonicalPlaylistUseCase()

        result = await use_case.execute(command, mock_uow)

        assert len(result.warnings) == 1
        assert "external services" in result.warnings[0]
        # Still deletes (proceeds with warning)
        mock_uow.commit.assert_called_once()

    async def test_force_delete_suppresses_warnings(self, mock_uow):
        """Test that force_delete=True skips external connection warnings."""
        playlist = make_playlist(
            name="Connected", connector_playlist_identifiers={"spotify": "sp_123"}
        )
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = playlist

        command = DeleteCanonicalPlaylistCommand(
            playlist_id=str(playlist.id), force_delete=True
        )
        use_case = DeleteCanonicalPlaylistUseCase()

        result = await use_case.execute(command, mock_uow)

        assert len(result.warnings) == 0

    async def test_deletion_failure_raises(self, mock_uow):
        """Test that delete_playlist returning False raises ValueError."""
        playlist = make_playlist(name="Phantom")
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = playlist
        mock_uow.get_playlist_repository().delete_playlist.return_value = False

        command = DeleteCanonicalPlaylistCommand(playlist_id=str(playlist.id))
        use_case = DeleteCanonicalPlaylistUseCase()

        with pytest.raises(ValueError, match="Failed to delete"):
            await use_case.execute(command, mock_uow)

        mock_uow.rollback.assert_called_once()

    async def test_result_includes_execution_time(self, mock_uow):
        """Test that result includes non-negative execution time."""
        playlist = make_playlist()
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = playlist

        command = DeleteCanonicalPlaylistCommand(playlist_id=str(playlist.id))
        use_case = DeleteCanonicalPlaylistUseCase()

        result = await use_case.execute(command, mock_uow)

        assert result.execution_time_ms >= 0
