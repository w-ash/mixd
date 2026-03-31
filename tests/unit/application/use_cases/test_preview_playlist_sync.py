"""Unit tests for PreviewPlaylistSyncUseCase.

Tests both the diff-engine path (locally-cached external exists) and the
fallback path (never-synced link with no comparison data).
"""

from uuid import uuid7

import pytest

from src.application.use_cases.preview_playlist_sync import (
    PreviewPlaylistSyncCommand,
    PreviewPlaylistSyncUseCase,
)
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection, SyncStatus
from src.domain.exceptions import NotFoundError
from tests.fixtures import make_mock_uow, make_playlist, make_tracks

# Stable UUIDs for consistent cross-reference in test helpers
_CANONICAL_PLAYLIST_ID = uuid7()
_LINK_ID = uuid7()


def _make_link(
    *,
    direction: SyncDirection = SyncDirection.PUSH,
) -> PlaylistLink:
    return PlaylistLink(
        id=_LINK_ID,
        playlist_id=_CANONICAL_PLAYLIST_ID,
        connector_name="spotify",
        connector_playlist_identifier="ext123",
        connector_playlist_name="External Playlist",
        sync_direction=direction,
        sync_status=SyncStatus.NEVER_SYNCED,
    )


def _make_uow_with_playlists(
    link: PlaylistLink | None = None,
    canonical_track_count: int = 10,
    external_track_count: int = 7,
    external_exists: bool = True,
) -> object:
    """Set up UoW with canonical and (optionally) cached external playlist.

    Tracks get unique UUIDs from the factory. The diff engine matches by track ID,
    so we build shared tracks for overlap by passing the same list to both playlists.
    """
    link = link or _make_link()
    uow = make_mock_uow()

    uow.get_playlist_link_repository().get_link.return_value = link

    # Build a shared pool: the first min(canonical, external) tracks are shared
    overlap = min(canonical_track_count, external_track_count)
    shared_tracks = make_tracks(count=overlap)

    canonical_extra = make_tracks(count=canonical_track_count - overlap)
    canonical = make_playlist(
        id=_CANONICAL_PLAYLIST_ID,
        name="My Playlist",
        tracks=shared_tracks + canonical_extra,
    )
    uow.get_playlist_repository().get_playlist_by_id.return_value = canonical

    if external_exists:
        external_extra = make_tracks(count=external_track_count - overlap)
        external = make_playlist(
            name="External Playlist",
            tracks=shared_tracks + external_extra,
        )
        uow.get_playlist_repository().get_playlist_by_connector.return_value = external
    else:
        uow.get_playlist_repository().get_playlist_by_connector.return_value = None

    return uow


class TestPreviewPlaylistSyncPush:
    """Preview push sync: canonical is source of truth, external is updated."""

    @pytest.mark.asyncio
    async def test_push_more_canonical_than_external(self):
        """Canonical has 10, external has 7 → 3 to add, 0 to remove, 7 unchanged."""
        uow = _make_uow_with_playlists(canonical_track_count=10, external_track_count=7)

        result = await PreviewPlaylistSyncUseCase().execute(
            PreviewPlaylistSyncCommand(user_id="test-user", link_id=_LINK_ID), uow
        )

        assert result.tracks_to_add == 3
        assert result.tracks_to_remove == 0
        assert result.tracks_unchanged == 7
        assert result.direction == SyncDirection.PUSH
        assert result.connector_name == "spotify"
        assert result.playlist_name == "My Playlist"
        assert result.has_comparison_data is True

    @pytest.mark.asyncio
    async def test_push_fewer_canonical_than_external(self):
        """Canonical has 5, external has 8 → 0 to add, 3 to remove, 5 unchanged."""
        uow = _make_uow_with_playlists(canonical_track_count=5, external_track_count=8)

        result = await PreviewPlaylistSyncUseCase().execute(
            PreviewPlaylistSyncCommand(user_id="test-user", link_id=_LINK_ID), uow
        )

        assert result.tracks_to_add == 0
        assert result.tracks_to_remove == 3
        assert result.tracks_unchanged == 5
        assert result.direction == SyncDirection.PUSH

    @pytest.mark.asyncio
    async def test_push_identical_playlists(self):
        """Same tracks in both → 0 adds, 0 removes, all unchanged."""
        uow = _make_uow_with_playlists(canonical_track_count=5, external_track_count=5)

        result = await PreviewPlaylistSyncUseCase().execute(
            PreviewPlaylistSyncCommand(user_id="test-user", link_id=_LINK_ID), uow
        )

        assert result.tracks_to_add == 0
        assert result.tracks_to_remove == 0
        assert result.tracks_unchanged == 5


class TestPreviewPlaylistSyncPull:
    """Preview pull sync: external is source of truth, canonical is updated."""

    @pytest.mark.asyncio
    async def test_pull_more_external_than_canonical(self):
        """Canonical has 5, external has 8 → 3 to add, 0 to remove, 5 unchanged."""
        link = _make_link(direction=SyncDirection.PULL)
        uow = _make_uow_with_playlists(
            link=link, canonical_track_count=5, external_track_count=8
        )

        result = await PreviewPlaylistSyncUseCase().execute(
            PreviewPlaylistSyncCommand(user_id="test-user", link_id=_LINK_ID), uow
        )

        assert result.tracks_to_add == 3
        assert result.tracks_to_remove == 0
        assert result.tracks_unchanged == 5
        assert result.direction == SyncDirection.PULL

    @pytest.mark.asyncio
    async def test_direction_override_changes_computation(self):
        """Link is push but override to pull reverses the diff direction."""
        link = _make_link(direction=SyncDirection.PUSH)
        uow = _make_uow_with_playlists(
            link=link, canonical_track_count=5, external_track_count=8
        )

        result = await PreviewPlaylistSyncUseCase().execute(
            PreviewPlaylistSyncCommand(
                user_id="test-user",
                link_id=_LINK_ID,
                direction_override=SyncDirection.PULL,
            ),
            uow,
        )

        # Pull: external (8) is source → canonical (5) gets updated
        assert result.tracks_to_add == 3
        assert result.tracks_to_remove == 0
        assert result.tracks_unchanged == 5
        assert result.direction == SyncDirection.PULL


class TestPreviewNeverSynced:
    """Links that have never been synced have no locally-cached external playlist."""

    @pytest.mark.asyncio
    async def test_no_external_cache_returns_no_comparison_data(self):
        uow = _make_uow_with_playlists(external_exists=False)

        result = await PreviewPlaylistSyncUseCase().execute(
            PreviewPlaylistSyncCommand(user_id="test-user", link_id=_LINK_ID), uow
        )

        assert result.has_comparison_data is False
        assert result.direction == SyncDirection.PUSH
        assert result.connector_name == "spotify"
        assert result.playlist_name == "My Playlist"


class TestPreviewSafetyCheck:
    """Safety check integration in preview results."""

    @pytest.mark.asyncio
    async def test_large_removal_flags_safety(self):
        """Push with canonical=5, external=150 → 145 removals flags safety."""
        uow = _make_uow_with_playlists(
            canonical_track_count=5, external_track_count=150
        )

        result = await PreviewPlaylistSyncUseCase().execute(
            PreviewPlaylistSyncCommand(user_id="test-user", link_id=_LINK_ID), uow
        )

        assert result.safety_flagged is True
        assert result.safety_message is not None
        assert "145" in result.safety_message

    @pytest.mark.asyncio
    async def test_small_removal_no_safety_flag(self):
        """Push with canonical=7, external=10 → 3 removals not flagged."""
        uow = _make_uow_with_playlists(canonical_track_count=7, external_track_count=10)

        result = await PreviewPlaylistSyncUseCase().execute(
            PreviewPlaylistSyncCommand(user_id="test-user", link_id=_LINK_ID), uow
        )

        assert result.safety_flagged is False
        assert result.safety_message is None

    @pytest.mark.asyncio
    async def test_no_comparison_data_no_safety_flag(self):
        """Never-synced links can't be destructive — no cached external."""
        uow = _make_uow_with_playlists(external_exists=False)

        result = await PreviewPlaylistSyncUseCase().execute(
            PreviewPlaylistSyncCommand(user_id="test-user", link_id=_LINK_ID), uow
        )

        assert result.safety_flagged is False
        assert result.safety_message is None


class TestPreviewPlaylistSyncErrors:
    """Error handling for preview."""

    @pytest.mark.asyncio
    async def test_link_not_found_raises(self):
        uow = make_mock_uow()
        uow.get_playlist_link_repository().get_link.return_value = None

        with pytest.raises(NotFoundError, match="not found"):
            await PreviewPlaylistSyncUseCase().execute(
                PreviewPlaylistSyncCommand(user_id="test-user", link_id=uuid7()), uow
            )
