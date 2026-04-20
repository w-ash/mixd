"""Unit tests for ListSpotifyPlaylistsUseCase.

The load-bearing properties are:
- cache-first: zero connector calls when cache is hot and force_refresh=False
- force_refresh bypasses the cache and triggers a fetch + upsert
- import_status resolves correctly for linked vs not-linked playlists
- get_playlist_with_all_tracks is never touched during browse (metadata only)
"""

from unittest.mock import AsyncMock
from uuid import uuid7

import pytest

from src.application.use_cases.list_spotify_playlists import (
    ListSpotifyPlaylistsCommand,
    ListSpotifyPlaylistsUseCase,
)
from src.domain.entities.playlist_assignment import PlaylistAssignment
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection
from tests.fixtures import make_connector_playlist, make_mock_uow


def _cache_cp(name: str, identifier: str, *, snapshot: str | None = None):
    return make_connector_playlist(
        connector_playlist_identifier=identifier,
        name=name,
        snapshot_id=snapshot,
    )


def _link(identifier: str, *, user_id: str = "default"):
    return PlaylistLink(
        playlist_id=uuid7(),
        connector_name="spotify",
        connector_playlist_identifier=identifier,
        sync_direction=SyncDirection.PULL,
    )


def _uow_with_connector(fetch_result=None):
    """UoW whose service connector provider returns a mock Spotify connector."""
    connector = AsyncMock()
    connector.fetch_user_playlists.return_value = fetch_result or []
    # The real `get_playlist_with_all_tracks` must NEVER be called during browse.
    # Attach it as an AsyncMock so the negative assertion in tests is meaningful.
    connector.get_playlist_with_all_tracks = AsyncMock()

    from unittest.mock import MagicMock

    provider = MagicMock()
    provider.get_connector.return_value = connector
    uow = make_mock_uow(connector_provider=provider)
    return uow, connector


class TestCacheBehavior:
    async def test_cache_hit_avoids_connector(self) -> None:
        """Pre-populated cache + force_refresh=False ⇒ zero connector calls."""
        cached = [_cache_cp("Chill Vibes", "s1"), _cache_cp("Workout", "s2")]
        uow, connector = _uow_with_connector(fetch_result=[])
        uow.get_connector_playlist_repository().list_by_connector.return_value = cached

        result = await ListSpotifyPlaylistsUseCase().execute(
            ListSpotifyPlaylistsCommand(user_id="default"), uow
        )

        connector.fetch_user_playlists.assert_not_called()
        # Belt-and-suspenders: the full-tracks path must never fire during browse.
        connector.get_playlist_with_all_tracks.assert_not_called()
        uow.get_connector_playlist_repository().bulk_upsert_models.assert_not_called()
        assert result.from_cache is True
        assert [p.name for p in result.playlists] == ["Chill Vibes", "Workout"]

    async def test_force_refresh_bypasses_cache(self) -> None:
        fetched = [_cache_cp("Fresh", "s9", snapshot="sn-9")]
        uow, connector = _uow_with_connector(fetch_result=fetched)
        # Populate cache to prove force_refresh ignores it.
        uow.get_connector_playlist_repository().list_by_connector.return_value = [
            _cache_cp("Stale", "s1")
        ]

        result = await ListSpotifyPlaylistsUseCase().execute(
            ListSpotifyPlaylistsCommand(user_id="default", force_refresh=True), uow
        )

        connector.fetch_user_playlists.assert_awaited_once()
        uow.get_connector_playlist_repository().bulk_upsert_models.assert_awaited_once()
        uow.commit.assert_awaited_once()
        assert result.from_cache is False
        assert [p.name for p in result.playlists] == ["Fresh"]

    async def test_empty_cache_triggers_fetch(self) -> None:
        """No force_refresh, but empty cache ⇒ fetch + upsert."""
        fetched = [_cache_cp("First Fetch", "s1")]
        uow, connector = _uow_with_connector(fetch_result=fetched)

        result = await ListSpotifyPlaylistsUseCase().execute(
            ListSpotifyPlaylistsCommand(user_id="default"), uow
        )

        connector.fetch_user_playlists.assert_awaited_once()
        assert result.from_cache is False


class TestImportStatus:
    async def test_linked_playlist_marked_imported(self) -> None:
        cached = [_cache_cp("A", "linked"), _cache_cp("B", "orphan")]
        uow, _ = _uow_with_connector()
        uow.get_connector_playlist_repository().list_by_connector.return_value = cached
        uow.get_playlist_link_repository().list_by_user_connector.return_value = [
            _link("linked")
        ]

        result = await ListSpotifyPlaylistsUseCase().execute(
            ListSpotifyPlaylistsCommand(user_id="default"), uow
        )

        statuses = {
            p.connector_playlist_identifier: p.import_status for p in result.playlists
        }
        assert statuses == {"linked": "imported", "orphan": "not_imported"}

    async def test_import_status_scoped_to_requesting_user(self) -> None:
        """Another user's link must not mark the playlist as imported for us."""
        cached = [_cache_cp("A", "linked")]
        uow, _ = _uow_with_connector()
        uow.get_connector_playlist_repository().list_by_connector.return_value = cached

        # Repo was asked with OUR user_id — the mock returns what the caller
        # configured for THIS user. The link_repo default is [] already.
        result = await ListSpotifyPlaylistsUseCase().execute(
            ListSpotifyPlaylistsCommand(user_id="alice"), uow
        )

        assert result.playlists[0].import_status == "not_imported"
        uow.get_playlist_link_repository().list_by_user_connector.assert_awaited_once_with(
            "alice", "spotify"
        )


class TestProjection:
    async def test_preserves_snapshot_id(self) -> None:
        cached = [_cache_cp("A", "s1", snapshot="snap-abc")]
        uow, _ = _uow_with_connector()
        uow.get_connector_playlist_repository().list_by_connector.return_value = cached

        result = await ListSpotifyPlaylistsUseCase().execute(
            ListSpotifyPlaylistsCommand(user_id="default"), uow
        )

        assert result.playlists[0].snapshot_id == "snap-abc"

    async def test_track_count_from_raw_metadata(self) -> None:
        """Browse-path playlists store total_tracks in raw_metadata, not items."""
        cp = make_connector_playlist(
            connector_playlist_identifier="s1",
            name="A",
            raw_metadata={"total_tracks": 247, "images": []},
        )
        uow, _ = _uow_with_connector()
        uow.get_connector_playlist_repository().list_by_connector.return_value = [cp]

        result = await ListSpotifyPlaylistsUseCase().execute(
            ListSpotifyPlaylistsCommand(user_id="default"), uow
        )

        assert result.playlists[0].track_count == 247

    async def test_image_url_extracted_defensively(self) -> None:
        cp = make_connector_playlist(
            connector_playlist_identifier="s1",
            name="A",
            raw_metadata={
                "images": [{"url": "https://i.example/1.jpg", "height": 640}]
            },
        )
        uow, _ = _uow_with_connector()
        uow.get_connector_playlist_repository().list_by_connector.return_value = [cp]

        result = await ListSpotifyPlaylistsUseCase().execute(
            ListSpotifyPlaylistsCommand(user_id="default"), uow
        )

        assert result.playlists[0].image_url == "https://i.example/1.jpg"

    @pytest.mark.parametrize(
        "raw_metadata",
        [
            {},
            {"images": []},
            {"images": "not-a-list"},
            {"images": [{"no_url": "x"}]},
        ],
    )
    async def test_missing_or_malformed_images_yield_none(self, raw_metadata) -> None:
        cp = make_connector_playlist(
            connector_playlist_identifier="s1",
            name="A",
            raw_metadata=raw_metadata,
        )
        uow, _ = _uow_with_connector()
        uow.get_connector_playlist_repository().list_by_connector.return_value = [cp]

        result = await ListSpotifyPlaylistsUseCase().execute(
            ListSpotifyPlaylistsCommand(user_id="default"), uow
        )

        assert result.playlists[0].image_url is None


class TestCurrentAssignments:
    async def test_active_assignments_surface_on_matching_row(self) -> None:
        cp_a = _cache_cp("A", "s1")
        cp_b = _cache_cp("B", "s2")
        assignment = PlaylistAssignment.create(
            user_id="default",
            connector_playlist_id=cp_a.id,
            action_type="add_tag",
            raw_action_value="mood:chill",
        )

        uow, _ = _uow_with_connector()
        uow.get_connector_playlist_repository().list_by_connector.return_value = [
            cp_a,
            cp_b,
        ]
        uow.get_playlist_assignment_repository().list_for_connector_playlist_ids.side_effect = (
            lambda ids, **kw: {
                cp_a.id: [assignment],
            }
        )

        result = await ListSpotifyPlaylistsUseCase().execute(
            ListSpotifyPlaylistsCommand(user_id="default"), uow
        )

        by_id = {p.connector_playlist_identifier: p for p in result.playlists}
        assert len(by_id["s1"].current_assignments) == 1
        assert by_id["s1"].current_assignments[0].action_value == "mood:chill"
        assert by_id["s2"].current_assignments == []
