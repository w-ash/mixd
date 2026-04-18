"""Unit tests for RefreshConnectorPlaylistsUseCase: snapshot skip,
failure isolation, connector resolution, no-work paths."""

from src.application.use_cases.refresh_connector_playlists import (
    RefreshConnectorPlaylistsCommand,
    RefreshConnectorPlaylistsUseCase,
)
from tests.fixtures import make_connector_playlist, make_mock_uow_with_connector


def _cp(
    identifier: str,
    name: str = "A",
    snapshot_id: str | None = "snap",
):
    return make_connector_playlist(
        connector_playlist_identifier=identifier,
        name=name,
        items=[],
        snapshot_id=snapshot_id,
    )


def _cmd(ids, connector_name="spotify", user="default"):
    return RefreshConnectorPlaylistsCommand(
        user_id=user,
        connector_name=connector_name,
        connector_playlist_ids=ids,
    )


class TestSnapshotShortCircuit:
    async def test_cached_snapshot_skips_fetch(self) -> None:
        uow, connector = make_mock_uow_with_connector()
        uow.get_connector_playlist_repository().list_by_connector.return_value = [
            _cp("sp1", snapshot_id="snap-existing")
        ]

        result = await RefreshConnectorPlaylistsUseCase().execute(_cmd(["sp1"]), uow)

        connector.get_playlist.assert_not_called()
        assert list(result.skipped_unchanged) == ["sp1"]
        assert len(result.succeeded) == 0
        uow.commit.assert_not_called()

    async def test_null_snapshot_triggers_fetch(self) -> None:
        cp = _cp("sp1", snapshot_id="fresh")
        uow, connector = make_mock_uow_with_connector(get_playlist_return=cp)
        uow.get_connector_playlist_repository().list_by_connector.return_value = [
            _cp("sp1", snapshot_id=None)
        ]

        result = await RefreshConnectorPlaylistsUseCase().execute(_cmd(["sp1"]), uow)

        connector.get_playlist.assert_awaited_once_with("sp1")
        assert len(result.succeeded) == 1
        assert result.succeeded[0].connector_playlist_identifier == "sp1"
        uow.commit.assert_awaited_once()


class TestFailureIsolation:
    async def test_one_failure_others_succeed(self) -> None:
        async def fake_get_playlist(pid: str):
            if pid == "bad":
                raise RuntimeError("404")
            return _cp(pid)

        uow, connector = make_mock_uow_with_connector()
        connector.get_playlist.side_effect = fake_get_playlist

        result = await RefreshConnectorPlaylistsUseCase().execute(
            _cmd(["good", "bad"]), uow
        )

        assert len(result.succeeded) == 1
        assert result.succeeded[0].connector_playlist_identifier == "good"
        assert len(result.failed) == 1
        assert result.failed[0].connector_playlist_identifier == "bad"
        assert "404" in result.failed[0].message


class TestConnectorThreading:
    async def test_connector_name_resolves_to_provider(self) -> None:
        cp = _cp("sp1")
        uow, _ = make_mock_uow_with_connector(get_playlist_return=cp)

        _ = await RefreshConnectorPlaylistsUseCase().execute(
            _cmd(["sp1"], connector_name="spotify"), uow
        )

        uow.get_service_connector_provider().get_connector.assert_called_with("spotify")


class TestNoWork:
    async def test_empty_ids_returns_empty_result(self) -> None:
        uow, connector = make_mock_uow_with_connector()

        result = await RefreshConnectorPlaylistsUseCase().execute(_cmd([]), uow)

        connector.get_playlist.assert_not_called()
        assert len(result.succeeded) == 0
        assert len(result.skipped_unchanged) == 0
        assert len(result.failed) == 0
        uow.commit.assert_not_called()

    async def test_dedup_repeated_ids(self) -> None:
        cp = _cp("sp1")
        uow, connector = make_mock_uow_with_connector(get_playlist_return=cp)

        result = await RefreshConnectorPlaylistsUseCase().execute(
            _cmd(["sp1", "sp1", "sp1"]), uow
        )

        connector.get_playlist.assert_awaited_once()
        assert len(result.succeeded) == 1
