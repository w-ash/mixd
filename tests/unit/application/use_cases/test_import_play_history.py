"""Unit tests for ImportTracksUseCase.

Tests command validation for service/mode combinations and the routing logic
that delegates to service-specific importers.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.application.services.play_import_orchestrator import PlayImportOrchestrator
from src.application.use_cases.import_play_history import (
    ImportTracksCommand,
    ImportTracksResult,
    ImportTracksUseCase,
)
from src.domain.entities import OperationResult
from src.domain.entities.progress import NullProgressEmitter
from src.domain.repositories.play import (
    AppleRecentImportParams,
    LastfmImportParams,
    SpotifyImportParams,
    SpotifyRecentImportParams,
)
from tests.fixtures import make_mock_uow


def _uow(importer_error: Exception | None = None) -> MagicMock:
    """UoW whose play-import provider builds an importer, or fails to."""
    uow = make_mock_uow()
    provider = uow.get_play_import_provider.return_value
    provider.create_play_importer = AsyncMock(side_effect=importer_error)
    return uow


class TestImportTracksCommand:
    """Test command validation for service/mode combinations."""

    def test_lastfm_file_mode_rejected(self):
        """Test that LastFM doesn't support file mode."""
        with pytest.raises(ValueError, match="doesn't support file mode"):
            ImportTracksCommand(
                user_id="test-user",
                service="lastfm",
                mode="file",
                file_path=Path("/data/test.json"),
            )

    def test_spotify_full_mode_rejected(self):
        """The API retains only ~50 plays, so 'the whole history' is unaskable."""
        with pytest.raises(ValueError, match="doesn't support full mode"):
            ImportTracksCommand(user_id="test-user", service="spotify", mode="full")

    def test_spotify_api_mode_with_file_path_rejected(self):
        """An API poll has no file to read — passing one means a confused caller."""
        with pytest.raises(ValueError, match="file_path is not valid"):
            ImportTracksCommand(
                user_id="test-user",
                service="spotify",
                mode="recent",
                file_path=Path("/data/export.json"),
            )

    def test_spotify_file_without_path_rejected(self):
        """Test that Spotify file mode requires file_path."""
        with pytest.raises(ValueError, match="file_path is required"):
            ImportTracksCommand(user_id="test-user", service="spotify", mode="file")


class TestImportTracksUseCase:
    """Test use case execution and error handling."""

    async def test_exception_returns_failed_result(self):
        """A failing import becomes a failed result carrying the error."""
        uow = _uow(importer_error=RuntimeError("Connection failed"))
        command = ImportTracksCommand(
            user_id="test-user", service="lastfm", mode="recent"
        )

        result = await ImportTracksUseCase().execute(command, uow)

        assert result.service == "lastfm"
        assert result.mode == "recent"
        assert result.operation_result.is_failure
        assert result.operation_result.summary_metrics.get("errors") == 1
        assert result.operation_result.metadata["error"] == "Connection failed"

    async def test_quota_exhaustion_propagates_instead_of_failed_result(self):
        """PDR-003 quota exhaustion must escape like the auth errors do — a
        soft-failure result would bury the outage and let callers keep going."""
        from src.domain.exceptions import SpotifyQuotaExhaustedError

        uow = _uow(importer_error=SpotifyQuotaExhaustedError())
        command = ImportTracksCommand(
            user_id="test-user", service="spotify", mode="recent"
        )

        with pytest.raises(SpotifyQuotaExhaustedError):
            _ = await ImportTracksUseCase().execute(command, uow)

    @staticmethod
    def _patched_two_phase(op_result: OperationResult):
        """Stub the orchestrator's two-phase run so routing can be asserted alone."""
        return patch.object(
            PlayImportOrchestrator,
            "import_plays_two_phase",
            new_callable=AsyncMock,
            return_value=op_result,
        )

    @pytest.mark.parametrize(
        ("mode", "kwargs", "expected"),
        [
            (
                "recent",
                {"limit": 250},
                LastfmImportParams(limit=250),
            ),
            (
                "recent",
                {},
                LastfmImportParams(limit=1000),
            ),
            (
                "incremental",
                {"username": "someone"},
                LastfmImportParams(username="someone"),
            ),
            (
                "full",
                {"confirm": True},
                LastfmImportParams(limit=50000),
            ),
        ],
    )
    async def test_lastfm_modes_build_expected_params(self, mode, kwargs, expected):
        """Each Last.fm mode differs only in the params it hands the runner."""
        uow = _uow()
        op_result = OperationResult(operation_name="test")
        use_case = ImportTracksUseCase()
        cmd = ImportTracksCommand(
            user_id="test-user", service="lastfm", mode=mode, **kwargs
        )

        with self._patched_two_phase(op_result) as runner:
            result = await use_case.execute(cmd, uow)

        assert result.mode == mode
        assert runner.call_args.kwargs["params"] == expected
        uow.get_play_import_provider().create_play_importer.assert_awaited_once_with(
            "lastfm", "api", uow
        )

    async def test_spotify_file_builds_file_params_and_kind(self):
        """A file import is the only branch that asks for the 'file' importer."""
        uow = _uow()
        op_result = OperationResult(operation_name="test")
        use_case = ImportTracksUseCase()
        cmd = ImportTracksCommand(
            user_id="test-user",
            service="spotify",
            mode="file",
            file_path=Path("/data/test.json"),
        )

        with self._patched_two_phase(op_result) as runner:
            result = await use_case.execute(cmd, uow)

        assert result.service == "spotify"
        assert result.mode == "file"
        assert runner.call_args.kwargs["params"] == SpotifyImportParams(
            file_path=Path("/data/test.json")
        )
        uow.get_play_import_provider().create_play_importer.assert_awaited_once_with(
            "spotify", "file", uow
        )

    @pytest.mark.parametrize(
        ("limit", "expected_limit"),
        [
            (None, 50),
            (10, 10),
            # 0 is falsy, so it takes the default before the clamp sees it
            (0, 50),
            (-5, 1),
            (500, 50),
        ],
    )
    @pytest.mark.parametrize("mode", ["recent", "incremental"])
    async def test_spotify_api_modes_clamp_limit(self, mode, limit, expected_limit):
        """Both API spellings share one branch, and the limit is clamped to 1..50."""
        uow = _uow()
        op_result = OperationResult(operation_name="test")
        use_case = ImportTracksUseCase()
        cmd = ImportTracksCommand(
            user_id="test-user",
            service="spotify",
            mode=mode,
            limit=limit,
            additional_options={"force": True},
        )

        with self._patched_two_phase(op_result) as runner:
            _ = await use_case.execute(cmd, uow)

        assert runner.call_args.kwargs["params"] == SpotifyRecentImportParams(
            limit=expected_limit, force=True
        )

    @pytest.mark.parametrize("mode", ["recent", "incremental"])
    async def test_apple_modes_build_force_only_params(self, mode):
        """Apple carries no limit: the importer's prefix-diff bounds the ingest."""
        uow = _uow()
        op_result = OperationResult(operation_name="test")
        use_case = ImportTracksUseCase()
        cmd = ImportTracksCommand(user_id="test-user", service="apple", mode=mode)

        with self._patched_two_phase(op_result) as runner:
            result = await use_case.execute(cmd, uow)

        assert result.service == "apple"
        assert runner.call_args.kwargs["params"] == AppleRecentImportParams(force=False)

    async def test_unconfirmed_full_history_is_cancelled(self):
        """An unconfirmed full history import never reaches the runner."""
        uow = _uow()
        use_case = ImportTracksUseCase()
        cmd = ImportTracksCommand(
            user_id="test-user", service="lastfm", mode="full", confirm=False
        )

        with self._patched_two_phase(OperationResult(operation_name="test")) as runner:
            result = await use_case.execute(cmd, uow)

        runner.assert_not_awaited()
        assert result.operation_result.metadata["cancelled"] is True
        assert result.operation_result.summary_metrics.get("status") == 0

    async def test_missing_file_path_is_reported_as_failure(self):
        """The file-mode guard survives a command that dodged validation."""
        uow = AsyncMock()
        use_case = ImportTracksUseCase()
        # Frozen commands cannot normally lose their path; the guard is
        # defence-in-depth for a caller that bypasses __attrs_post_init__.
        with patch.object(ImportTracksCommand, "__attrs_post_init__", lambda _: None):
            cmd = ImportTracksCommand(
                user_id="test-user", service="spotify", mode="file"
            )

        with pytest.raises(
            ValueError, match="file_path is required for Spotify file imports"
        ):
            _ = await use_case._execute_import(cmd, uow, NullProgressEmitter())

    @pytest.mark.parametrize(
        ("service", "mode", "message"),
        [
            ("lastfm", "file", "LastFM service doesn't support mode: file"),
            ("spotify", "full", "Spotify service doesn't support mode: full"),
        ],
    )
    async def test_unsupported_mode_raises(self, service, mode, message):
        """Unreachable-by-construction combos still raise rather than fall through."""
        uow = AsyncMock()
        use_case = ImportTracksUseCase()
        with patch.object(ImportTracksCommand, "__attrs_post_init__", lambda _: None):
            cmd = ImportTracksCommand(user_id="test-user", service=service, mode=mode)

        with pytest.raises(ValueError, match=message):
            _ = await use_case._execute_import(cmd, uow, NullProgressEmitter())

    async def test_result_success_rate_property(self):
        """Test that ImportTracksResult.success_rate reads from metrics."""
        op_result = OperationResult(operation_name="test")
        op_result.summary_metrics.add(
            "success_rate", 95.5, "Success Rate", format="percent", significance=1
        )

        result = ImportTracksResult(
            operation_result=op_result,
            service="lastfm",
            mode="recent",
        )

        assert result.success_rate == 95.5

    async def test_result_success_rate_default_zero(self):
        """Test that success_rate defaults to 0.0 when metric absent."""
        op_result = OperationResult(operation_name="test")

        result = ImportTracksResult(
            operation_result=op_result,
            service="lastfm",
            mode="recent",
        )

        assert result.success_rate == 0.0
