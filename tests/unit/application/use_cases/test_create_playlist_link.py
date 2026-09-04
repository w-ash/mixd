"""Unit tests for CreatePlaylistLinkUseCase."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid7

import pytest

from src.application.connector_protocols import PlaylistConnector
from src.application.use_cases.create_playlist_link import (
    CreatePlaylistLinkCommand,
    CreatePlaylistLinkUseCase,
)
from src.domain.entities.playlist import Playlist
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection, SyncStatus
from src.domain.exceptions import NotFoundError
from tests.fixtures import (
    make_connector_playlist,
    make_mock_connector_provider,
    make_mock_uow,
    make_playlist,
)

# Stable UUID for the default playlist used across tests
_DEFAULT_PLAYLIST_ID = uuid7()


def _make_uow_with_playlist(playlist: Playlist | None = None) -> MagicMock:
    """Create a mock UoW pre-configured with a playlist and connector."""
    playlist = playlist or make_playlist(id=_DEFAULT_PLAYLIST_ID, name="My Playlist")

    # The connector owns identifier parsing; the stub strips a URL path the
    # way the real Spotify override does.
    mock_connector = AsyncMock(spec=PlaylistConnector)
    mock_connector.parse_playlist_identifier.side_effect = lambda raw: raw.rsplit(
        "/", 1
    )[-1]
    mock_connector.get_playlist.return_value = make_connector_playlist(
        connector_name="spotify",
        connector_playlist_identifier="ext123",
        name="External Playlist",
    )
    uow = make_mock_uow(connector_provider=make_mock_connector_provider(mock_connector))

    # Ownership gate: the playlist exists and belongs to the acting user
    uow.get_playlist_repository().is_owned_by.return_value = True

    # Connector playlist repo upserts
    uow.get_connector_playlist_repository().upsert_model.side_effect = lambda cp: cp

    # Link repo creates links
    def create_link(link: PlaylistLink) -> PlaylistLink:
        from attrs import evolve

        return evolve(link, id=uuid7())

    uow.get_playlist_link_repository().create_link.side_effect = create_link

    return uow


class TestCreatePlaylistLinkHappyPath:
    """Creating a new playlist link."""

    @pytest.mark.asyncio
    async def test_creates_link_with_push_direction(self):
        playlist = make_playlist(name="My Playlist")
        uow = _make_uow_with_playlist(playlist)

        result = await CreatePlaylistLinkUseCase().execute(
            CreatePlaylistLinkCommand(
                user_id="test-user",
                playlist_id=playlist.id,
                connector="spotify",
                connector_playlist_identifier="ext123",
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
        playlist = make_playlist(name="My Playlist")
        uow = _make_uow_with_playlist(playlist)

        result = await CreatePlaylistLinkUseCase().execute(
            CreatePlaylistLinkCommand(
                user_id="test-user",
                playlist_id=playlist.id,
                connector="spotify",
                connector_playlist_identifier="ext123",
                sync_direction=SyncDirection.PULL,
            ),
            uow,
        )

        assert result.link.sync_direction == SyncDirection.PULL

    @pytest.mark.asyncio
    async def test_delegates_identifier_parsing_to_the_connector(self):
        playlist = make_playlist(name="My Playlist")
        uow = _make_uow_with_playlist(playlist)
        raw_input = "https://open.spotify.com/playlist/37i9dQZF1DZ06evO05tE88"

        result = await CreatePlaylistLinkUseCase().execute(
            CreatePlaylistLinkCommand(
                user_id="test-user",
                playlist_id=playlist.id,
                connector="spotify",
                connector_playlist_identifier=raw_input,
            ),
            uow,
        )

        mock_connector = uow.get_service_connector_provider().get_connector()
        mock_connector.parse_playlist_identifier.assert_called_once_with(raw_input)
        mock_connector.get_playlist.assert_called_once_with("37i9dQZF1DZ06evO05tE88")
        assert result.link.connector_playlist_identifier == "37i9dQZF1DZ06evO05tE88"

    @pytest.mark.asyncio
    async def test_upserts_connector_playlist(self):
        playlist = make_playlist(name="My Playlist")
        uow = _make_uow_with_playlist(playlist)

        await CreatePlaylistLinkUseCase().execute(
            CreatePlaylistLinkCommand(
                user_id="test-user",
                playlist_id=playlist.id,
                connector="spotify",
                connector_playlist_identifier="ext123",
            ),
            uow,
        )

        uow.get_connector_playlist_repository().upsert_model.assert_called_once()


class TestCreatePlaylistLinkErrors:
    """Error cases for creating playlist links."""

    @pytest.mark.asyncio
    async def test_playlist_not_found_raises(self):
        uow = _make_uow_with_playlist()
        uow.get_playlist_repository().is_owned_by.return_value = False

        with pytest.raises(NotFoundError):
            await CreatePlaylistLinkUseCase().execute(
                CreatePlaylistLinkCommand(
                    user_id="test-user",
                    playlist_id=uuid7(),
                    connector="spotify",
                    connector_playlist_identifier="ext123",
                ),
                uow,
            )

    @pytest.mark.asyncio
    async def test_unknown_connector_raises(self):
        playlist = make_playlist(name="My Playlist")
        uow = _make_uow_with_playlist(playlist)
        uow.get_service_connector_provider().get_connector.side_effect = ValueError(
            "Unknown connector: badservice"
        )

        with pytest.raises(ValueError, match="Unknown connector"):
            await CreatePlaylistLinkUseCase().execute(
                CreatePlaylistLinkCommand(
                    user_id="test-user",
                    playlist_id=playlist.id,
                    connector="badservice",
                    connector_playlist_identifier="ext123",
                ),
                uow,
            )

    @pytest.mark.asyncio
    async def test_external_playlist_not_found_raises(self):
        playlist = make_playlist(name="My Playlist")
        uow = _make_uow_with_playlist(playlist)
        mock_connector = uow.get_service_connector_provider().get_connector()
        mock_connector.get_playlist.side_effect = ValueError(
            "Playlist not found on Spotify"
        )

        with pytest.raises(ValueError, match="Playlist not found"):
            await CreatePlaylistLinkUseCase().execute(
                CreatePlaylistLinkCommand(
                    user_id="test-user",
                    playlist_id=playlist.id,
                    connector="spotify",
                    connector_playlist_identifier="nonexistent",
                ),
                uow,
            )
