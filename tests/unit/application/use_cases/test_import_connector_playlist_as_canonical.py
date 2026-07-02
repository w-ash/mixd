"""Unit tests for ImportConnectorPlaylistsAsCanonicalUseCase.

Verifies the first-import create path (refresh + canonical + link), the
re-import path (routes through PlaylistReconciliationEngine.apply: reconcile or
no-op), force-bypass on first import, failure isolation across phases, the
OperationResult mapper, and per-playlist issue recording on the audit row.
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid7

from src.application.services.playlist_reconciliation_engine import (
    PlaylistReconciliationEngine,
    ReconcileResult,
)
from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistResult,
)
from src.application.use_cases.import_connector_playlist_as_canonical import (
    CanonicalImportFailure,
    CanonicalImportOutcome,
    ImportConnectorPlaylistsAsCanonicalCommand,
    ImportConnectorPlaylistsAsCanonicalResult,
    ImportConnectorPlaylistsAsCanonicalUseCase,
    to_operation_result,
)
from src.application.use_cases.update_canonical_playlist import (
    UpdateCanonicalPlaylistResult,
)
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection
from src.domain.entities.shared import ConnectorPlaylistIdentifier
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
        connector_playlist_identifiers=ids,
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
_ISSUE_PATCH = (
    "src.application.use_cases.import_connector_playlist_as_canonical.append_run_issue"
)


class TestReimportRoutesThroughEngine:
    """A re-import (link already exists) reconciles against the fresh remote via
    the engine — never a cache short-circuit (the durability fix for bug #2)."""

    async def test_linked_reimport_noop_is_skipped_unchanged(self) -> None:
        """A re-import whose engine diff is a no-op lands in skipped_unchanged —
        based on a REAL fresh diff, and still commits the refreshed base."""
        uow, _ = make_mock_uow_with_connector()
        uow.get_playlist_link_repository().list_by_user_connector.return_value = [
            _link("sp1")
        ]
        uow.get_connector_playlist_repository().list_by_connector.return_value = [
            _cp("sp1", snapshot_id="cached-snap")
        ]

        apply_mock = AsyncMock(
            return_value=ReconcileResult(direction=SyncDirection.PULL, skipped=True)
        )
        with (
            patch.object(PlaylistReconciliationEngine, "apply", apply_mock),
            patch(_UPSERT_PATCH, new=AsyncMock()) as upsert_mock,
        ):
            result = await _use_case().execute(_cmd(["sp1"]), uow)

        apply_mock.assert_awaited_once()
        upsert_mock.assert_not_called()
        assert list(result.skipped_unchanged) == ["sp1"]
        assert len(result.succeeded) == 0
        # The no-op still records a fresh base snapshot, so the batch commits.
        uow.commit_batch.assert_awaited_once()

    async def test_linked_reimport_reconciles_as_update(self) -> None:
        """A re-import with a real diff lands in succeeded as an update."""
        uow, _ = make_mock_uow_with_connector()
        uow.get_playlist_link_repository().list_by_user_connector.return_value = [
            _link("sp1")
        ]
        uow.get_connector_playlist_repository().list_by_connector.return_value = [
            _cp("sp1", snapshot_id="cached-snap")
        ]

        apply_mock = AsyncMock(
            return_value=ReconcileResult(
                direction=SyncDirection.PULL,
                tracks_added=3,
                tracks_removed=1,
                resolved=10,
                unresolved=2,
            )
        )
        with patch.object(PlaylistReconciliationEngine, "apply", apply_mock):
            result = await _use_case().execute(_cmd(["sp1"]), uow)

        apply_mock.assert_awaited_once()
        # Import always pulls, and confirms (mirror semantics — no destructive gate).
        assert apply_mock.await_args.args[1] == SyncDirection.PULL
        assert apply_mock.await_args.kwargs["confirmed"] is True
        assert list(result.skipped_unchanged) == []
        assert len(result.succeeded) == 1
        outcome = result.succeeded[0]
        assert outcome.connector_playlist_identifier == "sp1"
        assert outcome.was_created is False
        assert outcome.resolved == 10
        assert outcome.unresolved == 2
        uow.commit_batch.assert_awaited_once()

    async def test_reimport_failure_isolated_from_first_import(self) -> None:
        """One link's engine failure is captured; a sibling new import still lands."""
        cp = _cp("new1", name="Fresh")
        uow, connector = make_mock_uow_with_connector(get_playlist_return=cp)
        uow.get_playlist_link_repository().list_by_user_connector.return_value = [
            _link("sp1")
        ]

        apply_mock = AsyncMock(side_effect=RuntimeError("engine blew up"))
        with (
            patch.object(PlaylistReconciliationEngine, "apply", apply_mock),
            patch(_UPSERT_PATCH, new=AsyncMock(return_value=_create_result("Fresh"))),
        ):
            result = await _use_case().execute(_cmd(["new1", "sp1"]), uow)

        assert len(result.succeeded) == 1
        assert result.succeeded[0].connector_playlist_identifier == "new1"
        assert len(result.failed) == 1
        assert result.failed[0].connector_playlist_identifier == "sp1"
        assert "engine blew up" in result.failed[0].message

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
        assert link_repo.create_link.await_count == 3
        created_ids = [
            c.args[0].connector_playlist_identifier
            for c in link_repo.create_link.await_args_list
        ]
        assert created_ids == ["sp1", "sp2", "sp3"]
        uow.commit_batch.assert_awaited()


class TestForce:
    """``force`` controls cache bypass on a *first* import (no link yet).

    Re-imports always fetch fresh via the engine regardless of this flag, so
    force only affects the create path's cache read-through.
    """

    async def test_force_bypasses_cache_for_first_import(self) -> None:
        """No link + fresh cache + force=True → fetch fresh from the connector."""
        cp = _cp("sp1", snapshot_id="fresh")
        uow, connector = make_mock_uow_with_connector(get_playlist_return=cp)
        uow.get_connector_playlist_repository().list_by_connector.return_value = [
            _cp("sp1", snapshot_id="cached-snap")
        ]

        with patch(_UPSERT_PATCH, new=AsyncMock(return_value=_create_result("Chill"))):
            result = await _use_case().execute(_cmd(["sp1"], force=True), uow)

        connector.get_playlist.assert_awaited_once_with("sp1")
        assert len(result.succeeded) == 1
        assert list(result.skipped_unchanged) == []

    async def test_no_force_uses_fresh_cache_for_first_import(self) -> None:
        """No link + fresh cache + force=False → use cache (no network), still create."""
        uow, connector = make_mock_uow_with_connector()
        uow.get_connector_playlist_repository().list_by_connector.return_value = [
            _cp("sp1", snapshot_id="cached-snap")
        ]

        with patch(_UPSERT_PATCH, new=AsyncMock(return_value=_create_result())):
            result = await _use_case().execute(_cmd(["sp1"], force=False), uow)

        connector.get_playlist.assert_not_called()
        assert len(result.succeeded) == 1
        uow.get_playlist_link_repository().create_link.assert_awaited_once()


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

        link_repo = uow.get_playlist_link_repository()
        link_repo.create_link.assert_awaited_once()
        link = link_repo.create_link.call_args.args[0]
        assert link.connector_name == "spotify"
        assert link.connector_playlist_identifier == "sp1"
        assert link.sync_direction == SyncDirection.PULL

        # Canonical + link commit together, per item.
        uow.commit_batch.assert_awaited()


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
        uow.get_playlist_link_repository().create_link.assert_awaited_once()
        uow.commit_batch.assert_awaited()


class TestPerItemLinkCreation:
    async def test_two_new_playlists_create_one_link_each(self) -> None:
        cp1 = _cp("sp1", name="A")
        cp2 = _cp("sp2", name="B")

        async def fake_get_playlist(pid: str):
            return cp1 if pid == "sp1" else cp2

        uow, connector = make_mock_uow_with_connector()
        connector.get_playlist.side_effect = fake_get_playlist

        with patch(_UPSERT_PATCH, new=AsyncMock(return_value=_create_result())):
            result = await _use_case().execute(_cmd(["sp1", "sp2"]), uow)

        assert len(result.succeeded) == 2
        # One link created per item — per-item atomic, not a single batch.
        link_repo = uow.get_playlist_link_repository()
        assert link_repo.create_link.await_count == 2
        created_ids = [
            c.args[0].connector_playlist_identifier
            for c in link_repo.create_link.await_args_list
        ]
        assert created_ids == ["sp1", "sp2"]


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
        # Upsert failed before the link existed; the item rolled back its own work
        # so nothing half-written lands.
        uow.get_playlist_link_repository().create_link.assert_not_called()
        uow.rollback.assert_awaited()


class TestPerItemAtomicity:
    """Each item commits in its own transaction: a failure mid-batch rolls back
    only the failing item, while earlier items stay committed and the failed one
    is never left half-written (no orphan canonical)."""

    async def test_second_item_failure_rolls_back_only_itself(self) -> None:
        cp1 = _cp("sp1", name="A")
        cp2 = _cp("sp2", name="B")

        async def fake_get_playlist(pid: str):
            return cp1 if pid == "sp1" else cp2

        uow, connector = make_mock_uow_with_connector()
        connector.get_playlist.side_effect = fake_get_playlist

        # Item A's link lands; item B's create_link fails *after* A committed —
        # the "fails after a prior item's commit" path the rearchitecture targets.
        link_repo = uow.get_playlist_link_repository()
        link_repo.create_link = AsyncMock(
            side_effect=[None, RuntimeError("link insert failed")]
        )

        with patch(_UPSERT_PATCH, new=AsyncMock(return_value=_create_result())):
            result = await _use_case().execute(_cmd(["sp1", "sp2"]), uow)

        # A succeeded (committed); B captured as failed — partial success holds.
        assert [o.connector_playlist_identifier for o in result.succeeded] == ["sp1"]
        assert [f.connector_playlist_identifier for f in result.failed] == ["sp2"]
        # Only B rolled back; A's commit (plus the pre-loop cache commit) landed.
        uow.rollback.assert_awaited_once()
        assert uow.commit_batch.await_count >= 2

    async def test_post_commit_emit_failure_does_not_double_count(self) -> None:
        """A progress-emit failure *after* an item committed must not re-record it
        as failed: the item is durable, so it stays in succeeded only (not in both
        succeeded and failed), and the committed item is never rolled back."""
        cp = _cp("sp1", name="A")
        uow, _ = make_mock_uow_with_connector(get_playlist_return=cp)

        # The post-commit terminal emission blows up (e.g. SSE/broker hiccup).
        emit_boom = AsyncMock(side_effect=RuntimeError("SSE broker down"))
        with (
            patch(_UPSERT_PATCH, new=AsyncMock(return_value=_create_result())),
            patch.object(
                ImportConnectorPlaylistsAsCanonicalUseCase,
                "_emit_sub_outcome",
                emit_boom,
            ),
        ):
            result = await _use_case().execute(_cmd(["sp1"]), uow)

        assert [o.connector_playlist_identifier for o in result.succeeded] == ["sp1"]
        assert len(result.failed) == 0
        emit_boom.assert_awaited()  # the success emit was attempted and raised
        uow.commit_batch.assert_awaited()  # the item committed
        uow.rollback.assert_not_awaited()  # a committed item is never rolled back

    async def test_failure_path_emit_error_does_not_abort_batch(self) -> None:
        """A failing item whose *failure*-path emit ALSO raises must not abort the
        batch: the failure is already recorded, so execute returns a partial result
        instead of letting the emit error escape past _complete_top_op/_record_issues."""
        cp = _cp("sp1", name="A")
        uow, _ = make_mock_uow_with_connector(get_playlist_return=cp)

        async def bad_upsert(*args: object, **kwargs: object):
            raise RuntimeError("canonical blew up")

        emit_boom = AsyncMock(side_effect=RuntimeError("SSE broker down"))
        with (
            patch(_UPSERT_PATCH, new=AsyncMock(side_effect=bad_upsert)),
            patch.object(
                ImportConnectorPlaylistsAsCanonicalUseCase,
                "_emit_sub_outcome",
                emit_boom,
            ),
        ):
            # Must NOT raise: without best-effort emit, the failure-path emit would
            # propagate out of execute and skip the failed-item accounting.
            result = await _use_case().execute(_cmd(["sp1"]), uow)

        assert [f.connector_playlist_identifier for f in result.failed] == ["sp1"]
        assert len(result.succeeded) == 0
        emit_boom.assert_awaited()  # the failure emit was attempted and raised


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
        uow.commit_batch.assert_not_called()


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
                progress_broker=None,
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
                progress_broker=manager,
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
                progress_broker=manager,
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


class TestRunIssueRecording:
    """Per-playlist failures land on the durable OperationRun audit row when a
    run_id is threaded (web/SSE path), and are skipped without one (CLI/tests)."""

    async def test_failure_records_issue_when_run_id_present(self) -> None:
        async def fake_get_playlist(pid: str, **_kwargs):
            raise RuntimeError("boom")

        uow, connector = make_mock_uow_with_connector()
        connector.get_playlist.side_effect = fake_get_playlist
        run_id = uuid7()

        with patch(_ISSUE_PATCH, new=AsyncMock()) as issue_mock:
            result = await _use_case().execute(_cmd(["sp1"]), uow, run_id=run_id)

        assert len(result.failed) == 1
        issue_mock.assert_awaited_once()
        assert issue_mock.await_args.args[0] == run_id

    async def test_no_issue_recorded_when_run_id_none(self) -> None:
        async def fake_get_playlist(pid: str, **_kwargs):
            raise RuntimeError("boom")

        uow, connector = make_mock_uow_with_connector()
        connector.get_playlist.side_effect = fake_get_playlist

        with patch(_ISSUE_PATCH, new=AsyncMock()) as issue_mock:
            result = await _use_case().execute(_cmd(["sp1"]), uow)

        assert len(result.failed) == 1
        issue_mock.assert_not_called()


class TestToOperationResult:
    """The SSE-seam mapper flattens the native result into audit counts."""

    def test_maps_imported_updated_skipped_unresolved_errors(self) -> None:
        result = ImportConnectorPlaylistsAsCanonicalResult(
            succeeded=[
                CanonicalImportOutcome(
                    ConnectorPlaylistIdentifier("a"),
                    uuid7(),
                    resolved=5,
                    unresolved=1,
                    was_created=True,
                ),
                CanonicalImportOutcome(
                    ConnectorPlaylistIdentifier("b"),
                    uuid7(),
                    resolved=3,
                    unresolved=2,
                    was_created=False,
                ),
            ],
            skipped_unchanged=["c"],
            failed=[CanonicalImportFailure(ConnectorPlaylistIdentifier("d"), "boom")],
        )

        counts = to_operation_result(result).to_counts()

        assert counts["imported"] == 1
        assert counts["updated"] == 1
        assert counts["skipped"] == 1
        assert counts["unresolved"] == 3
        assert counts["errors"] == 1

    def test_any_failure_marks_is_failure(self) -> None:
        result = ImportConnectorPlaylistsAsCanonicalResult(
            succeeded=[],
            skipped_unchanged=[],
            failed=[CanonicalImportFailure(ConnectorPlaylistIdentifier("d"), "boom")],
        )
        assert to_operation_result(result).is_failure is True

    def test_clean_run_is_not_failure_and_omits_errors(self) -> None:
        result = ImportConnectorPlaylistsAsCanonicalResult(
            succeeded=[
                CanonicalImportOutcome(
                    ConnectorPlaylistIdentifier("a"),
                    uuid7(),
                    resolved=5,
                    unresolved=0,
                    was_created=True,
                )
            ],
            skipped_unchanged=[],
            failed=[],
        )
        op = to_operation_result(result)
        assert op.is_failure is False
        assert "errors" not in op.to_counts()
