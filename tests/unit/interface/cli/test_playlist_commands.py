"""Tests for the playlist CLI commands.

Focus on the CLI command contract — argument parsing, reference
resolution, --source flag mapping, validation errors, and clean error
output. Use-case behavior itself is covered by
``test_import_connector_playlist_as_canonical.py``,
``test_apply_playlist_assignments.py``, and ``test_list_connector_playlists.py``.
"""

from collections.abc import Sequence
from unittest.mock import AsyncMock, patch
from uuid import uuid4, uuid7

from typer.testing import CliRunner

from src.application.services.connector_playlist_sync_service import RefreshFailure
from src.application.use_cases.import_connector_playlist_as_canonical import (
    CanonicalImportFailure,
    CanonicalImportOutcome,
    ImportConnectorPlaylistsAsCanonicalResult,
)
from src.application.use_cases.list_connector_playlists import (
    ConnectorPlaylistView,
    ImportStatus,
    ListConnectorPlaylistsResult,
)
from src.application.use_cases.refresh_connector_playlists import (
    RefreshConnectorPlaylistsResult,
)
from src.domain.entities.playlist_link import SyncDirection
from src.interface.cli.app import app

runner = CliRunner()

_IMPORT_PATCH = (
    "src.application.use_cases.import_connector_playlist_as_canonical."
    "run_import_connector_playlists_as_canonical"
)
_LIST_PATCH = (
    "src.application.use_cases.list_connector_playlists.run_list_connector_playlists"
)
_REFRESH_PATCH = (
    "src.application.use_cases.refresh_connector_playlists."
    "run_refresh_connector_playlists"
)


def _view(
    identifier: str,
    name: str,
    *,
    status: ImportStatus = "not_imported",
    track_count: int = 10,
) -> ConnectorPlaylistView:
    return ConnectorPlaylistView(
        connector_playlist_identifier=identifier,
        connector_playlist_db_id=uuid7(),
        name=name,
        description=None,
        owner="ash",
        image_url=None,
        track_count=track_count,
        snapshot_id="snap",
        collaborative=False,
        is_public=False,
        import_status=status,
    )


def _listing(views: Sequence[ConnectorPlaylistView]) -> ListConnectorPlaylistsResult:
    return ListConnectorPlaylistsResult(playlists=views, from_cache=True)


def _empty_import_result() -> ImportConnectorPlaylistsAsCanonicalResult:
    return ImportConnectorPlaylistsAsCanonicalResult(
        succeeded=[], skipped_unchanged=[], failed=[]
    )


class TestBrowseSpotify:
    """Cached browse reads the use case; filtering is client-side."""

    def test_renders_cached_results_without_force_refresh(self) -> None:
        views = [_view("sp1", "Chill Vibes"), _view("sp2", "Workout")]
        list_mock = AsyncMock(return_value=_listing(views))

        with patch(
            _LIST_PATCH,
            list_mock,
        ):
            result = runner.invoke(app, ["playlist", "browse-spotify"])

        assert result.exit_code == 0, result.output
        assert "Chill Vibes" in result.output
        assert "Workout" in result.output
        list_mock.assert_awaited_once()
        call_kwargs = list_mock.await_args.kwargs
        assert call_kwargs["force_refresh"] is False
        assert "Traceback" not in result.output

    def test_not_imported_filter(self) -> None:
        views = [
            _view("sp1", "Chill Vibes", status="imported"),
            _view("sp2", "Workout", status="not_imported"),
        ]
        with patch(
            _LIST_PATCH,
            AsyncMock(return_value=_listing(views)),
        ):
            result = runner.invoke(
                app, ["playlist", "browse-spotify", "--not-imported"]
            )

        assert result.exit_code == 0, result.output
        assert "Workout" in result.output
        assert "Chill Vibes" not in result.output


class TestImportSpotifyResolution:
    """Name-or-ID resolution against the cached listing."""

    def test_resolves_by_name(self) -> None:
        views = [_view("sp1", "Chill Vibes"), _view("sp2", "Workout Bangers")]
        import_mock = AsyncMock(return_value=_empty_import_result())

        with (
            patch(
                _LIST_PATCH,
                AsyncMock(return_value=_listing(views)),
            ),
            patch(
                _IMPORT_PATCH,
                import_mock,
            ),
        ):
            result = runner.invoke(app, ["playlist", "import-spotify", "Chill Vibes"])

        assert result.exit_code == 0, result.output
        call_kwargs = import_mock.await_args.kwargs
        assert list(call_kwargs["connector_playlist_identifiers"]) == ["sp1"]
        assert "Traceback" not in result.output

    def test_ambiguous_name_produces_clean_error(self) -> None:
        views = [_view("sp1", "Chill Morning"), _view("sp2", "Chill Evening")]
        with (
            patch(
                _LIST_PATCH,
                AsyncMock(return_value=_listing(views)),
            ),
            patch(
                _IMPORT_PATCH,
                AsyncMock(return_value=_empty_import_result()),
            ),
        ):
            result = runner.invoke(app, ["playlist", "import-spotify", "Chill"])

        assert result.exit_code == 1
        assert "matches multiple" in result.output
        assert "Traceback" not in result.output


class TestImportSpotifyDirection:
    """Import always pulls (external → canonical); ``--source`` was removed."""

    def test_import_relies_on_pull_default(self) -> None:
        views = [_view("sp1", "A")]
        import_mock = AsyncMock(return_value=_empty_import_result())
        with (
            patch(_LIST_PATCH, AsyncMock(return_value=_listing(views))),
            patch(_IMPORT_PATCH, import_mock),
        ):
            result = runner.invoke(app, ["playlist", "import-spotify", "sp1"])

        assert result.exit_code == 0, result.output
        # Import never passes a direction — it relies on the wrapper's PULL default.
        assert "sync_direction" not in import_mock.await_args.kwargs

    def test_source_flag_no_longer_accepted(self) -> None:
        # The incoherent "import --source mixd" (push during import) path is gone.
        result = runner.invoke(
            app, ["playlist", "import-spotify", "sp1", "--source", "mixd"]
        )
        assert result.exit_code == 2  # unknown option
        assert "Traceback" not in result.output


class TestImportSpotifyAllNotImported:
    """`--all --not-imported` should skip playlists that already have links."""

    def test_skips_already_imported(self) -> None:
        views = [
            _view("sp1", "Already", status="imported"),
            _view("sp2", "New A", status="not_imported"),
            _view("sp3", "New B", status="not_imported"),
        ]
        import_mock = AsyncMock(return_value=_empty_import_result())

        with (
            patch(
                _LIST_PATCH,
                AsyncMock(return_value=_listing(views)),
            ),
            patch(
                _IMPORT_PATCH,
                import_mock,
            ),
        ):
            result = runner.invoke(
                app, ["playlist", "import-spotify", "--all", "--not-imported"]
            )

        assert result.exit_code == 0, result.output
        sent_ids = set(import_mock.await_args.kwargs["connector_playlist_identifiers"])
        assert sent_ids == {"sp2", "sp3"}
        assert "Traceback" not in result.output

    def test_reports_summary_with_batch_result_shape(self) -> None:
        views = [_view("sp1", "A"), _view("sp2", "B")]
        import_result = ImportConnectorPlaylistsAsCanonicalResult(
            succeeded=[
                CanonicalImportOutcome(
                    connector_playlist_identifier="sp1",
                    canonical_playlist_id=uuid4(),
                    resolved=10,
                    unresolved=0,
                )
            ],
            skipped_unchanged=["sp2"],
            failed=[],
        )
        with (
            patch(
                _LIST_PATCH,
                AsyncMock(return_value=_listing(views)),
            ),
            patch(
                _IMPORT_PATCH,
                AsyncMock(return_value=import_result),
            ),
        ):
            result = runner.invoke(app, ["playlist", "import-spotify", "--all"])

        assert result.exit_code == 0, result.output
        assert "Succeeded" in result.output
        assert "Skipped" in result.output
        assert "Total" in result.output

    def test_renders_per_item_failures_then_summary(self) -> None:
        # A failed item surfaces as a "Failed:" line AND is counted in the
        # summary — the path the shared report_connector_batch_outcome
        # helper owns (DUP-06 consolidation).
        views = [_view("sp1", "A")]
        import_result = ImportConnectorPlaylistsAsCanonicalResult(
            succeeded=[],
            skipped_unchanged=[],
            failed=[
                CanonicalImportFailure(
                    connector_playlist_identifier="sp1",
                    message="connector rejected the push",
                )
            ],
        )
        with (
            patch(_LIST_PATCH, AsyncMock(return_value=_listing(views))),
            patch(_IMPORT_PATCH, AsyncMock(return_value=import_result)),
        ):
            result = runner.invoke(app, ["playlist", "import-spotify", "--all"])

        assert result.exit_code == 0, result.output
        assert "Failed:" in result.output
        assert "sp1" in result.output
        assert "connector rejected the push" in result.output
        assert "Traceback" not in result.output


class TestAssignmentValidation:
    """`mixd playlist assign` rejects bad action types + values cleanly."""

    def test_invalid_action_rejected(self) -> None:
        result = runner.invoke(
            app,
            [
                "playlist",
                "assign",
                "Chill",
                "--action",
                "not_a_real_action",
                "--value",
                "star",
            ],
        )
        assert result.exit_code == 2
        assert "set_preference" in result.output
        assert "Traceback" not in result.output

    def test_invalid_preference_value_rejected(self) -> None:
        result = runner.invoke(
            app,
            [
                "playlist",
                "assign",
                "Chill",
                "--action",
                "set_preference",
                "--value",
                "love",
            ],
        )
        assert result.exit_code == 2
        assert "must be one of" in result.output
        assert "Traceback" not in result.output

    def test_invalid_tag_value_rejected(self) -> None:
        result = runner.invoke(
            app,
            [
                "playlist",
                "assign",
                "Chill",
                "--action",
                "add_tag",
                "--value",
                "bad/tag",
            ],
        )
        assert result.exit_code == 2
        assert "Traceback" not in result.output


class TestImportSpotifyRefresh:
    """``--refresh`` forwards force=True to the import use case."""

    def test_refresh_flag_sets_force_true(self) -> None:
        views = [_view("sp1", "Chill Vibes")]
        import_mock = AsyncMock(return_value=_empty_import_result())

        with (
            patch(_LIST_PATCH, AsyncMock(return_value=_listing(views))),
            patch(_IMPORT_PATCH, import_mock),
        ):
            result = runner.invoke(
                app,
                ["playlist", "import-spotify", "Chill Vibes", "--refresh"],
            )

        assert result.exit_code == 0, result.output
        assert import_mock.await_args.kwargs["force"] is True

    def test_no_refresh_flag_sets_force_false(self) -> None:
        views = [_view("sp1", "Chill Vibes")]
        import_mock = AsyncMock(return_value=_empty_import_result())

        with (
            patch(_LIST_PATCH, AsyncMock(return_value=_listing(views))),
            patch(_IMPORT_PATCH, import_mock),
        ):
            result = runner.invoke(app, ["playlist", "import-spotify", "Chill Vibes"])

        assert result.exit_code == 0, result.output
        assert import_mock.await_args.kwargs["force"] is False


class TestRefreshSpotify:
    """``refresh-spotify`` resolves a single ref and calls UC1 only."""

    def test_resolves_single_name_and_calls_refresh_uc(self) -> None:
        views = [_view("sp1", "Chill Vibes"), _view("sp2", "Workout")]
        refresh_mock = AsyncMock(
            return_value=RefreshConnectorPlaylistsResult(
                succeeded=["sp1"], skipped_unchanged=[], failed=[]
            )
        )

        with (
            patch(_LIST_PATCH, AsyncMock(return_value=_listing(views))),
            patch(_REFRESH_PATCH, refresh_mock),
        ):
            result = runner.invoke(app, ["playlist", "refresh-spotify", "Chill Vibes"])

        assert result.exit_code == 0, result.output
        call_kwargs = refresh_mock.await_args.kwargs
        assert list(call_kwargs["connector_playlist_identifiers"]) == ["sp1"]
        assert call_kwargs["force"] is False
        assert "Traceback" not in result.output

    def test_refresh_flag_sets_force_true_on_uc(self) -> None:
        views = [_view("sp1", "Chill Vibes")]
        refresh_mock = AsyncMock(
            return_value=RefreshConnectorPlaylistsResult(
                succeeded=["sp1"], skipped_unchanged=[], failed=[]
            )
        )

        with (
            patch(_LIST_PATCH, AsyncMock(return_value=_listing(views))),
            patch(_REFRESH_PATCH, refresh_mock),
        ):
            result = runner.invoke(
                app,
                ["playlist", "refresh-spotify", "Chill Vibes", "--refresh"],
            )

        assert result.exit_code == 0, result.output
        assert refresh_mock.await_args.kwargs["force"] is True

    def test_renders_per_item_failures_then_summary(self) -> None:
        # refresh-spotify renders failures through the same shared helper
        # as import-spotify (DUP-06 consolidation) — identical output shape.
        views = [_view("sp1", "Chill Vibes")]
        refresh_result = RefreshConnectorPlaylistsResult(
            succeeded=[],
            skipped_unchanged=[],
            failed=[
                RefreshFailure(
                    connector_playlist_identifier="sp1",
                    message="cache refresh failed",
                )
            ],
        )
        with (
            patch(_LIST_PATCH, AsyncMock(return_value=_listing(views))),
            patch(_REFRESH_PATCH, AsyncMock(return_value=refresh_result)),
        ):
            result = runner.invoke(app, ["playlist", "refresh-spotify", "Chill Vibes"])

        assert result.exit_code == 0, result.output
        assert "Failed:" in result.output
        assert "cache refresh failed" in result.output
        assert "Traceback" not in result.output

    def test_unknown_ref_exits_with_error(self) -> None:
        views = [_view("sp1", "Chill Vibes")]

        with (
            patch(_LIST_PATCH, AsyncMock(return_value=_listing(views))),
            patch(_REFRESH_PATCH, AsyncMock()) as refresh_mock,
        ):
            result = runner.invoke(
                app, ["playlist", "refresh-spotify", "Nonexistent Playlist"]
            )

        assert result.exit_code == 1
        assert "No Spotify playlist matching" in result.output
        refresh_mock.assert_not_called()
        assert "Traceback" not in result.output

    def test_missing_arg_errors_via_typer(self) -> None:
        result = runner.invoke(app, ["playlist", "refresh-spotify"])
        assert result.exit_code == 2  # Typer missing-arg exit
        assert "Traceback" not in result.output


class TestSyncLinkOutput:
    """``sync`` renders the unmatched count only when there is one."""

    @staticmethod
    def _result(*, unmatched: int):
        from src.application.use_cases.sync_playlist_link import SyncPlaylistLinkResult
        from src.domain.entities.playlist_link import PlaylistLink

        link = PlaylistLink(
            playlist_id=uuid7(),
            connector_name="spotify",
            connector_playlist_identifier="ext123",
        )
        return SyncPlaylistLinkResult(
            link=link, tracks_added=5, tracks_removed=2, tracks_unmatched=unmatched
        )

    def _invoke(self, sync_result):
        # run_async is the call-site bridge; close the coroutine to avoid an
        # "unawaited coroutine" warning, then return the canned result.
        def _fake_run_async(coro):
            coro.close()
            return sync_result

        with patch("src.interface.cli.playlist_commands.run_async", _fake_run_async):
            return runner.invoke(app, ["playlist", "sync", str(uuid4())])

    def test_shows_unmatched_when_present(self) -> None:
        result = self._invoke(self._result(unmatched=3))
        assert result.exit_code == 0, result.output
        assert "3 unmatched" in result.output
        assert "Traceback" not in result.output

    def test_omits_unmatched_when_zero(self) -> None:
        result = self._invoke(self._result(unmatched=0))
        assert result.exit_code == 0, result.output
        assert "unmatched" not in result.output


def _sync_result(*, added: int = 1, removed: int = 0, unmatched: int = 0):
    from src.application.use_cases.sync_playlist_link import SyncPlaylistLinkResult
    from src.domain.entities.playlist_link import PlaylistLink

    link = PlaylistLink(
        playlist_id=uuid7(), connector_name="spotify", connector_playlist_identifier="x"
    )
    return SyncPlaylistLinkResult(
        link=link,
        tracks_added=added,
        tracks_removed=removed,
        tracks_unmatched=unmatched,
    )


def _invoke_sync_capturing(args: list[str], *, result=None, raises=None):
    """Run ``playlist sync`` with the use case mocked, capturing the built command.

    Patches the use-case ``execute`` + the runner so the real command body runs
    (flag → Command mapping) without a DB. Returns ``(invoke_result, captured)``.
    """
    from src.application.use_cases.sync_playlist_link import SyncPlaylistLinkUseCase

    captured: dict[str, object] = {}

    async def _fake_execute(_self, command, _uow):
        captured["command"] = command
        if raises is not None:
            raise raises
        return result if result is not None else _sync_result()

    async def _fake_euc(factory, user_id=None):
        return await factory(AsyncMock())

    with (
        patch.object(SyncPlaylistLinkUseCase, "execute", _fake_execute),
        patch("src.application.runner.execute_use_case", _fake_euc),
    ):
        invoke_result = runner.invoke(app, ["playlist", "sync", *args])
    return invoke_result, captured


class TestSyncSource:
    """``--source {spotify,mixd}`` maps to a one-time direction override."""

    def test_source_spotify_maps_to_pull(self) -> None:
        result, captured = _invoke_sync_capturing([str(uuid4()), "--source", "spotify"])
        assert result.exit_code == 0, result.output
        assert captured["command"].direction_override == SyncDirection.PULL

    def test_source_mixd_maps_to_push(self) -> None:
        result, captured = _invoke_sync_capturing([str(uuid4()), "--source", "mixd"])
        assert result.exit_code == 0, result.output
        assert captured["command"].direction_override == SyncDirection.PUSH

    def test_no_source_leaves_override_none(self) -> None:
        result, captured = _invoke_sync_capturing([str(uuid4())])
        assert result.exit_code == 0, result.output
        assert captured["command"].direction_override is None

    def test_invalid_source_exits_2(self) -> None:
        result = runner.invoke(
            app, ["playlist", "sync", str(uuid4()), "--source", "apple"]
        )
        assert result.exit_code == 2
        assert "Traceback" not in result.output


class TestSyncConfirm:
    """``--confirm`` and the destructive two-step (the CLI's 409 equivalent)."""

    def test_confirm_flag_sets_confirmed_true(self) -> None:
        result, captured = _invoke_sync_capturing([str(uuid4()), "--confirm"])
        assert result.exit_code == 0, result.output
        assert captured["command"].confirmed is True

    def test_without_confirm_is_false(self) -> None:
        _result, captured = _invoke_sync_capturing([str(uuid4())])
        assert captured["command"].confirmed is False

    def test_destructive_renders_warning_and_exits_1(self) -> None:
        from src.domain.exceptions import ConfirmationRequiredError

        exc = ConfirmationRequiredError(
            "This will remove 40 of 50 tracks. 10 will remain.",
            removals=40,
            total=50,
            remaining=10,
        )
        result, _captured = _invoke_sync_capturing([str(uuid4())], raises=exc)

        assert result.exit_code == 1
        assert "Destructive Sync" in result.output
        assert "40" in result.output
        assert "--confirm" in result.output  # the re-run hint
        assert "Traceback" not in result.output


class TestSyncPreviewRender:
    """``sync-preview`` renders the diff and surfaces the destructive warning."""

    @staticmethod
    def _preview(*, flagged: bool = False):
        from src.application.use_cases.preview_playlist_sync import (
            PreviewPlaylistSyncResult,
        )

        return PreviewPlaylistSyncResult(
            tracks_to_add=4,
            tracks_to_remove=40 if flagged else 1,
            tracks_unchanged=20,
            direction=SyncDirection.PULL,
            safety_flagged=flagged,
            safety_message="This will remove 40 of 50 tracks." if flagged else None,
        )

    def _invoke(self, preview, args: list[str]):
        def _fake_run_async(coro):
            coro.close()
            return preview

        with patch("src.interface.cli.playlist_commands.run_async", _fake_run_async):
            return runner.invoke(app, ["playlist", "sync-preview", *args])

    def test_renders_diff(self) -> None:
        result = self._invoke(self._preview(), [str(uuid4())])
        assert result.exit_code == 0, result.output
        assert "To add:" in result.output
        assert "Direction:" in result.output

    def test_renders_safety_warning_when_flagged(self) -> None:
        result = self._invoke(self._preview(flagged=True), [str(uuid4())])
        assert result.exit_code == 0, result.output
        assert "remove 40 of 50" in result.output
        assert "--confirm" in result.output


class TestRepair:
    """``repair`` renders resolved / still-unresolved counts."""

    def _invoke(self, repaired: int, still_unresolved: int):
        from types import SimpleNamespace

        from src.application.use_cases.repair_unresolved_entries import (
            RepairUnresolvedEntriesResult,
        )

        def _fake_run_async(coro):
            coro.close()
            return RepairUnresolvedEntriesResult(
                repaired=repaired, still_unresolved=still_unresolved
            )

        with (
            patch(
                "src.interface.cli.playlist_commands.resolve_playlist_ref",
                return_value=SimpleNamespace(id=uuid7()),
            ),
            patch("src.interface.cli.playlist_commands.run_async", _fake_run_async),
        ):
            return runner.invoke(app, ["playlist", "repair", "My Playlist"])

    def test_nothing_to_repair(self) -> None:
        result = self._invoke(repaired=0, still_unresolved=0)
        assert result.exit_code == 0, result.output
        assert "Nothing to repair" in result.output

    def test_reports_repaired_and_remaining(self) -> None:
        result = self._invoke(repaired=3, still_unresolved=2)
        assert result.exit_code == 0, result.output
        assert "Repaired 3" in result.output
        assert "2 still unresolved" in result.output
