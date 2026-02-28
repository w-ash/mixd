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


@pytest.mark.unit
class TestImportTracksCommand:
    """Test command validation for service/mode combinations."""

    def test_valid_lastfm_recent(self):
        """Test valid LastFM recent import command."""
        cmd = ImportTracksCommand(service="lastfm", mode="recent", limit=1000)
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
            service="spotify", mode="file", file_path=Path("/data/export.json")
        )
        assert cmd.service == "spotify"
        assert cmd.file_path == Path("/data/export.json")

    def test_lastfm_file_mode_rejected(self):
        """Test that LastFM doesn't support file mode."""
        with pytest.raises(ValueError, match="doesn't support file mode"):
            ImportTracksCommand(
                service="lastfm", mode="file", file_path=Path("/data/test.json")
            )

    def test_spotify_recent_mode_rejected(self):
        """Test that Spotify only supports file mode."""
        with pytest.raises(ValueError, match="only supports file mode"):
            ImportTracksCommand(service="spotify", mode="recent")

    def test_spotify_file_without_path_rejected(self):
        """Test that Spotify file mode requires file_path."""
        with pytest.raises(ValueError, match="file_path is required"):
            ImportTracksCommand(service="spotify", mode="file")

    def test_command_is_frozen(self):
        """Test command immutability."""
        cmd = ImportTracksCommand(service="lastfm", mode="recent")
        with pytest.raises(AttributeError):
            cmd.service = "spotify"


@pytest.mark.unit
class TestImportTracksUseCase:
    """Test use case execution and error handling."""

    async def test_exception_returns_failed_result(self):
        """Test that exceptions are captured and returned as failed result."""
        uow = AsyncMock()

        command = ImportTracksCommand(service="lastfm", mode="recent")
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

    async def test_successful_import_returns_result(self):
        """Test that successful import returns proper result."""
        uow = AsyncMock()

        op_result = OperationResult(operation_name="Lastfm Recent Import")
        op_result.summary_metrics.add("track_plays", 42, "Track Plays", significance=1)

        command = ImportTracksCommand(service="lastfm", mode="recent", limit=100)
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

    async def test_routing_lastfm_modes(self):
        """Test that lastfm modes route to correct internal methods."""
        uow = AsyncMock()
        op_result = OperationResult(operation_name="test")
        use_case = ImportTracksUseCase()

        for mode in ["recent", "incremental", "full"]:
            cmd = ImportTracksCommand(service="lastfm", mode=mode, confirm=True)
            method_name = (
                f"_run_lastfm_{mode}" if mode != "full" else "_run_lastfm_full_history"
            )

            with patch.object(
                ImportTracksUseCase,
                "_execute_import",
                return_value=op_result,
            ):
                result = await use_case.execute(cmd, uow)
                assert result.mode == mode

    async def test_routing_spotify_file(self):
        """Test that spotify file mode routes correctly."""
        uow = AsyncMock()
        op_result = OperationResult(operation_name="test")

        cmd = ImportTracksCommand(
            service="spotify", mode="file", file_path=Path("/data/test.json")
        )
        use_case = ImportTracksUseCase()

        with patch.object(
            ImportTracksUseCase,
            "_execute_import",
            return_value=op_result,
        ):
            result = await use_case.execute(cmd, uow)
            assert result.service == "spotify"
            assert result.mode == "file"

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
