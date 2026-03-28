"""Unit tests for SyncPlaylistLinkUseCase."""

from unittest.mock import AsyncMock, patch
from uuid import uuid7

import pytest

from src.application.use_cases.sync_playlist_link import (
    SyncPlaylistLinkCommand,
    SyncPlaylistLinkUseCase,
)
from src.application.use_cases.update_connector_playlist import (
    UpdateConnectorPlaylistResult,
)
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection, SyncStatus
from src.domain.exceptions import ConfirmationRequiredError, NotFoundError
from tests.fixtures import make_mock_uow, make_playlist, make_tracks

# Stable UUIDs for consistent cross-reference in test helpers
_PLAYLIST_ID = uuid7()
_LINK_ID = uuid7()


def _make_link(
    *,
    direction: SyncDirection = SyncDirection.PUSH,
    status: SyncStatus = SyncStatus.NEVER_SYNCED,
) -> PlaylistLink:
    return PlaylistLink(
        id=_LINK_ID,
        playlist_id=_PLAYLIST_ID,
        connector_name="spotify",
        connector_playlist_identifier="ext123",
        connector_playlist_name="External Playlist",
        sync_direction=direction,
        sync_status=status,
    )


def _make_uow_with_link(link: PlaylistLink | None = None) -> AsyncMock:
    link = link or _make_link()
    uow = make_mock_uow()

    link_repo = uow.get_playlist_link_repository()
    link_repo.get_link.return_value = link
    link_repo.update_sync_status.return_value = None

    playlist = make_playlist(id=_PLAYLIST_ID, name="My Playlist")
    uow.get_playlist_repository().get_playlist_by_id.return_value = playlist

    return uow


class TestSyncPlaylistLinkPush:
    """Push sync (canonical → external)."""

    @pytest.mark.asyncio
    async def test_push_sync_success(self):
        uow = _make_uow_with_link()

        # After sync completes, re-fetch returns the updated link
        updated_link = _make_link(status=SyncStatus.SYNCED)
        uow.get_playlist_link_repository().get_link.side_effect = [
            _make_link(),  # Initial fetch
            updated_link,  # Re-fetch after sync
        ]

        mock_push_result = UpdateConnectorPlaylistResult(
            playlist_id="ext123",
            connector="spotify",
            tracks_added=3,
            tracks_removed=1,
        )

        with patch.object(
            SyncPlaylistLinkUseCase, "_push_sync", new_callable=AsyncMock
        ) as mock_push:
            from src.application.use_cases.sync_playlist_link import (
                SyncPlaylistLinkResult,
            )

            mock_push.return_value = SyncPlaylistLinkResult(
                link=_make_link(),
                tracks_added=3,
                tracks_removed=1,
            )

            result = await SyncPlaylistLinkUseCase().execute(
                SyncPlaylistLinkCommand(link_id=_LINK_ID), uow
            )

            assert result.tracks_added == 3
            assert result.tracks_removed == 1
            mock_push.assert_called_once()


class TestSyncPlaylistLinkPull:
    """Pull sync (external → canonical)."""

    @pytest.mark.asyncio
    async def test_pull_sync_via_direction_override(self):
        uow = _make_uow_with_link(_make_link(direction=SyncDirection.PUSH))

        updated_link = _make_link(status=SyncStatus.SYNCED)
        uow.get_playlist_link_repository().get_link.side_effect = [
            _make_link(),
            updated_link,
        ]

        with patch.object(
            SyncPlaylistLinkUseCase, "_pull_sync", new_callable=AsyncMock
        ) as mock_pull:
            from src.application.use_cases.sync_playlist_link import (
                SyncPlaylistLinkResult,
            )

            mock_pull.return_value = SyncPlaylistLinkResult(
                link=_make_link(),
                tracks_added=5,
                tracks_removed=0,
            )

            result = await SyncPlaylistLinkUseCase().execute(
                SyncPlaylistLinkCommand(
                    link_id=_LINK_ID,
                    direction_override=SyncDirection.PULL,
                ),
                uow,
            )

            assert result.tracks_added == 5
            mock_pull.assert_called_once()


class TestSyncPlaylistLinkErrors:
    """Error handling during sync."""

    @pytest.mark.asyncio
    async def test_link_not_found_raises(self):
        uow = make_mock_uow()
        uow.get_playlist_link_repository().get_link.return_value = None

        with pytest.raises(NotFoundError, match="not found"):
            await SyncPlaylistLinkUseCase().execute(
                SyncPlaylistLinkCommand(link_id=uuid7()), uow
            )

    @pytest.mark.asyncio
    async def test_sync_error_updates_status(self):
        uow = _make_uow_with_link()

        uow.get_playlist_link_repository().get_link.side_effect = [
            _make_link(),  # Initial fetch
        ]

        with patch.object(
            SyncPlaylistLinkUseCase, "_push_sync", new_callable=AsyncMock
        ) as mock_push:
            mock_push.side_effect = RuntimeError("Spotify API error")

            with pytest.raises(RuntimeError, match="Spotify API error"):
                await SyncPlaylistLinkUseCase().execute(
                    SyncPlaylistLinkCommand(link_id=_LINK_ID), uow
                )

            # Verify error status was set
            link_repo = uow.get_playlist_link_repository()
            # First call: SYNCING, last call: ERROR
            calls = link_repo.update_sync_status.call_args_list
            assert calls[0].args == (_LINK_ID, SyncStatus.SYNCING)
            assert calls[-1].args == (_LINK_ID, SyncStatus.ERROR)
            assert "Spotify API error" in str(calls[-1].kwargs.get("error", ""))


def _make_uow_with_safety_setup(
    *,
    canonical_track_count: int = 5,
    external_track_count: int = 150,
    external_cached: bool = True,
) -> AsyncMock:
    """Set up UoW for safety check tests with configurable playlist sizes."""
    link = _make_link(direction=SyncDirection.PUSH)
    uow = make_mock_uow()

    link_repo = uow.get_playlist_link_repository()
    updated_link = _make_link(status=SyncStatus.SYNCED)
    link_repo.get_link.side_effect = [link, updated_link]
    link_repo.update_sync_status.return_value = None

    # Build shared tracks for overlap so the diff engine can match by track ID
    overlap = min(canonical_track_count, external_track_count)
    shared_tracks = make_tracks(count=overlap)

    canonical_extra = make_tracks(count=canonical_track_count - overlap)
    canonical = make_playlist(
        id=_PLAYLIST_ID,
        name="My Playlist",
        tracks=shared_tracks + canonical_extra,
    )
    playlist_repo = uow.get_playlist_repository()
    playlist_repo.get_playlist_by_id.return_value = canonical

    if external_cached:
        external_extra = make_tracks(count=external_track_count - overlap)
        external = make_playlist(
            name="External Playlist",
            tracks=shared_tracks + external_extra,
        )
        playlist_repo.get_playlist_by_connector.return_value = external
    else:
        playlist_repo.get_playlist_by_connector.return_value = None

    return uow


class TestSyncPlaylistLinkSafetyCheck:
    """Safety check gates destructive push syncs."""

    @pytest.mark.asyncio
    async def test_push_blocked_without_confirmation(self):
        """Destructive push (145 removals) raises ConfirmationRequiredError."""
        uow = _make_uow_with_safety_setup(
            canonical_track_count=5, external_track_count=150
        )

        with pytest.raises(ConfirmationRequiredError) as exc_info:
            await SyncPlaylistLinkUseCase().execute(
                SyncPlaylistLinkCommand(link_id=_LINK_ID, confirmed=False), uow
            )

        assert exc_info.value.removals == 145
        assert exc_info.value.total == 150
        assert exc_info.value.remaining == 5

    @pytest.mark.asyncio
    async def test_push_proceeds_with_confirmation(self):
        """Same destructive diff but confirmed=True bypasses safety."""
        uow = _make_uow_with_safety_setup(
            canonical_track_count=5, external_track_count=150
        )

        with patch.object(
            SyncPlaylistLinkUseCase, "_push_sync", wraps=None, new_callable=AsyncMock
        ) as mock_push:
            from src.application.use_cases.sync_playlist_link import (
                SyncPlaylistLinkResult,
            )

            mock_push.return_value = SyncPlaylistLinkResult(
                link=_make_link(), tracks_added=0, tracks_removed=145
            )

            result = await SyncPlaylistLinkUseCase().execute(
                SyncPlaylistLinkCommand(link_id=_LINK_ID, confirmed=True), uow
            )

            assert result.tracks_removed == 145
            mock_push.assert_called_once()

    @pytest.mark.asyncio
    async def test_push_no_cached_external_skips_check(self):
        """First sync (no cached external) proceeds without confirmation."""
        uow = _make_uow_with_safety_setup(external_cached=False)

        with patch.object(
            SyncPlaylistLinkUseCase, "_push_sync", wraps=None, new_callable=AsyncMock
        ) as mock_push:
            from src.application.use_cases.sync_playlist_link import (
                SyncPlaylistLinkResult,
            )

            mock_push.return_value = SyncPlaylistLinkResult(
                link=_make_link(), tracks_added=5, tracks_removed=0
            )

            result = await SyncPlaylistLinkUseCase().execute(
                SyncPlaylistLinkCommand(link_id=_LINK_ID, confirmed=False), uow
            )

            assert result.tracks_added == 5

    @pytest.mark.asyncio
    async def test_pull_not_affected_by_safety_check(self):
        """Pull direction bypasses safety check entirely."""
        link = _make_link(direction=SyncDirection.PULL)
        uow = make_mock_uow()
        link_repo = uow.get_playlist_link_repository()
        updated_link = _make_link(
            direction=SyncDirection.PULL, status=SyncStatus.SYNCED
        )
        link_repo.get_link.side_effect = [link, updated_link]
        link_repo.update_sync_status.return_value = None

        with patch.object(
            SyncPlaylistLinkUseCase, "_pull_sync", new_callable=AsyncMock
        ) as mock_pull:
            from src.application.use_cases.sync_playlist_link import (
                SyncPlaylistLinkResult,
            )

            mock_pull.return_value = SyncPlaylistLinkResult(
                link=link, tracks_added=100, tracks_removed=0
            )

            result = await SyncPlaylistLinkUseCase().execute(
                SyncPlaylistLinkCommand(link_id=_LINK_ID, confirmed=False), uow
            )

            assert result.tracks_added == 100
            mock_pull.assert_called_once()
