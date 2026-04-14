"""Unit tests for ImportSpotifyPlaylistsUseCase.

Critical properties:
- Already-imported playlists with a known snapshot_id short-circuit — the
  connector's `get_playlist` (full-tracks fetch) MUST NOT be called.
- NULL stored snapshot triggers a refetch even if a link exists (pre-
  migration rows can't be trusted as up-to-date).
- Fetch phase is non-atomic: one failing playlist does not abort others;
  the fetch-successful subset then goes through bulk DB writes as one
  batch.
- Unresolved tracks are counted per playlist, not raised.
- Dedup of repeated IDs prevents UNIQUE-constraint violations.
- Every DB write phase is called ONCE with a batch, not N times.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid7

from src.application.use_cases.import_spotify_playlists import (
    ImportSpotifyPlaylistsCommand,
    ImportSpotifyPlaylistsUseCase,
)
from src.domain.entities import ConnectorPlaylist, ConnectorPlaylistItem
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection
from tests.fixtures import make_connector_playlist, make_mock_uow, make_track


def _cp_with_items(
    identifier: str,
    name: str = "A",
    item_ids: list[str] | None = None,
    snapshot_id: str | None = "snap-new",
) -> ConnectorPlaylist:
    items = [
        ConnectorPlaylistItem(connector_track_identifier=tid, position=i)
        for i, tid in enumerate(item_ids or [])
    ]
    return make_connector_playlist(
        connector_playlist_identifier=identifier,
        name=name,
        items=items,
        snapshot_id=snapshot_id,
    )


def _link(identifier: str) -> PlaylistLink:
    return PlaylistLink(
        playlist_id=uuid7(),
        connector_name="spotify",
        connector_playlist_identifier=identifier,
        sync_direction=SyncDirection.PULL,
    )


def _uow_with_connector(get_playlist_return=None):
    """UoW whose provider returns a mock PlaylistConnector.

    `get_playlist` is the full-tracks fetch method whose *absence* in
    call history is load-bearing for the short-circuit test.
    """
    connector = AsyncMock()
    if get_playlist_return is not None:
        connector.get_playlist.return_value = get_playlist_return
    # Satisfy the resolve_playlist_connector protocol check.
    connector.get_playlist_details = AsyncMock()

    provider = MagicMock()
    provider.get_connector.return_value = connector
    uow = make_mock_uow(connector_provider=provider)
    # Mock defaults already echo batch inputs. No extra setup needed.
    return uow, connector


def _cmd(ids, direction=SyncDirection.PULL, user="default"):
    return ImportSpotifyPlaylistsCommand(
        user_id=user,
        connector_playlist_ids=ids,
        sync_direction=direction,
    )


class TestSnapshotShortCircuit:
    """Re-import with a known snapshot must not re-fetch tracks."""

    async def test_existing_link_with_snapshot_skips_fetch(self) -> None:
        uow, connector = _uow_with_connector()
        uow.get_playlist_link_repository().list_by_user_connector.return_value = [
            _link("sp1")
        ]
        uow.get_connector_playlist_repository().list_by_connector.return_value = [
            _cp_with_items("sp1", snapshot_id="snap-old")
        ]

        result = await ImportSpotifyPlaylistsUseCase().execute(_cmd(["sp1"]), uow)

        # Load-bearing negative assertion — no Spotify fetch, no DB writes.
        connector.get_playlist.assert_not_called()
        assert list(result.skipped_unchanged) == ["sp1"]
        assert len(result.succeeded) == 0
        uow.get_connector_playlist_repository().bulk_upsert_models.assert_not_called()
        uow.get_playlist_repository().save_playlists_batch.assert_not_called()
        uow.get_playlist_link_repository().create_links_batch.assert_not_called()

    async def test_null_stored_snapshot_triggers_refetch(self) -> None:
        """NULL snapshot means cache predates snapshot tracking → refetch."""
        cp = _cp_with_items("sp1", item_ids=["t1"], snapshot_id="snap-new")
        uow, connector = _uow_with_connector(get_playlist_return=cp)
        uow.get_playlist_link_repository().list_by_user_connector.return_value = [
            _link("sp1")
        ]
        uow.get_connector_playlist_repository().list_by_connector.return_value = [
            _cp_with_items("sp1", snapshot_id=None)
        ]

        result = await ImportSpotifyPlaylistsUseCase().execute(_cmd(["sp1"]), uow)

        connector.get_playlist.assert_awaited_once_with("sp1")
        assert len(result.skipped_unchanged) == 0
        assert len(result.succeeded) == 1

    async def test_no_existing_link_triggers_fetch(self) -> None:
        cp = _cp_with_items("sp1", item_ids=["t1"])
        uow, connector = _uow_with_connector(get_playlist_return=cp)

        result = await ImportSpotifyPlaylistsUseCase().execute(_cmd(["sp1"]), uow)

        connector.get_playlist.assert_awaited_once()
        assert len(result.succeeded) == 1


class TestBulkWrites:
    """Every DB write phase fires exactly once, with a batch payload."""

    async def test_each_write_phase_called_once(self) -> None:
        cp1 = _cp_with_items("sp1", name="Chill", item_ids=["t1"])
        cp2 = _cp_with_items("sp2", name="Workout", item_ids=["t2"])

        async def fake_get_playlist(pid: str):
            return cp1 if pid == "sp1" else cp2

        uow, connector = _uow_with_connector()
        connector.get_playlist.side_effect = fake_get_playlist
        uow.get_connector_repository().find_tracks_by_connectors.return_value = {
            ("spotify", "t1"): make_track(id=uuid7(), title="T1"),
            ("spotify", "t2"): make_track(id=uuid7(), title="T2"),
        }

        result = await ImportSpotifyPlaylistsUseCase().execute(
            _cmd(["sp1", "sp2"]), uow
        )

        assert len(result.succeeded) == 2
        # Three bulk write calls, one each, regardless of playlist count.
        uow.get_connector_playlist_repository().bulk_upsert_models.assert_awaited_once()
        uow.get_connector_repository().find_tracks_by_connectors.assert_awaited_once()
        uow.get_playlist_repository().save_playlists_batch.assert_awaited_once()
        uow.get_playlist_link_repository().create_links_batch.assert_awaited_once()

        # Each batch payload sized to the fetched subset.
        saved_batch = uow.get_playlist_repository().save_playlists_batch.call_args.args[
            0
        ]
        assert len(saved_batch) == 2
        links_batch = (
            uow.get_playlist_link_repository().create_links_batch.call_args.args[0]
        )
        assert len(links_batch) == 2

    async def test_find_tracks_called_with_union_of_all_connections(self) -> None:
        """Cross-playlist track resolution is one query, not N."""
        cp1 = _cp_with_items("sp1", item_ids=["t1", "t2"])
        cp2 = _cp_with_items("sp2", item_ids=["t3"])

        async def fake_get_playlist(pid: str):
            return cp1 if pid == "sp1" else cp2

        uow, connector = _uow_with_connector()
        connector.get_playlist.side_effect = fake_get_playlist

        _ = await ImportSpotifyPlaylistsUseCase().execute(_cmd(["sp1", "sp2"]), uow)

        call = uow.get_connector_repository().find_tracks_by_connectors.await_args
        connections = call.args[0]
        # Exactly one call, union of all 3 items across both playlists.
        assert set(connections) == {
            ("spotify", "t1"),
            ("spotify", "t2"),
            ("spotify", "t3"),
        }


class TestHappyPath:
    async def test_saves_canonical_playlist_with_resolved_tracks(self) -> None:
        cp = _cp_with_items("sp1", name="Chill", item_ids=["t1", "t2"])
        uow, _ = _uow_with_connector(get_playlist_return=cp)
        track1 = make_track(id=uuid7(), title="T1")
        track2 = make_track(id=uuid7(), title="T2")
        uow.get_connector_repository().find_tracks_by_connectors.return_value = {
            ("spotify", "t1"): track1,
            ("spotify", "t2"): track2,
        }

        result = await ImportSpotifyPlaylistsUseCase().execute(_cmd(["sp1"]), uow)

        assert len(result.succeeded) == 1
        outcome = result.succeeded[0]
        assert outcome.connector_playlist_identifier == "sp1"
        assert outcome.resolved == 2
        assert outcome.unresolved == 0

        batch = uow.get_playlist_repository().save_playlists_batch.call_args.args[0]
        assert len(batch) == 1
        assert batch[0].name == "Chill"
        assert len(batch[0].entries) == 2

    async def test_sync_direction_flows_to_link(self) -> None:
        cp = _cp_with_items("sp1", item_ids=[])
        uow, _ = _uow_with_connector(get_playlist_return=cp)

        _ = await ImportSpotifyPlaylistsUseCase().execute(
            _cmd(["sp1"], direction=SyncDirection.PUSH), uow
        )

        links = uow.get_playlist_link_repository().create_links_batch.call_args.args[0]
        assert links[0].sync_direction == SyncDirection.PUSH

    async def test_unresolved_tracks_counted_not_raised(self) -> None:
        cp = _cp_with_items("sp1", item_ids=["t1", "t2", "t3"])
        uow, _ = _uow_with_connector(get_playlist_return=cp)
        uow.get_connector_repository().find_tracks_by_connectors.return_value = {
            ("spotify", "t1"): make_track(id=uuid7(), title="Only one"),
        }

        result = await ImportSpotifyPlaylistsUseCase().execute(_cmd(["sp1"]), uow)

        outcome = result.succeeded[0]
        assert outcome.resolved == 1
        assert outcome.unresolved == 2

    async def test_commit_called_when_any_succeed(self) -> None:
        cp = _cp_with_items("sp1", item_ids=[])
        uow, _ = _uow_with_connector(get_playlist_return=cp)

        _ = await ImportSpotifyPlaylistsUseCase().execute(_cmd(["sp1"]), uow)

        uow.commit.assert_awaited_once()

    async def test_commit_not_called_when_all_skipped(self) -> None:
        uow, _ = _uow_with_connector()
        uow.get_playlist_link_repository().list_by_user_connector.return_value = [
            _link("sp1")
        ]
        uow.get_connector_playlist_repository().list_by_connector.return_value = [
            _cp_with_items("sp1", snapshot_id="snap-old")
        ]

        result = await ImportSpotifyPlaylistsUseCase().execute(_cmd(["sp1"]), uow)

        uow.commit.assert_not_called()
        assert list(result.skipped_unchanged) == ["sp1"]


class TestNonAtomicFetchPhase:
    """One fetch failure must not abort the batch."""

    async def test_one_failure_among_successes(self) -> None:
        cp_ok = _cp_with_items("good", item_ids=[])

        async def fake_get_playlist(pid: str):
            if pid == "bad":
                raise RuntimeError("Spotify said 404")
            return cp_ok

        uow, connector = _uow_with_connector()
        connector.get_playlist.side_effect = fake_get_playlist

        result = await ImportSpotifyPlaylistsUseCase().execute(
            _cmd(["good", "bad"]), uow
        )

        assert len(result.succeeded) == 1
        assert result.succeeded[0].connector_playlist_identifier == "good"
        assert len(result.failed) == 1
        assert result.failed[0].connector_playlist_identifier == "bad"
        assert "404" in result.failed[0].message
        # The bulk write phases still run for the surviving "good".
        uow.get_playlist_repository().save_playlists_batch.assert_awaited_once()

    async def test_all_failures_skip_bulk_writes_and_commit(self) -> None:
        uow, connector = _uow_with_connector()
        connector.get_playlist.side_effect = RuntimeError("down")

        result = await ImportSpotifyPlaylistsUseCase().execute(_cmd(["a", "b"]), uow)

        assert len(result.failed) == 2
        assert len(result.succeeded) == 0
        uow.get_connector_playlist_repository().bulk_upsert_models.assert_not_called()
        uow.get_playlist_repository().save_playlists_batch.assert_not_called()
        uow.get_playlist_link_repository().create_links_batch.assert_not_called()
        uow.commit.assert_not_called()


class TestDedup:
    async def test_repeated_ids_processed_once(self) -> None:
        cp = _cp_with_items("sp1", item_ids=[])
        uow, connector = _uow_with_connector(get_playlist_return=cp)

        result = await ImportSpotifyPlaylistsUseCase().execute(
            _cmd(["sp1", "sp1", "sp1"]), uow
        )

        connector.get_playlist.assert_awaited_once()
        assert len(result.succeeded) == 1


class TestEmptyBatch:
    async def test_empty_ids_returns_empty_result(self) -> None:
        uow, connector = _uow_with_connector()

        result = await ImportSpotifyPlaylistsUseCase().execute(_cmd([]), uow)

        connector.get_playlist.assert_not_called()
        assert len(result.succeeded) == 0
        assert len(result.skipped_unchanged) == 0
        assert len(result.failed) == 0
        uow.commit.assert_not_called()
