"""Unit tests for ImportTracksUseCase.

Tests command validation for service/mode combinations and the routing logic
that delegates to service-specific importers.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.application.use_cases.import_play_history import (
    ImportTracksCommand,
    ImportTracksResult,
    ImportTracksUseCase,
)
from src.domain.entities import OperationResult
from src.domain.entities.progress import NullProgressEmitter
from src.domain.repositories.play import (
    RECENTLY_PLAYED_PAGE_LIMIT,
    AppleRecentImportParams,
    LastfmImportParams,
    SpotifyImportParams,
    SpotifyRecentImportParams,
)


class TestImportTracksCommand:
    """Test command validation for service/mode combinations."""

    def test_valid_lastfm_recent(self):
        """Test valid LastFM recent import command."""
        cmd = ImportTracksCommand(
            user_id="test-user", service="lastfm", mode="recent", limit=1000
        )
        assert cmd.service == "lastfm"
        assert cmd.mode == "recent"

    def test_valid_lastfm_incremental(self):
        """Test valid LastFM incremental import command."""
        cmd = ImportTracksCommand(
            service="lastfm", mode="incremental", user_id="testuser"
        )
        assert cmd.mode == "incremental"

    def test_valid_lastfm_full(self):
        """Test valid LastFM full history import command."""
        cmd = ImportTracksCommand(
            service="lastfm", mode="full", user_id="testuser", confirm=True
        )
        assert cmd.mode == "full"
        assert cmd.confirm is True

    def test_valid_spotify_file(self):
        """Test valid Spotify file import command."""
        cmd = ImportTracksCommand(
            user_id="test-user",
            service="spotify",
            mode="file",
            file_path=Path("/data/export.json"),
        )
        assert cmd.service == "spotify"
        assert cmd.file_path == Path("/data/export.json")

    def test_lastfm_file_mode_rejected(self):
        """Test that LastFM doesn't support file mode."""
        with pytest.raises(ValueError, match="doesn't support file mode"):
            ImportTracksCommand(
                user_id="test-user",
                service="lastfm",
                mode="file",
                file_path=Path("/data/test.json"),
            )

    @pytest.mark.parametrize("mode", ["recent", "incremental"])
    def test_spotify_api_modes_accepted(self, mode):
        """Recently-played polling (v0.10.1) accepts both API mode spellings."""
        cmd = ImportTracksCommand(user_id="test-user", service="spotify", mode=mode)
        assert cmd.mode == mode

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

    def test_command_is_frozen(self):
        """Test command immutability."""
        cmd = ImportTracksCommand(user_id="test-user", service="lastfm", mode="recent")
        with pytest.raises(AttributeError):
            cmd.service = "spotify"


class TestImportTracksUseCase:
    """Test use case execution and error handling."""

    async def test_exception_returns_failed_result(self):
        """Test that exceptions are captured and returned as failed result."""
        uow = AsyncMock()

        command = ImportTracksCommand(
            user_id="test-user", service="lastfm", mode="recent"
        )
        use_case = ImportTracksUseCase()

        # Patch internal method to raise
        with patch.object(
            ImportTracksUseCase,
            "_execute_import",
            side_effect=RuntimeError("Connection failed"),
        ):
            result = await use_case.execute(command, uow)

        assert isinstance(result, ImportTracksResult)
        assert result.service == "lastfm"
        assert result.mode == "recent"
        # Error should be in summary metrics
        error_metric = next(
            (
                m
                for m in result.operation_result.summary_metrics.metrics
                if m.name == "errors"
            ),
            None,
        )
        assert error_metric is not None
        assert error_metric.value == 1

    async def test_quota_exhaustion_propagates_instead_of_failed_result(self):
        """PDR-003 quota exhaustion must escape like the auth errors do — a
        soft-failure result would bury the outage and let callers keep going."""
        from src.domain.exceptions import SpotifyQuotaExhaustedError

        uow = AsyncMock()
        command = ImportTracksCommand(
            user_id="test-user", service="spotify", mode="recent"
        )
        use_case = ImportTracksUseCase()

        with (
            patch.object(
                ImportTracksUseCase,
                "_execute_import",
                side_effect=SpotifyQuotaExhaustedError(),
            ),
            pytest.raises(SpotifyQuotaExhaustedError),
        ):
            _ = await use_case.execute(command, uow)

    async def test_successful_import_returns_result(self):
        """Test that successful import returns proper result."""
        uow = AsyncMock()

        op_result = OperationResult(operation_name="Lastfm Recent Import")
        op_result.summary_metrics.add("track_plays", 42, "Track Plays", significance=1)

        command = ImportTracksCommand(
            user_id="test-user", service="lastfm", mode="recent", limit=100
        )
        use_case = ImportTracksUseCase()

        with patch.object(
            ImportTracksUseCase,
            "_execute_import",
            return_value=op_result,
        ):
            result = await use_case.execute(command, uow)

        assert isinstance(result, ImportTracksResult)
        assert result.service == "lastfm"
        assert result.mode == "recent"
        assert result.execution_time_ms >= 0

    @staticmethod
    def _patched_two_phase(op_result: OperationResult):
        """Patch the shared two-phase runner so routing can be asserted alone."""
        return patch.object(
            ImportTracksUseCase,
            "_run_two_phase",
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
        uow = AsyncMock()
        op_result = OperationResult(operation_name="test")
        use_case = ImportTracksUseCase()
        cmd = ImportTracksCommand(
            user_id="test-user", service="lastfm", mode=mode, **kwargs
        )

        with self._patched_two_phase(op_result) as runner:
            result = await use_case.execute(cmd, uow)

        assert result.mode == mode
        assert runner.call_args.kwargs["params"] == expected
        assert runner.call_args.kwargs.get("kind", "api") == "api"

    async def test_spotify_file_builds_file_params_and_kind(self):
        """A file import is the only branch that asks for the 'file' importer."""
        uow = AsyncMock()
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
        assert runner.call_args.kwargs["kind"] == "file"

    @pytest.mark.parametrize(
        ("limit", "expected_limit"),
        [
            (None, RECENTLY_PLAYED_PAGE_LIMIT),
            (10, 10),
            # 0 is falsy, so it takes the default before the clamp sees it
            (0, RECENTLY_PLAYED_PAGE_LIMIT),
            (-5, 1),
            (500, RECENTLY_PLAYED_PAGE_LIMIT),
        ],
    )
    @pytest.mark.parametrize("mode", ["recent", "incremental"])
    async def test_spotify_api_modes_clamp_limit(self, mode, limit, expected_limit):
        """Both API spellings share one branch, and the limit is clamped to 1..50."""
        uow = AsyncMock()
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
        uow = AsyncMock()
        op_result = OperationResult(operation_name="test")
        use_case = ImportTracksUseCase()
        cmd = ImportTracksCommand(user_id="test-user", service="apple", mode=mode)

        with self._patched_two_phase(op_result) as runner:
            result = await use_case.execute(cmd, uow)

        assert result.service == "apple"
        assert runner.call_args.kwargs["params"] == AppleRecentImportParams(force=False)

    async def test_unconfirmed_full_history_is_cancelled(self):
        """An unconfirmed full history import never reaches the runner."""
        uow = AsyncMock()
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
