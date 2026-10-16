"""Unit tests for timed_query async context manager.

Tests the timer envelope that consolidates execution timing and error logging
across read-side use cases.
"""

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
import structlog

from src.application.use_cases._shared.timed_execution import timed_query
from src.application.utilities import timing


class TestTimedQuerySuccess:
    """Test timed_query on successful operation."""

    async def test_yielded_timer_reports_elapsed_milliseconds_on_stop(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """stop() returns whole milliseconds since entry and records them."""
        clock = Mock(
            now=Mock(
                side_effect=[
                    datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
                    datetime(2025, 1, 1, 12, 0, 1, 250_000, tzinfo=UTC),
                ]
            )
        )
        monkeypatch.setattr(timing, "datetime", clock)

        async with timed_query("Test operation") as timer:
            elapsed_ms = timer.stop()

        assert elapsed_ms == 1250
        assert timer.elapsed_ms == 1250

    async def test_success_path_logs_no_error(self):
        with structlog.testing.capture_logs() as logs:
            async with timed_query("Test operation") as timer:
                timer.stop()

        assert [e for e in logs if e["log_level"] == "error"] == []


class TestTimedQueryException:
    """Test timed_query on exception paths."""

    async def test_re_raises_exception(self):
        """Test that exceptions are re-raised after logging."""
        with pytest.raises(ValueError, match="Test error"):
            async with timed_query("Test operation"):
                raise ValueError("Test error")

    async def test_error_log_names_the_operation_error_and_context(self):
        with structlog.testing.capture_logs() as logs, pytest.raises(ValueError):
            async with timed_query(
                "Track retrieval",
                error_log_context={"user_id": "test-user", "limit": 100},
            ):
                raise ValueError("Database connection failed")

        errors = [e for e in logs if e["log_level"] == "error"]
        assert len(errors) == 1
        assert errors[0]["event"] == "Track retrieval failed"
        assert errors[0]["error"] == "Database connection failed"
        assert errors[0]["user_id"] == "test-user"
        assert errors[0]["limit"] == 100

    async def test_no_context_on_exception(self):
        """Without error_log_context the original exception still propagates."""
        with pytest.raises(RuntimeError):
            async with timed_query("Operation"):
                raise RuntimeError("Something went wrong")
