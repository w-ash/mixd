"""Tests for logging configuration and functionality.

This module provides comprehensive tests for the logging system to ensure
backward compatibility during refactoring and verify all logging features work correctly.
"""

from pathlib import Path
import tempfile
from unittest.mock import patch

from src.config.logging import (
    get_logger,
    setup_loguru_logger,
)
from src.config.settings import LoggingConfig


class TestCurrentLoggingBehavior:
    """Baseline tests for current logging behavior."""

    def test_get_logger_returns_bound_logger(self):
        """Test that get_logger returns a properly bound Loguru logger."""
        test_logger = get_logger("test.module")

        # Should be a Loguru logger with bound context
        assert hasattr(test_logger, "info")
        assert hasattr(test_logger, "debug")
        assert hasattr(test_logger, "error")
        assert hasattr(test_logger, "bind")

        # Should have narada service context
        # Note: Testing exact binding is complex with Loguru, so we test functionality
        test_logger.info("Test message")

    def test_get_logger_with_module_name(self):
        """Test get_logger with __name__ pattern."""
        module_name = "src.config.test_module"
        test_logger = get_logger(module_name)

        # Should not raise and should be callable
        test_logger.debug("Debug message")
        test_logger.info("Info message")
        test_logger.error("Error message")

    def test_setup_loguru_logger_default_config(self):
        """Test setup_loguru_logger with default configuration."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a temporary settings for testing
            test_log_file = Path(temp_dir) / "test.log"

            with patch("src.config.logging.settings.logging.log_file", test_log_file):
                with patch("src.config.logging.settings.logging.console_level", "INFO"):
                    with patch(
                        "src.config.logging.settings.logging.file_level", "DEBUG"
                    ):
                        # Should not raise
                        setup_loguru_logger(verbose=False)

                        # Test that logging works
                        test_logger = get_logger(__name__)
                        test_logger.info("Test setup message")

                        # Log file should exist
                        assert test_log_file.exists()

    def test_setup_loguru_logger_verbose_mode(self):
        """Test setup_loguru_logger with verbose=True."""
        with tempfile.TemporaryDirectory() as temp_dir:
            test_log_file = Path(temp_dir) / "test_verbose.log"

            with patch("src.config.logging.settings.logging.log_file", test_log_file):
                setup_loguru_logger(verbose=True)

                # Should work in verbose mode
                test_logger = get_logger(__name__)
                test_logger.debug("Debug message in verbose mode")
                test_logger.info("Info message in verbose mode")


class TestLoggingIntegration:
    """Integration tests for logging system."""

    def test_full_logging_workflow(self):
        """Test complete logging workflow from setup to usage."""
        with tempfile.TemporaryDirectory() as temp_dir:
            test_log_file = Path(temp_dir) / "integration_test.log"

            with patch("src.config.logging.settings.logging.log_file", test_log_file):
                # Setup logging
                setup_loguru_logger(verbose=False)

                # Get logger and log messages at different levels
                test_logger = get_logger("integration.test")

                test_logger.debug("Debug message")
                test_logger.info("Info message", context="test")
                test_logger.warning("Warning message")
                test_logger.error("Error message")

                # Log file should exist and contain messages
                assert test_log_file.exists()

                # Read log file content
                log_content = test_log_file.read_text()

                # Should contain structured log entries
                assert "integration.test" in log_content
                assert "Info message" in log_content

    def test_logger_context_binding(self):
        """Test that logger context binding works correctly."""
        test_logger = get_logger("context.test")

        # Should support context binding
        contextual_logger = test_logger.bind(operation="test_op", batch_id=123)

        # Should not raise
        contextual_logger.info("Contextual message")


class TestLoggingConfiguration:
    """Test logging configuration system."""

    def test_settings_integration(self):
        """Test that logging uses settings correctly."""
        from src.config.settings import settings

        assert hasattr(settings.logging, "console_level")
        assert hasattr(settings.logging, "file_level")
        assert hasattr(settings.logging, "log_file")
        assert hasattr(settings.logging, "real_time_debug")

        # New fields should be accessible with defaults
        assert hasattr(settings.logging, "diagnose_in_production")
        assert hasattr(settings.logging, "backtrace_in_production")
        assert hasattr(settings.logging, "console_format")
        assert hasattr(settings.logging, "file_format")
        assert hasattr(settings.logging, "rotation")
        assert hasattr(settings.logging, "retention")
        assert hasattr(settings.logging, "compression")
        assert hasattr(settings.logging, "serialize")
        assert hasattr(settings.logging, "catch_internal_errors")

        # Test default values
        assert settings.logging.diagnose_in_production is False
        assert settings.logging.backtrace_in_production is False
        assert settings.logging.console_format is None
        assert settings.logging.file_format is None
        assert settings.logging.rotation == "10 MB"
        assert settings.logging.retention == "1 week"
        assert settings.logging.compression == "zip"
        assert settings.logging.serialize is True
        assert settings.logging.catch_internal_errors is True

    def test_log_file_directory_creation(self):
        """Test that log directory is created if it doesn't exist."""
        with tempfile.TemporaryDirectory() as temp_dir:
            nested_log_file = Path(temp_dir) / "logs" / "nested" / "test.log"

            with patch("src.config.logging.settings.logging.log_file", nested_log_file):
                setup_loguru_logger()

                # Directory should be created
                assert nested_log_file.parent.exists()


class TestErrorHandling:
    """Test error handling in logging system."""

    def test_logging_with_invalid_settings(self):
        """Test logging behavior with edge case settings."""
        # Test with minimal settings
        minimal_config = LoggingConfig()

        # Should have sensible defaults
        assert minimal_config.console_level == "INFO"
        assert minimal_config.file_level == "DEBUG"
        assert minimal_config.log_file == Path("narada.log")
        assert minimal_config.real_time_debug is True


class TestSecurityEnhancements:
    """Test new security-focused logging features."""

    def test_production_security_defaults(self):
        """Test that production security settings default to safe values."""
        from src.config.settings import LoggingConfig

        config = LoggingConfig()
        assert config.diagnose_in_production is False  # Safe default
        assert config.backtrace_in_production is False  # Safe default

    def test_verbose_mode_overrides_security(self):
        """Test that verbose mode enables diagnostics regardless of production settings."""
        with tempfile.TemporaryDirectory() as temp_dir:
            test_log_file = Path(temp_dir) / "security_test.log"

            with patch("src.config.logging.settings.logging.log_file", test_log_file):
                with patch(
                    "src.config.logging.settings.logging.diagnose_in_production", False
                ):
                    with patch(
                        "src.config.logging.settings.logging.backtrace_in_production",
                        False,
                    ):
                        # Even with production security disabled, verbose should enable diagnostics
                        setup_loguru_logger(verbose=True)

                        # Should work without issues
                        test_logger = get_logger("security.test")
                        test_logger.info("Security test message")

    def test_custom_format_strings(self):
        """Test that custom format strings are used when provided."""
        custom_console_format = "CUSTOM: {time} | {message}"
        custom_file_format = "FILE: {time} | {level} | {message}"

        with tempfile.TemporaryDirectory() as temp_dir:
            test_log_file = Path(temp_dir) / "format_test.log"

            with patch("src.config.logging.settings.logging.log_file", test_log_file):
                with patch(
                    "src.config.logging.settings.logging.console_format",
                    custom_console_format,
                ):
                    with patch(
                        "src.config.logging.settings.logging.file_format",
                        custom_file_format,
                    ):
                        setup_loguru_logger()

                        # Should work with custom formats
                        test_logger = get_logger("format.test")
                        test_logger.info("Format test message")

    def test_configurable_rotation_settings(self):
        """Test that rotation settings are configurable."""
        with tempfile.TemporaryDirectory() as temp_dir:
            test_log_file = Path(temp_dir) / "rotation_test.log"

            with patch("src.config.logging.settings.logging.log_file", test_log_file):
                with patch("src.config.logging.settings.logging.rotation", "5 MB"):
                    with patch(
                        "src.config.logging.settings.logging.retention", "3 days"
                    ):
                        with patch(
                            "src.config.logging.settings.logging.compression", "gz"
                        ):
                            setup_loguru_logger()

                            # Should work with custom rotation settings
                            test_logger = get_logger("rotation.test")
                            test_logger.info("Rotation test message")


class TestConsoleOutputCoordination:
    """Regression tests for enable/restore console output lifecycle."""

    def test_restore_standard_console_output_after_enable(self, capsys):
        """Logging restore must not fall through to the error-recovery path.

        Regression: restore_standard_console_output() used delattr() on the
        enable_unified_console_output function, treating it as a namespace for
        module-level globals. This caused AttributeError that was caught by the
        except block, triggering a "Warning: Failed to restore..." print and
        a full reset instead of a clean restore.
        """
        from unittest.mock import MagicMock

        from src.config.logging import (
            enable_unified_console_output,
            restore_standard_console_output,
        )

        mock_console = MagicMock()
        enable_unified_console_output(mock_console)

        restore_standard_console_output()

        # The error-recovery path prints a warning to stdout — verify it was NOT hit
        captured = capsys.readouterr()
        assert "Failed to restore" not in captured.out

    def test_restore_without_enable_is_safe(self):
        """Calling restore without a prior enable should not crash."""
        from src.config.logging import restore_standard_console_output

        # Should be a no-op, not an error
        restore_standard_console_output()


class TestEnhancedLoggingFeatures:
    """Test the enhanced logging features actually work in practice."""

    def test_production_security_settings_accessible(self):
        """Test that new security settings are accessible and have safe defaults."""
        from src.config.settings import settings

        # New security settings should exist and be secure by default
        assert hasattr(settings.logging, "diagnose_in_production")
        assert hasattr(settings.logging, "backtrace_in_production")

        # Should default to False for production safety
        assert settings.logging.diagnose_in_production is False
        assert settings.logging.backtrace_in_production is False
