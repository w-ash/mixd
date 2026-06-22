"""Unit tests for RepairUnresolvedEntriesUseCase.

Verifies it hydrates now-mappable unresolved entries via the shared resolution
lookup, is idempotent / a no-op when nothing is mappable, and never *creates*
mappings (that is ResolveMatchReviewUseCase's job — repair only consumes).
"""

from unittest.mock import AsyncMock
from uuid import uuid7

from src.application.use_cases.repair_unresolved_entries import (
    RepairUnresolvedEntriesCommand,
    RepairUnresolvedEntriesUseCase,
)
from src.domain.entities.playlist import ConnectorTrackRef, Playlist, PlaylistEntry
from tests.fixtures import make_mock_uow, make_track


def _unresolved(cid: str, title: str = "Song") -> PlaylistEntry:
    return PlaylistEntry(
        track=None,
        connector_track_ref=ConnectorTrackRef(
            connector_name="spotify", connector_track_identifier=cid, title=title
        ),
    )


def _cmd(playlist_id):
    return RepairUnresolvedEntriesCommand(user_id="u", playlist_id=playlist_id)


def _uow(playlist: Playlist, resolved_map: dict):
    uow = make_mock_uow()
    uow.get_playlist_repository().get_playlist_by_id = AsyncMock(return_value=playlist)
    uow.get_playlist_repository().update_playlist = AsyncMock(return_value=playlist)
    uow.get_connector_repository().find_tracks_by_connectors = AsyncMock(
        return_value=resolved_map
    )
    return uow


class TestRepairUnresolved:
    async def test_hydrates_now_mappable_entry(self) -> None:
        pid = uuid7()
        track = make_track(id=uuid7())
        playlist = Playlist(
            id=pid, name="X", entries=[_unresolved("t1"), _unresolved("t2")]
        )
        uow = _uow(playlist, {("spotify", "t1"): track})

        result = await RepairUnresolvedEntriesUseCase().execute(_cmd(pid), uow)

        assert result.repaired == 1
        assert result.still_unresolved == 1
        update = uow.get_playlist_repository().update_playlist
        update.assert_awaited_once()
        persisted = update.await_args.args[1]
        hydrated = [e for e in persisted.entries if e.track is not None]
        assert len(hydrated) == 1
        # t2 had no mapping → still unresolved.
        assert any(e.track is None for e in persisted.entries)

    async def test_noop_when_no_unresolved(self) -> None:
        pid = uuid7()
        playlist = Playlist(
            id=pid, name="X", entries=[PlaylistEntry(track=make_track())]
        )
        uow = _uow(playlist, {})

        result = await RepairUnresolvedEntriesUseCase().execute(_cmd(pid), uow)

        assert result.repaired == 0
        assert result.still_unresolved == 0
        uow.get_playlist_repository().update_playlist.assert_not_called()

    async def test_idempotent_when_nothing_mappable(self) -> None:
        pid = uuid7()
        playlist = Playlist(id=pid, name="X", entries=[_unresolved("t1")])
        uow = _uow(playlist, {})  # no mapping exists yet

        result = await RepairUnresolvedEntriesUseCase().execute(_cmd(pid), uow)

        assert result.repaired == 0
        assert result.still_unresolved == 1
        uow.get_playlist_repository().update_playlist.assert_not_called()

    async def test_does_not_create_mappings(self) -> None:
        pid = uuid7()
        track = make_track(id=uuid7())
        playlist = Playlist(id=pid, name="X", entries=[_unresolved("t1")])
        uow = _uow(playlist, {("spotify", "t1"): track})

        await RepairUnresolvedEntriesUseCase().execute(_cmd(pid), uow)

        # Mapping creation is match-review's job — repair only consumes.
        uow.get_connector_repository().map_track_to_connector.assert_not_called()
