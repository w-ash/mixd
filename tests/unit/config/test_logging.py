"""Tests for structlog-based logging configuration.

Verifies setup_logging(), get_logger(), logging_context(), per-workflow-run
JSONL sinks, Rich progress console coordination, and rotation/retention helpers.
"""

import json
import logging
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch

import pytest
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
from src.config.settings import LoggingConfig


class TestSetupLogging:
    """Test setup_logging() configures handlers correctly."""

    def test_setup_creates_console_and_file_handlers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_log_file = Path(temp_dir) / "test.log"
            with patch("src.config.logging.settings.logging.log_file", test_log_file):
                setup_logging(verbose=False)

                root = logging.getLogger()
                handler_types = [type(h).__name__ for h in root.handlers]
                assert "StreamHandler" in handler_types
                assert "RotatingFileHandler" in handler_types

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

    def test_setup_creates_log_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            nested = Path(temp_dir) / "logs" / "nested" / "test.log"
            with patch("src.config.logging.settings.logging.log_file", nested):
                setup_logging()
                assert nested.parent.exists()

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
                assert "timestamp" in entry
                assert "logger" in entry

                # Must NOT have loguru's nested structure
                assert "record" not in entry


class TestGetLogger:
    """Test get_logger() factory."""

    def test_returns_bound_logger(self):
        logger = get_logger("test.module")
        assert hasattr(logger, "info")
        assert hasattr(logger, "debug")
        assert hasattr(logger, "error")
        assert hasattr(logger, "bind")

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
            # Inside: contextvars should be set
            import contextvars

            ctx = contextvars.copy_context()
            ctx_keys = {k.name for k in ctx if k.name.startswith("structlog_")}
            assert "structlog_workflow_id" in ctx_keys
            assert "structlog_run_id" in ctx_keys

        # Outside: contextvars should be cleared (reset to sentinel Ellipsis)
        ctx = contextvars.copy_context()
        ctx_keys = {
            k.name for k in ctx if k.name.startswith("structlog_") and ctx[k] is not ...
        }
        assert "structlog_workflow_id" not in ctx_keys
        assert "structlog_run_id" not in ctx_keys

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

        import contextvars

        ctx = contextvars.copy_context()
        for k in ctx:
            if k.name == "structlog_key":
                assert ctx[k] is ..., "key should be unbound after exception"


class TestWorkflowRunLogger:
    """Test per-workflow-run JSONL sink."""

    def test_add_and_remove_run_logger(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("src.config.logging.settings.workflow_log_dir", temp_dir):
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

                # Cleanup
                remove_workflow_run_logger(handle)

    def test_run_filter_excludes_other_runs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("src.config.logging.settings.workflow_log_dir", temp_dir):
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

    def test_remove_nonexistent_handle_is_safe(self):
        remove_workflow_run_logger("nonexistent")


class TestConsoleOutputCoordination:
    """Test Rich progress bar console coordination."""

    def test_enable_and_restore_lifecycle(self, capsys):
        setup_logging()
        mock_console = MagicMock()
        enable_unified_console_output(mock_console)

        restore_standard_console_output()

        captured = capsys.readouterr()
        assert "Failed" not in captured.out

    def test_restore_without_enable_is_safe(self):
        restore_standard_console_output()


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


class TestLoggingConfigDefaults:
    """Test LoggingConfig settings defaults."""

    def test_sensible_defaults(self):
        config = LoggingConfig()
        assert config.console_level == "INFO"
        assert config.file_level == "DEBUG"
        assert config.log_file == Path("mixd.log")
        assert config.rotation == "10 MB"
        assert config.retention == "1 week"
        assert config.prefect_log_level == "DEBUG"
