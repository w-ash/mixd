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

from src.application.use_cases.import_connector_playlist_as_canonical import (
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
        assert list(call_kwargs["connector_playlist_ids"]) == ["sp1"]
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


class TestImportSpotifySource:
    """--source flag maps to SyncDirection, with clean errors for typos."""

    def test_source_spotify_maps_to_pull(self) -> None:
        views = [_view("sp1", "A")]
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
                app, ["playlist", "import-spotify", "sp1", "--source", "spotify"]
            )

        assert result.exit_code == 0, result.output
        assert import_mock.await_args.kwargs["sync_direction"] == SyncDirection.PULL

    def test_source_mixd_maps_to_push(self) -> None:
        views = [_view("sp1", "A")]
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
                app, ["playlist", "import-spotify", "sp1", "--source", "mixd"]
            )

        assert result.exit_code == 0, result.output
        assert import_mock.await_args.kwargs["sync_direction"] == SyncDirection.PUSH

    def test_invalid_source_raises_bad_parameter(self) -> None:
        result = runner.invoke(
            app, ["playlist", "import-spotify", "sp1", "--source", "apple_music"]
        )

        assert result.exit_code == 2
        assert "not a valid source" in result.output
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
        sent_ids = set(import_mock.await_args.kwargs["connector_playlist_ids"])
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
        assert list(call_kwargs["connector_playlist_ids"]) == ["sp1"]
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
