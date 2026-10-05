"""Tests for structlog-based logging configuration.

Verifies setup_logging(), get_logger(), logging_context(), per-workflow-run
JSONL sinks, Rich progress console coordination, and rotation/retention helpers.
"""

from datetime import datetime
import io
import json
import logging
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

import pytest
from rich.console import Console
import structlog

from src.config.logging import (
    _parse_retention,
    _parse_rotation,
    add_workflow_run_logger,
    enable_unified_console_output,
    get_logger,
    logging_context,
    remove_workflow_run_logger,
    restore_standard_console_output,
    setup_logging,
)


@pytest.fixture(autouse=True)
def _restore_root_logger():
    """Put the root logger's handlers and level back after each test."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


class TestSetupLogging:
    """Test setup_logging() configures handlers correctly."""

    def test_setup_verbose_sets_debug_console(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_log_file = Path(temp_dir) / "test.log"
            with patch("src.config.logging.settings.logging.log_file", test_log_file):
                setup_logging(verbose=True)

                root = logging.getLogger()
                stream_handlers = [
                    h
                    for h in root.handlers
                    if isinstance(h, logging.StreamHandler)
                    and not isinstance(h, logging.FileHandler)
                ]
                assert stream_handlers
                assert stream_handlers[0].level == logging.DEBUG

    def test_file_handler_produces_flat_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_log_file = Path(temp_dir) / "test.log"
            with patch("src.config.logging.settings.logging.log_file", test_log_file):
                setup_logging()
                logger = get_logger("test.json")
                logger.info("flat json test", operation="verify")

                # Force flush
                for h in logging.getLogger().handlers:
                    h.flush()

                content = test_log_file.read_text().strip()
                assert content, "Log file should not be empty"

                entry = json.loads(content.split("\n")[-1])

                # Flat structure — no nesting
                assert entry["level"] == "info"
                assert entry["event"] == "flat json test"
                assert entry["operation"] == "verify"
                assert entry["service"] == "mixd"
                assert entry["logger"] == "test.json"
                assert datetime.fromisoformat(entry["timestamp"])

                # Must NOT have loguru's nested structure
                assert "record" not in entry


class TestFileSinkFallback:
    """A log sink that cannot be opened degrades to console-only, never fatally.

    Console output is the sink Fly reads; the file is a local convenience.
    Treating both as required lets the weaker one veto startup.
    """

    @staticmethod
    def _root_handlers_reset() -> None:
        logging.getLogger().handlers.clear()

    @pytest.mark.skipif(
        os.getuid() == 0, reason="root bypasses directory permissions entirely"
    )
    def test_unwritable_directory_configures_console_only(self):
        """A real read-only directory raises nothing and still logs to console."""
        stream = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            readonly_dir = Path(temp_dir) / "readonly"
            readonly_dir.mkdir()
            readonly_dir.chmod(0o555)
            try:
                with patch(
                    "src.config.logging.settings.logging.log_file",
                    readonly_dir / "mixd.log",
                ):
                    setup_logging(console_stream=stream)

                    root = logging.getLogger()
                    handler_types = [type(h).__name__ for h in root.handlers]
                    assert "StreamHandler" in handler_types
                    assert "RotatingFileHandler" not in handler_types

                    get_logger("fallback.test").info("still alive")
                    assert "still alive" in stream.getvalue()
            finally:
                self._root_handlers_reset()
                readonly_dir.chmod(0o755)

    def test_unwritable_parent_creation_configures_console_only(self):
        """The mkdir branch is guarded too, not just the handler construction."""
        stream = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            nested = Path(temp_dir) / "readonly" / "mixd.log"
            with (
                patch("src.config.logging.settings.logging.log_file", nested),
                patch.object(
                    Path, "mkdir", side_effect=PermissionError(13, "Permission denied")
                ),
            ):
                try:
                    setup_logging(console_stream=stream)

                    handler_types = [
                        type(h).__name__ for h in logging.getLogger().handlers
                    ]
                    assert "StreamHandler" in handler_types
                    assert "RotatingFileHandler" not in handler_types
                finally:
                    self._root_handlers_reset()

        assert "Permission denied" in stream.getvalue()

    def test_fallback_warning_names_path_and_reason(self):
        """The one warning identifies both the path and the OS reason."""
        stream = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = Path(temp_dir) / "mixd.log"
            with (
                patch("src.config.logging.settings.logging.log_file", log_file),
                patch(
                    "logging.handlers.RotatingFileHandler",
                    side_effect=OSError(30, "Read-only file system"),
                ),
            ):
                try:
                    setup_logging(console_stream=stream)
                finally:
                    self._root_handlers_reset()

        output = stream.getvalue()
        assert "File logging disabled" in output
        assert str(log_file) in output
        assert "Read-only file system" in output

    def test_writable_path_still_attaches_file_handler(self):
        """The guard must not disable the file sink where it does work."""
        stream = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = Path(temp_dir) / "nested" / "mixd.log"
            with patch("src.config.logging.settings.logging.log_file", log_file):
                try:
                    setup_logging(console_stream=stream)
                    get_logger("fallback.test").info("written to disk")
                    for handler in logging.getLogger().handlers:
                        handler.flush()
                finally:
                    self._root_handlers_reset()

            assert "written to disk" in log_file.read_text()
        assert "File logging disabled" not in stream.getvalue()


class TestGetLogger:
    """Test get_logger() factory."""

    def test_logger_has_service_and_module_context(self):
        with structlog.testing.capture_logs() as captured:
            logger = get_logger("my.module")
            logger.info("hello")

        assert len(captured) >= 1
        entry = captured[-1]
        assert entry["service"] == "mixd"
        assert entry["module"] == "my.module"
        assert entry["event"] == "hello"


class TestLoggingContext:
    """Test logging_context() context manager."""

    def test_binds_and_unbinds_context(self):
        """Verify contextvars are bound inside and unbound outside the block."""
        structlog.contextvars.clear_contextvars()

        with logging_context(workflow_id=42, run_id="abc"):
            assert structlog.contextvars.get_contextvars() == {
                "workflow_id": 42,
                "run_id": "abc",
            }

        assert structlog.contextvars.get_contextvars() == {}

    def test_context_appears_in_json_output(self):
        """Verify contextvars merge into flat JSON log output."""
        with tempfile.TemporaryDirectory() as temp_dir:
            test_log_file = Path(temp_dir) / "ctx.log"
            with patch("src.config.logging.settings.logging.log_file", test_log_file):
                setup_logging()
                structlog.contextvars.clear_contextvars()

                logger = get_logger("ctx.json")
                with logging_context(workflow_id=42):
                    logger.info("inside context")

                for h in logging.getLogger().handlers:
                    h.flush()

                content = test_log_file.read_text().strip()
                lines = [
                    line for line in content.split("\n") if "inside context" in line
                ]
                assert lines
                entry = json.loads(lines[0])
                assert entry["workflow_id"] == 42

    def test_unbinds_on_exception(self):
        structlog.contextvars.clear_contextvars()

        def _raise_inside_context():
            with logging_context(key="value"):
                raise ValueError("test")

        with pytest.raises(ValueError):
            _raise_inside_context()

        assert structlog.contextvars.get_contextvars() == {}


class TestWorkflowRunLogger:
    """Test per-workflow-run JSONL sink."""

    def test_add_and_remove_run_logger(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch("src.config.logging.settings.workflow_log_dir", temp_dir),
                patch(
                    "src.config.logging.settings.logging.log_file",
                    Path(temp_dir) / "mixd.log",
                ),
            ):
                setup_logging()
                handle = add_workflow_run_logger("wf_1", "run_abc")

                assert handle == "run_abc"

                # Log with matching context
                structlog.contextvars.clear_contextvars()
                structlog.contextvars.bind_contextvars(workflow_run_id="run_abc")
                logger = get_logger("workflow.test")
                logger.info("run log entry")
                structlog.contextvars.unbind_contextvars("workflow_run_id")

                # Flush handlers
                for h in logging.getLogger().handlers:
                    h.flush()

                # Check JSONL file
                log_path = Path(temp_dir) / "wf_1" / "run_abc.jsonl"
                assert log_path.exists()

                content = log_path.read_text().strip()
                assert content
                entry = json.loads(content.split("\n")[-1])
                assert entry["event"] == "run log entry"
                assert entry["workflow_run_id"] == "run_abc"

                remove_workflow_run_logger(handle)

                # A removed sink no longer receives the run's entries.
                structlog.contextvars.bind_contextvars(workflow_run_id="run_abc")
                logger.info("after removal")
                structlog.contextvars.unbind_contextvars("workflow_run_id")
                assert "after removal" not in log_path.read_text()

    def test_run_filter_excludes_other_runs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch("src.config.logging.settings.workflow_log_dir", temp_dir),
                patch(
                    "src.config.logging.settings.logging.log_file",
                    Path(temp_dir) / "mixd.log",
                ),
            ):
                setup_logging()
                handle = add_workflow_run_logger("wf_1", "run_xyz")

                # Log WITHOUT matching context
                structlog.contextvars.clear_contextvars()
                logger = get_logger("workflow.test")
                logger.info("unrelated log")

                for h in logging.getLogger().handlers:
                    h.flush()

                log_path = Path(temp_dir) / "wf_1" / "run_xyz.jsonl"
                content = log_path.read_text().strip() if log_path.exists() else ""
                assert "unrelated log" not in content

                remove_workflow_run_logger(handle)

    def test_remove_nonexistent_handle_is_a_no_op(self):
        root = logging.getLogger()
        before = list(root.handlers)

        remove_workflow_run_logger("nonexistent")

        assert root.handlers == before


class TestConsoleOutputCoordination:
    """Rich progress coordination: console logging moves onto the progress
    console while bars are live, and moves back afterwards."""

    @pytest.fixture
    def console_stream(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Configure logging with a captured console stream; no saved handler."""
        monkeypatch.setattr("src.config.logging._saved_console_handler", None)
        stream = io.StringIO()
        with patch("src.config.logging.settings.logging.log_file", tmp_path / "x.log"):
            setup_logging(console_stream=stream)
        return stream

    def test_enable_routes_structlog_and_stdlib_through_progress_console(
        self, console_stream: io.StringIO
    ):
        progress_output = io.StringIO()
        enable_unified_console_output(Console(file=progress_output, width=200))
        try:
            get_logger("coord.structlog").warning("structlog line")
            logging.getLogger("coord.stdlib").warning("stdlib line")
        finally:
            restore_standard_console_output()

        assert "structlog line" in progress_output.getvalue()
        assert "stdlib line" in progress_output.getvalue()
        # The standard console handler is detached while bars are live.
        assert "structlog line" not in console_stream.getvalue()

    def test_restore_reattaches_the_standard_console(self, console_stream: io.StringIO):
        progress_output = io.StringIO()
        enable_unified_console_output(Console(file=progress_output, width=200))
        restore_standard_console_output()

        get_logger("coord.structlog").warning("after restore")

        assert "after restore" in console_stream.getvalue()
        assert "after restore" not in progress_output.getvalue()

    def test_restore_without_enable_is_a_no_op(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("src.config.logging._saved_console_handler", None)
        root = logging.getLogger()
        before = list(root.handlers)

        restore_standard_console_output()

        assert root.handlers == before


class TestRotationHelpers:
    """Test _parse_rotation and _parse_retention."""

    def test_parse_rotation_mb(self):
        assert _parse_rotation("10 MB") == 10 * 1024 * 1024

    def test_parse_rotation_kb(self):
        assert _parse_rotation("500 KB") == 500 * 1024

    def test_parse_rotation_gb(self):
        assert _parse_rotation("1 GB") == 1024**3

    def test_parse_retention_week(self):
        assert _parse_retention("1 week") == 7

    def test_parse_retention_weeks(self):
        assert _parse_retention("2 weeks") == 14

    def test_parse_retention_days(self):
        assert _parse_retention("3 days") == 3

    def test_parse_retention_month(self):
        assert _parse_retention("1 month") == 30

    def test_parse_retention_default(self):
        assert _parse_retention("forever") == 7
