"""Tests for the shared resolve_playlist helper.

Validates that playlist resolution works for UUID IDs, connector IDs,
and correctly handles not-found scenarios.
"""

from uuid import uuid7

import pytest

from src.application.use_cases._shared.playlist_resolver import resolve_playlist
from src.domain.exceptions import NotFoundError
from tests.fixtures import make_playlist
from tests.fixtures.mocks import make_mock_uow


@pytest.fixture
def mock_uow():
    """Mock UnitOfWork with playlist repository."""
    return make_mock_uow()


class TestResolvePlaylist:
    """Test resolve_playlist with various ID types and options."""

    async def test_uuid_id_found(self, mock_uow):
        """UUID playlist_id resolves via get_playlist_by_id."""
        playlist = make_playlist(name="Found")
        mock_uow.get_playlist_repository().get_playlist_by_id.return_value = playlist

        result = await resolve_playlist(str(playlist.id), mock_uow, user_id="test-user")

        assert result is playlist
        mock_uow.get_playlist_repository().get_playlist_by_id.assert_awaited_once_with(
            playlist.id, user_id="test-user"
        )

    async def test_uuid_id_not_found_raises(self, mock_uow):
        """UUID ID not found raises NotFoundError after exhausting fallbacks."""
        mock_uow.get_playlist_repository().get_playlist_by_id.side_effect = (
            NotFoundError("not found")
        )
        mock_uow.get_playlist_repository().get_playlist_by_connector.return_value = None

        with pytest.raises(NotFoundError, match="not found"):
            await resolve_playlist(str(uuid7()), mock_uow, user_id="test-user")

    async def test_uuid_id_not_found_returns_none(self, mock_uow):
        """UUID ID not found returns None when raise_if_not_found=False."""
        mock_uow.get_playlist_repository().get_playlist_by_id.side_effect = (
            NotFoundError("not found")
        )
        mock_uow.get_playlist_repository().get_playlist_by_connector.return_value = None

        result = await resolve_playlist(
            str(uuid7()), mock_uow, user_id="test-user", raise_if_not_found=False
        )

        assert result is None

    async def test_string_connector_id_found(self, mock_uow):
        """Non-UUID string resolves via get_playlist_by_connector."""
        playlist = make_playlist(name="Spotify Playlist")
        # UUID("abc") raises ValueError → triggers connector fallback
        mock_uow.get_playlist_repository().get_playlist_by_id.side_effect = ValueError
        mock_uow.get_playlist_repository().get_playlist_by_connector.return_value = (
            playlist
        )

        result = await resolve_playlist(
            "spotify_abc_123", mock_uow, user_id="test-user"
        )

        assert result is playlist
        mock_uow.get_playlist_repository().get_playlist_by_connector.assert_awaited_once_with(
            "spotify", "spotify_abc_123", user_id="test-user", raise_if_not_found=True
        )

    async def test_custom_connector_name(self, mock_uow):
        """Custom connector name is passed through to repository."""
        playlist = make_playlist(name="LastFM Playlist")
        mock_uow.get_playlist_repository().get_playlist_by_id.side_effect = ValueError
        mock_uow.get_playlist_repository().get_playlist_by_connector.return_value = (
            playlist
        )

        result = await resolve_playlist(
            "lfm_xyz", mock_uow, user_id="test-user", connector="lastfm"
        )

        assert result is playlist
        mock_uow.get_playlist_repository().get_playlist_by_connector.assert_awaited_once_with(
            "lastfm", "lfm_xyz", user_id="test-user", raise_if_not_found=True
        )
