"""Unit tests for CLI helper utilities.

Tests cover:
- Date parsing and validation
- File path validation
- User input prompting
- Progress context integration
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer

from src.interface.shared.cli_helpers import (
    parse_date_string,
    prompt_batch_size,
    run_import_with_progress,
    validate_date_range,
    validate_file_path,
)


class TestParseDateString:
    """Test date string parsing with timezone handling."""

    def test_parse_valid_date_string(self):
        """Parse valid YYYY-MM-DD format returns UTC datetime."""
        result = parse_date_string("2025-03-15", "test-date")
        assert result == datetime(2025, 3, 15, tzinfo=UTC)

    def test_parse_none_returns_none(self):
        """Parsing None returns None."""
        result = parse_date_string(None, "test-date")
        assert result is None

    def test_parse_empty_string_returns_none(self):
        """Parsing empty string returns None."""
        result = parse_date_string("", "test-date")
        assert result is None

    def test_parse_invalid_format_raises_exit(self):
        """Invalid date format raises typer.Exit."""
        with pytest.raises(typer.Exit) as exc_info:
            parse_date_string("2025/03/15", "test-date")
        assert exc_info.value.exit_code == 1

    def test_parse_invalid_date_raises_exit(self):
        """Invalid date value raises typer.Exit."""
        with pytest.raises(typer.Exit) as exc_info:
            parse_date_string("2025-13-45", "invalid-date")
        assert exc_info.value.exit_code == 1

    def test_parse_sets_utc_timezone(self):
        """Parsed date has UTC timezone."""
        result = parse_date_string("2025-01-01", "test")
        assert result.tzinfo == UTC


class TestValidateDateRange:
    """Test date range validation."""

    def test_valid_date_range_passes(self):
        """Valid from < to date range passes without error."""
        from_date = datetime(2025, 1, 1, tzinfo=UTC)
        to_date = datetime(2025, 12, 31, tzinfo=UTC)
        # Should not raise
        validate_date_range(from_date, to_date)

    def test_equal_dates_passes(self):
        """Equal from and to dates pass without error."""
        date = datetime(2025, 1, 1, tzinfo=UTC)
        # Should not raise
        validate_date_range(date, date)

    def test_inverted_range_raises_exit(self):
        """From date after to date raises typer.Exit."""
        from_date = datetime(2025, 12, 31, tzinfo=UTC)
        to_date = datetime(2025, 1, 1, tzinfo=UTC)
        with pytest.raises(typer.Exit) as exc_info:
            validate_date_range(from_date, to_date)
        assert exc_info.value.exit_code == 1

    def test_none_dates_passes(self):
        """None dates pass without error."""
        # Should not raise
        validate_date_range(None, None)

    def test_partial_none_passes(self):
        """One None date passes without error."""
        date = datetime(2025, 1, 1, tzinfo=UTC)
        # Should not raise
        validate_date_range(None, date)
        validate_date_range(date, None)


class TestPromptBatchSize:
    """Test batch size prompting."""

    @patch("src.interface.shared.cli_helpers.Prompt.ask")
    def test_prompt_with_valid_integer_returns_int(self, mock_ask):
        """User input of valid integer returns int."""
        mock_ask.return_value = "100"
        result = prompt_batch_size()
        assert result == 100

    @patch("src.interface.shared.cli_helpers.Prompt.ask")
    def test_prompt_with_empty_returns_none(self, mock_ask):
        """User input of empty string returns None (default)."""
        mock_ask.return_value = ""
        result = prompt_batch_size()
        assert result is None

    @patch("src.interface.shared.cli_helpers.Prompt.ask")
    def test_prompt_uses_correct_message(self, mock_ask):
        """Prompt message is descriptive."""
        mock_ask.return_value = ""
        prompt_batch_size()
        mock_ask.assert_called_once_with(
            "Batch size (leave empty for default)",
            default="",
        )


class TestValidateFilePath:
    """Test file path validation."""

    def test_valid_file_path_passes(self, tmp_path):
        """Valid existing file passes without error."""
        test_file = tmp_path / "test.json"
        test_file.write_text("{}")
        # Should not raise
        validate_file_path(test_file)

    def test_missing_file_raises_exit(self, tmp_path):
        """Missing file raises typer.Exit."""
        missing_file = tmp_path / "missing.json"
        with pytest.raises(typer.Exit) as exc_info:
            validate_file_path(missing_file)
        assert exc_info.value.exit_code == 1

    def test_directory_raises_exit(self, tmp_path):
        """Directory path raises typer.Exit."""
        with pytest.raises(typer.Exit) as exc_info:
            validate_file_path(tmp_path)
        assert exc_info.value.exit_code == 1


class TestRunImportWithProgress:
    """Test import execution with progress context."""

    @pytest.mark.asyncio
    async def test_run_import_executes_with_progress_context(self):
        """Import executes within progress coordination context."""
        # Mock the entire import pipeline
        with patch(
            "src.interface.shared.cli_helpers.progress_coordination_context"
        ) as mock_context, patch(
            "src.interface.shared.cli_helpers.run_import"
        ) as mock_run_import, patch(
            "src.interface.shared.cli_helpers.asyncio.run"
        ) as mock_async_run:
            # Setup mocks
            mock_progress_manager = MagicMock()
            mock_ctx = MagicMock()
            mock_ctx.get_progress_manager.return_value = mock_progress_manager
            mock_context.return_value.__aenter__.return_value = mock_ctx

            # Mock result
            from src.domain.entities import OperationResult

            expected_result = OperationResult(operation_name="test")
            mock_run_import.return_value = expected_result

            # Execute (the function calls asyncio.run internally)
            mock_async_run.return_value = expected_result

            result = run_import_with_progress(
                service="spotify",
                mode="file",
                file_path=Path("/test/file.json"),
                batch_size=100,
            )

            # Verify asyncio.run was called
            assert mock_async_run.called
            assert result == expected_result

    @pytest.mark.asyncio
    async def test_run_import_passes_kwargs_to_use_case(self):
        """Import passes additional kwargs to run_import use case."""
        from pathlib import Path

        with patch(
            "src.interface.shared.cli_helpers.progress_coordination_context"
        ) as mock_context, patch(
            "src.interface.shared.cli_helpers.run_import"
        ) as mock_run_import, patch(
            "src.interface.shared.cli_helpers.asyncio.run"
        ) as mock_async_run:
            # Setup mocks
            mock_progress_manager = MagicMock()
            mock_ctx = MagicMock()
            mock_ctx.get_progress_manager.return_value = mock_progress_manager
            mock_context.return_value.__aenter__.return_value = mock_ctx

            from src.domain.entities import OperationResult

            expected_result = OperationResult(operation_name="test")
            mock_run_import.return_value = expected_result
            mock_async_run.return_value = expected_result

            # Execute with kwargs
            test_path = Path("/test/file.json")
            result = run_import_with_progress(
                service="spotify",
                mode="file",
                file_path=test_path,
                batch_size=200,
            )

            # Verify asyncio.run was called
            assert mock_async_run.called
            assert result == expected_result
