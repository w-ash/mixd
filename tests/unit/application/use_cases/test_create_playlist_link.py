"""Unit tests for CreatePlaylistLinkUseCase."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.use_cases.create_playlist_link import (
    CreatePlaylistLinkCommand,
    CreatePlaylistLinkUseCase,
)
from src.domain.entities.playlist import Playlist
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection, SyncStatus
from src.domain.exceptions import NotFoundError
from tests.fixtures import make_connector_playlist, make_mock_uow, make_playlist


def _make_uow_with_playlist(playlist: Playlist | None = None) -> MagicMock:
    """Create a mock UoW pre-configured with a playlist and connector."""
    playlist = playlist or make_playlist(id=42, name="My Playlist")
    uow = make_mock_uow()

    # Playlist repo returns the playlist
    uow.get_playlist_repository().get_playlist_by_id.return_value = playlist

    # Connector provider returns a mock that can fetch playlists
    mock_connector = AsyncMock()
    mock_connector.get_playlist_details = AsyncMock()  # PlaylistConnector check
    mock_connector.get_playlist.return_value = make_connector_playlist(
        connector_name="spotify",
        connector_playlist_identifier="ext123",
        name="External Playlist",
    )
    uow.get_service_connector_provider().get_connector.return_value = mock_connector

    # Connector playlist repo upserts
    uow.get_connector_playlist_repository().upsert_model.side_effect = lambda cp: cp

    # Link repo creates links
    def create_link(link: PlaylistLink) -> PlaylistLink:
        from attrs import evolve

        return evolve(link, id=1)

    uow.get_playlist_link_repository().create_link.side_effect = create_link

    return uow


class TestCreatePlaylistLinkHappyPath:
    """Creating a new playlist link."""

    @pytest.mark.asyncio
    async def test_creates_link_with_push_direction(self):
        uow = _make_uow_with_playlist()

        result = await CreatePlaylistLinkUseCase().execute(
            CreatePlaylistLinkCommand(
                playlist_id=42,
                connector="spotify",
                connector_playlist_id="ext123",
                sync_direction=SyncDirection.PUSH,
            ),
            uow,
        )

        assert result.link.connector_name == "spotify"
        assert result.link.connector_playlist_identifier == "ext123"
        assert result.link.sync_direction == SyncDirection.PUSH
        assert result.link.sync_status == SyncStatus.NEVER_SYNCED

    @pytest.mark.asyncio
    async def test_creates_link_with_pull_direction(self):
        uow = _make_uow_with_playlist()

        result = await CreatePlaylistLinkUseCase().execute(
            CreatePlaylistLinkCommand(
                playlist_id=42,
                connector="spotify",
                connector_playlist_id="ext123",
                sync_direction=SyncDirection.PULL,
            ),
            uow,
        )

        assert result.link.sync_direction == SyncDirection.PULL

    @pytest.mark.asyncio
    async def test_parses_spotify_url(self):
        uow = _make_uow_with_playlist()

        # The connector will be called with the raw ID after URL parsing
        result = await CreatePlaylistLinkUseCase().execute(
            CreatePlaylistLinkCommand(
                playlist_id=42,
                connector="spotify",
                connector_playlist_id="https://open.spotify.com/playlist/37i9dQZF1DZ06evO05tE88",
            ),
            uow,
        )

        # Connector should have been called with the raw ID
        mock_connector = uow.get_service_connector_provider().get_connector()
        mock_connector.get_playlist.assert_called_once_with("37i9dQZF1DZ06evO05tE88")

    @pytest.mark.asyncio
    async def test_upserts_connector_playlist(self):
        uow = _make_uow_with_playlist()

        await CreatePlaylistLinkUseCase().execute(
            CreatePlaylistLinkCommand(
                playlist_id=42,
                connector="spotify",
                connector_playlist_id="ext123",
            ),
            uow,
        )

        uow.get_connector_playlist_repository().upsert_model.assert_called_once()


class TestCreatePlaylistLinkErrors:
    """Error cases for creating playlist links."""

    @pytest.mark.asyncio
    async def test_playlist_not_found_raises(self):
        uow = _make_uow_with_playlist()
        uow.get_playlist_repository().get_playlist_by_id.side_effect = NotFoundError(
            "Not found"
        )
        uow.get_playlist_repository().get_playlist_by_connector.return_value = None

        with pytest.raises(NotFoundError):
            await CreatePlaylistLinkUseCase().execute(
                CreatePlaylistLinkCommand(
                    playlist_id=999,
                    connector="spotify",
                    connector_playlist_id="ext123",
                ),
                uow,
            )

    @pytest.mark.asyncio
    async def test_unknown_connector_raises(self):
        uow = _make_uow_with_playlist()
        uow.get_service_connector_provider().get_connector.side_effect = ValueError(
            "Unknown connector: badservice"
        )

        with pytest.raises(ValueError, match="Unknown connector"):
            await CreatePlaylistLinkUseCase().execute(
                CreatePlaylistLinkCommand(
                    playlist_id=42,
                    connector="badservice",
                    connector_playlist_id="ext123",
                ),
                uow,
            )

    @pytest.mark.asyncio
    async def test_external_playlist_not_found_raises(self):
        uow = _make_uow_with_playlist()
        mock_connector = uow.get_service_connector_provider().get_connector()
        mock_connector.get_playlist.side_effect = ValueError(
            "Playlist not found on Spotify"
        )

        with pytest.raises(ValueError, match="Playlist not found"):
            await CreatePlaylistLinkUseCase().execute(
                CreatePlaylistLinkCommand(
                    playlist_id=42,
                    connector="spotify",
                    connector_playlist_id="nonexistent",
                ),
                uow,
            )
