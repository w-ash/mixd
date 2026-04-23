"""Unit tests for ImportConnectorPlaylistsAsCanonicalUseCase.

Verifies skip-when-linked, create path (refresh + canonical + link),
update path on legacy backup state, failure isolation across phases.
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid7

from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistResult,
)
from src.application.use_cases.import_connector_playlist_as_canonical import (
    ImportConnectorPlaylistsAsCanonicalCommand,
    ImportConnectorPlaylistsAsCanonicalUseCase,
)
from src.application.use_cases.update_canonical_playlist import (
    UpdateCanonicalPlaylistResult,
)
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection
from tests.fixtures import (
    make_connector_playlist,
    make_mock_metric_config,
    make_mock_uow_with_connector,
    make_playlist,
    make_track,
)


def _cp(identifier: str, name: str = "Chill", snapshot_id: str | None = "snap"):
    return make_connector_playlist(
        connector_playlist_identifier=identifier,
        name=name,
        items=[],
        snapshot_id=snapshot_id,
    )


def _link(identifier: str) -> PlaylistLink:
    return PlaylistLink(
        playlist_id=uuid7(),
        connector_name="spotify",
        connector_playlist_identifier=identifier,
        sync_direction=SyncDirection.PULL,
    )


def _cmd(ids, user="default", *, force=False):
    return ImportConnectorPlaylistsAsCanonicalCommand(
        user_id=user,
        connector_name="spotify",
        connector_playlist_ids=ids,
        sync_direction=SyncDirection.PULL,
        force=force,
    )


def _use_case() -> ImportConnectorPlaylistsAsCanonicalUseCase:
    return ImportConnectorPlaylistsAsCanonicalUseCase(
        metric_config=make_mock_metric_config()
    )


def _create_result(name: str = "Chill") -> CreateCanonicalPlaylistResult:
    return CreateCanonicalPlaylistResult(
        playlist=make_playlist(id=uuid7(), name=name, tracks=[make_track()]),
        tracks_created=1,
    )


def _update_result(name: str = "Chill") -> UpdateCanonicalPlaylistResult:
    playlist = make_playlist(
        id=uuid7(),
        name=name,
        tracks=[make_track(), make_track()],
    )
    return UpdateCanonicalPlaylistResult(playlist=playlist)


_UPSERT_PATCH = (
    "src.application.use_cases.import_connector_playlist_as_canonical."
    "upsert_canonical_playlist"
)


class TestSkipWhenLinked:
    async def test_link_and_snapshot_fully_skip(self) -> None:
        uow, connector = make_mock_uow_with_connector()
        uow.get_playlist_link_repository().list_by_user_connector.return_value = [
            _link("sp1")
        ]
        uow.get_connector_playlist_repository().list_by_connector.return_value = [
            _cp("sp1", snapshot_id="cached-snap")
        ]

        with patch(_UPSERT_PATCH, new=AsyncMock()) as upsert_mock:
            result = await _use_case().execute(_cmd(["sp1"]), uow)

        connector.get_playlist.assert_not_called()
        upsert_mock.assert_not_called()
        uow.get_playlist_link_repository().create_links_batch.assert_not_called()
        assert list(result.skipped_unchanged) == ["sp1"]
        assert len(result.succeeded) == 0
        uow.commit.assert_not_called()

    async def test_fresh_cache_no_link_still_creates_canonical(self) -> None:
        """Regression: fresh connector_playlists cache + no existing
        PlaylistLinks must still produce canonical Playlists.

        This was the prod v0.7.5 shape (483 cached connector_playlists,
        0 playlist_mappings). The pre-CQS-split code routed every id
        through a cache-skip branch and returned succeeded=[],
        skipped_unchanged=[N] — the UI showed "N unchanged" and nothing
        was persisted. The Query path makes that failure unrepresentable:
        get_current_connector_playlists always returns the playlist data,
        so the canonical-upsert loop runs for every resolved id.
        """
        uow, connector = make_mock_uow_with_connector()
        uow.get_playlist_link_repository().list_by_user_connector.return_value = []
        uow.get_connector_playlist_repository().list_by_connector.return_value = [
            _cp("sp1", name="Chill", snapshot_id="cached-snap"),
            _cp("sp2", name="Mellow", snapshot_id="cached-snap"),
            _cp("sp3", name="Drive", snapshot_id="cached-snap"),
        ]

        with patch(_UPSERT_PATCH, new=AsyncMock(return_value=_create_result())):
            result = await _use_case().execute(_cmd(["sp1", "sp2", "sp3"]), uow)

        connector.get_playlist.assert_not_called()  # cache was fresh
        assert len(result.succeeded) == 3
        assert list(result.skipped_unchanged) == []
        assert len(result.failed) == 0

        link_repo = uow.get_playlist_link_repository()
        link_repo.create_links_batch.assert_awaited_once()
        created_links = link_repo.create_links_batch.call_args.args[0]
        assert [link.connector_playlist_identifier for link in created_links] == [
            "sp1",
            "sp2",
            "sp3",
        ]
        uow.commit.assert_awaited_once()


class TestForce:
    """``force=True`` re-fetches even when the link + cache would normally skip."""

    async def test_force_bypasses_link_and_snapshot_short_circuit(self) -> None:
        """Pre-existing link + fresh cache: force=True should still re-fetch."""
        cp = _cp("sp1", snapshot_id="fresh")
        uow, connector = make_mock_uow_with_connector(get_playlist_return=cp)
        uow.get_playlist_link_repository().list_by_user_connector.return_value = [
            _link("sp1")
        ]
        uow.get_connector_playlist_repository().list_by_connector.return_value = [
            _cp("sp1", snapshot_id="cached-snap")
        ]

        with patch(_UPSERT_PATCH, new=AsyncMock(return_value=_create_result("Chill"))):
            result = await _use_case().execute(_cmd(["sp1"], force=True), uow)

        connector.get_playlist.assert_awaited_once_with("sp1")
        assert len(result.succeeded) == 1
        assert list(result.skipped_unchanged) == []

    async def test_force_false_preserves_existing_skip_behavior(self) -> None:
        """Default force=False keeps the v0.7.5 skip semantics intact."""
        uow, connector = make_mock_uow_with_connector()
        uow.get_playlist_link_repository().list_by_user_connector.return_value = [
            _link("sp1")
        ]
        uow.get_connector_playlist_repository().list_by_connector.return_value = [
            _cp("sp1", snapshot_id="cached-snap")
        ]

        with patch(_UPSERT_PATCH, new=AsyncMock()) as upsert_mock:
            result = await _use_case().execute(_cmd(["sp1"], force=False), uow)

        connector.get_playlist.assert_not_called()
        upsert_mock.assert_not_called()
        assert list(result.skipped_unchanged) == ["sp1"]


class TestCreatePath:
    async def test_new_playlist_creates_canonical_and_link(self) -> None:
        cp = _cp("sp1", name="Chill")
        uow, connector = make_mock_uow_with_connector(get_playlist_return=cp)

        with patch(_UPSERT_PATCH, new=AsyncMock(return_value=_create_result("Chill"))):
            result = await _use_case().execute(_cmd(["sp1"]), uow)

        connector.get_playlist.assert_awaited_once_with("sp1")
        assert len(result.succeeded) == 1
        outcome = result.succeeded[0]
        assert outcome.connector_playlist_identifier == "sp1"
        assert outcome.resolved == 1

        uow.get_playlist_link_repository().create_links_batch.assert_awaited_once()
        created_links = (
            uow.get_playlist_link_repository().create_links_batch.call_args.args[0]
        )
        assert len(created_links) == 1
        link = created_links[0]
        assert link.connector_name == "spotify"
        assert link.connector_playlist_identifier == "sp1"
        assert link.sync_direction == SyncDirection.PULL

        uow.commit.assert_awaited_once()


class TestUpdatePath:
    async def test_existing_canonical_without_link_creates_link_and_updates(
        self,
    ) -> None:
        cp = _cp("sp1", snapshot_id="fresh")
        uow, connector = make_mock_uow_with_connector(get_playlist_return=cp)
        uow.get_connector_playlist_repository().list_by_connector.return_value = [
            _cp("sp1", snapshot_id=None)
        ]

        with patch(_UPSERT_PATCH, new=AsyncMock(return_value=_update_result())):
            result = await _use_case().execute(_cmd(["sp1"]), uow)

        connector.get_playlist.assert_awaited_once()
        assert len(result.succeeded) == 1
        uow.get_playlist_link_repository().create_links_batch.assert_awaited_once()
        uow.commit.assert_awaited_once()


class TestBatchedLinkCreation:
    async def test_two_new_playlists_one_create_links_batch_call(self) -> None:
        cp1 = _cp("sp1", name="A")
        cp2 = _cp("sp2", name="B")

        async def fake_get_playlist(pid: str):
            return cp1 if pid == "sp1" else cp2

        uow, connector = make_mock_uow_with_connector()
        connector.get_playlist.side_effect = fake_get_playlist

        with patch(_UPSERT_PATCH, new=AsyncMock(return_value=_create_result())):
            result = await _use_case().execute(_cmd(["sp1", "sp2"]), uow)

        assert len(result.succeeded) == 2
        # Single round-trip for both new links.
        link_repo = uow.get_playlist_link_repository()
        link_repo.create_links_batch.assert_awaited_once()
        assert len(link_repo.create_links_batch.call_args.args[0]) == 2


class TestFailureIsolation:
    async def test_fetch_failure_one_of_two(self) -> None:
        async def fake_get_playlist(pid: str):
            if pid == "bad":
                raise RuntimeError("404 on bad")
            return _cp(pid)

        uow, connector = make_mock_uow_with_connector()
        connector.get_playlist.side_effect = fake_get_playlist

        with patch(_UPSERT_PATCH, new=AsyncMock(return_value=_create_result())):
            result = await _use_case().execute(_cmd(["good", "bad"]), uow)

        assert len(result.succeeded) == 1
        assert result.succeeded[0].connector_playlist_identifier == "good"
        assert len(result.failed) == 1
        assert result.failed[0].connector_playlist_identifier == "bad"

    async def test_canonical_upsert_failure_captured(self) -> None:
        cp = _cp("sp1")
        uow, _ = make_mock_uow_with_connector(get_playlist_return=cp)

        async def bad_upsert(*args, **kwargs):
            raise RuntimeError("canonical blew up")

        with patch(_UPSERT_PATCH, new=AsyncMock(side_effect=bad_upsert)):
            result = await _use_case().execute(_cmd(["sp1"]), uow)

        assert len(result.succeeded) == 0
        assert len(result.failed) == 1
        assert "canonical blew up" in result.failed[0].message
        uow.get_playlist_link_repository().create_links_batch.assert_not_called()
        uow.commit.assert_not_called()


class TestConnectorThreading:
    async def test_connector_name_resolves_to_provider(self) -> None:
        cp = _cp("sp1")
        uow, _ = make_mock_uow_with_connector(get_playlist_return=cp)

        with patch(_UPSERT_PATCH, new=AsyncMock(return_value=_create_result())):
            _ = await _use_case().execute(_cmd(["sp1"]), uow)

        uow.get_service_connector_provider().get_connector.assert_called_with("spotify")


class TestNoWork:
    async def test_empty_ids_empty_result(self) -> None:
        uow, connector = make_mock_uow_with_connector()

        with patch(_UPSERT_PATCH, new=AsyncMock()) as upsert_mock:
            result = await _use_case().execute(_cmd([]), uow)

        connector.get_playlist.assert_not_called()
        upsert_mock.assert_not_called()
        assert len(result.succeeded) == 0
        uow.commit.assert_not_called()


class TestProgressEmission:
    """Emitter-contract tests for the SSE migration.

    Zero emission when no emitter/manager is passed (preserves every existing
    CLI + unit-test path). When both are passed, the use case emits one
    top-level operation and one sub-operation per playlist with outcome
    metadata (phase, outcome, resolved/unresolved for successes, error_message
    for failures) — enough signal for the UI to render a per-playlist result
    list without inspecting the HTTP response.
    """

    async def test_no_emitter_no_events(self) -> None:
        """Default code path — zero events fire. Preserves CLI + test behavior."""
        from src.domain.entities.progress import NullProgressEmitter

        cp = _cp("sp1")
        uow, _ = make_mock_uow_with_connector(get_playlist_return=cp)
        emitter = NullProgressEmitter()
        # Wrap to observe any accidental emission.
        emitter.start_operation = AsyncMock(wraps=emitter.start_operation)  # type: ignore[method-assign]
        emitter.complete_operation = AsyncMock(wraps=emitter.complete_operation)  # type: ignore[method-assign]

        with patch(_UPSERT_PATCH, new=AsyncMock(return_value=_create_result())):
            _ = await _use_case().execute(
                _cmd(["sp1"]),
                uow,
                progress_emitter=None,
                progress_manager=None,
            )

        emitter.start_operation.assert_not_called()
        emitter.complete_operation.assert_not_called()

    async def test_emits_top_level_and_per_playlist_sub_ops(self) -> None:
        """With a real emitter + manager, top-level op starts once and each
        playlist gets its own sub-op with outcome metadata on completion."""
        cp1 = _cp("sp1", name="Chill")
        cp2 = _cp("sp2", name="Mellow")

        async def fake_get_playlist(pid: str, **_kwargs):
            return cp1 if pid == "sp1" else cp2

        uow, connector = make_mock_uow_with_connector()
        connector.get_playlist.side_effect = fake_get_playlist

        emitter = AsyncMock()
        emitter.start_operation = AsyncMock(return_value="top-op-id")
        emitter.complete_operation = AsyncMock()
        emitter.emit_progress = AsyncMock()

        manager = AsyncMock()
        manager.start_operation = AsyncMock(side_effect=[f"sub-{i}" for i in range(10)])
        manager.complete_operation = AsyncMock()
        manager.emit_progress = AsyncMock()

        with patch(_UPSERT_PATCH, new=AsyncMock(return_value=_create_result())):
            _ = await _use_case().execute(
                _cmd(["sp1", "sp2"]),
                uow,
                progress_emitter=emitter,
                progress_manager=manager,
            )

        # Exactly one top-level operation, batch-sized total.
        emitter.start_operation.assert_awaited_once()
        op_arg = emitter.start_operation.await_args.args[0]
        assert op_arg.total_items == 2
        assert "2 playlists" in op_arg.description.lower()

        # Two sub-operations started (one per playlist).
        assert manager.start_operation.await_count == 2
        # Each sub-op carries parent + identifier metadata for SSE routing.
        first_sub_op = manager.start_operation.await_args_list[0].args[0]
        assert first_sub_op.metadata["parent_operation_id"] == "top-op-id"
        assert first_sub_op.metadata["connector_playlist_identifier"] in {
            "sp1",
            "sp2",
        }

        # Each sub-op completes — triggering the SSE sub_operation_completed.
        assert manager.complete_operation.await_count == 2

        # Top-level ends COMPLETED (all succeeded → not FAILED).
        emitter.complete_operation.assert_awaited_once()
        final_status = emitter.complete_operation.await_args.args[1]
        assert final_status.value == "completed"

    async def test_emits_failure_outcome_metadata_on_fetch_error(self) -> None:
        """A fetch-phase 404 emits a sub_progress with outcome=failed and the
        error message in metadata before completing the sub-op FAILED."""
        from src.domain.entities.progress import ProgressStatus

        async def fake_get_playlist(pid: str, **_kwargs):
            if pid == "bad":
                raise RuntimeError("404 on bad")
            return _cp(pid)

        uow, connector = make_mock_uow_with_connector()
        connector.get_playlist.side_effect = fake_get_playlist

        emitter = AsyncMock()
        emitter.start_operation = AsyncMock(return_value="top-op-id")

        manager = AsyncMock()
        manager.start_operation = AsyncMock(side_effect=[f"sub-{i}" for i in range(10)])

        with patch(_UPSERT_PATCH, new=AsyncMock(return_value=_create_result())):
            _ = await _use_case().execute(
                _cmd(["good", "bad"]),
                uow,
                progress_emitter=emitter,
                progress_manager=manager,
            )

        # One of the emitted sub_progress events must carry the failure
        # outcome + error_message for the "bad" playlist.
        failure_events = [
            call.args[0]
            for call in manager.emit_progress.await_args_list
            if call.args[0].metadata.get("outcome") == "failed"
        ]
        assert len(failure_events) == 1
        failure = failure_events[0]
        assert failure.metadata["connector_playlist_identifier"] == "bad"
        assert failure.metadata["error_message"] == "404 on bad"
        assert failure.metadata["phase"] == "fetch"
        assert failure.status == ProgressStatus.FAILED
