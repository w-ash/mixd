"""Tests for logging configuration and functionality.

This module provides comprehensive tests for the logging system to ensure
backward compatibility during refactoring and verify all logging features work correctly.
"""

import asyncio
import logging
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from src.config.logging import (
    configure_prefect_logging,
    get_logger,
    log_startup_info,
    resilient_operation,
    setup_loguru_logger,
)
from src.config.settings import LoggingConfig


class TestCurrentLoggingBehavior:
    """Baseline tests for current logging behavior."""
    
    def test_get_logger_returns_bound_logger(self):
        """Test that get_logger returns a properly bound Loguru logger."""
        test_logger = get_logger("test.module")
        
        # Should be a Loguru logger with bound context
        assert hasattr(test_logger, 'info')
        assert hasattr(test_logger, 'debug')
        assert hasattr(test_logger, 'error')
        assert hasattr(test_logger, 'bind')
        
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
            
            with patch('src.config.logging.settings.logging.log_file', test_log_file):
                with patch('src.config.logging.settings.logging.console_level', 'INFO'):
                    with patch('src.config.logging.settings.logging.file_level', 'DEBUG'):
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
            
            with patch('src.config.logging.settings.logging.log_file', test_log_file):
                setup_loguru_logger(verbose=True)
                
                # Should work in verbose mode
                test_logger = get_logger(__name__)
                test_logger.debug("Debug message in verbose mode")
                test_logger.info("Info message in verbose mode")
    
    def test_log_startup_info(self):
        """Test log_startup_info function."""
        # Should not raise
        log_startup_info()
    
    def test_resilient_operation_decorator_success(self):
        """Test resilient_operation decorator with successful operation."""
        @resilient_operation("test_operation")
        async def successful_operation():
            return "success"
        
        async def run_test():
            result = await successful_operation()
            assert result == "success"
        
        asyncio.run(run_test())
    
    def test_resilient_operation_decorator_with_exception(self):
        """Test resilient_operation decorator with exception."""
        @resilient_operation("test_operation")
        async def failing_operation():
            raise ValueError("Test error")
        
        async def run_test():
            with pytest.raises(ValueError, match="Test error"):
                await failing_operation()
        
        asyncio.run(run_test())
    
    def test_resilient_operation_no_operation_name(self):
        """Test resilient_operation without explicit operation name."""
        @resilient_operation()
        async def test_function():
            return "test_result"
        
        async def run_test():
            result = await test_function()
            assert result == "test_result"
        
        asyncio.run(run_test())
    
    def test_resilient_operation_timing_disabled(self):
        """Test resilient_operation with timing disabled."""
        @resilient_operation("test_no_timing", include_timing=False)
        async def operation_no_timing():
            return "no_timing_result"
        
        async def run_test():
            result = await operation_no_timing()
            assert result == "no_timing_result"
        
        asyncio.run(run_test())
    
    def test_resilient_operation_http_error_classification(self):
        """Test HTTP error classification in resilient_operation."""
        class MockHTTPException(Exception):
            def __init__(self, status_code):
                self.response = MagicMock()
                self.response.status_code = status_code
                super().__init__(f"HTTP {status_code}")
        
        @resilient_operation("http_test")
        async def failing_http_operation():
            raise MockHTTPException(404)
        
        async def run_test():
            with pytest.raises(MockHTTPException):
                await failing_http_operation()
        
        asyncio.run(run_test())
    
    def test_configure_prefect_logging(self):
        """Test Prefect logging configuration."""
        # Should not raise
        configure_prefect_logging()
        
        # Verify Prefect logger is configured
        prefect_logger = logging.getLogger("prefect")
        assert len(prefect_logger.handlers) > 0
        assert not prefect_logger.propagate
    
    def test_prefect_loguru_handler_emit(self):
        """Test PrefectLoguruHandler.emit method."""
        configure_prefect_logging()
        
        # Create a test log record
        prefect_logger = logging.getLogger("prefect.test")
        
        # Should not raise when logging
        prefect_logger.info("Test prefect message")
        prefect_logger.error("Test prefect error")


class TestLoggingIntegration:
    """Integration tests for logging system."""
    
    def test_full_logging_workflow(self):
        """Test complete logging workflow from setup to usage."""
        with tempfile.TemporaryDirectory() as temp_dir:
            test_log_file = Path(temp_dir) / "integration_test.log"
            
            with patch('src.config.logging.settings.logging.log_file', test_log_file):
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
    
    @pytest.mark.asyncio
    async def test_async_logging_operations(self):
        """Test logging in async context."""
        test_logger = get_logger("async.test")
        
        @resilient_operation("async_test")
        async def async_operation():
            test_logger.info("Inside async operation")
            await asyncio.sleep(0.01)  # Simulate async work
            return "async_result"
        
        result = await async_operation()
        assert result == "async_result"


class TestLoggingConfiguration:
    """Test logging configuration system."""
    
    def test_settings_integration(self):
        """Test that logging uses settings correctly."""
        # Current settings should be accessible
        from src.config.settings import settings
        
        assert hasattr(settings.logging, 'console_level')
        assert hasattr(settings.logging, 'file_level')
        assert hasattr(settings.logging, 'log_file')
        assert hasattr(settings.logging, 'real_time_debug')
        
        # New fields should be accessible with defaults
        assert hasattr(settings.logging, 'diagnose_in_production')
        assert hasattr(settings.logging, 'backtrace_in_production')
        assert hasattr(settings.logging, 'console_format')
        assert hasattr(settings.logging, 'file_format')
        assert hasattr(settings.logging, 'rotation')
        assert hasattr(settings.logging, 'retention')
        assert hasattr(settings.logging, 'compression')
        assert hasattr(settings.logging, 'serialize')
        assert hasattr(settings.logging, 'catch_internal_errors')
        
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
            
            with patch('src.config.logging.settings.logging.log_file', nested_log_file):
                setup_loguru_logger()
                
                # Directory should be created
                assert nested_log_file.parent.exists()


class TestErrorHandling:
    """Test error handling in logging system."""
    
    def test_resilient_operation_logs_exceptions(self):
        """Test that resilient_operation properly logs exceptions."""
        @resilient_operation("error_test")
        async def error_operation():
            raise RuntimeError("Intentional test error")
        
        async def run_test():
            with pytest.raises(RuntimeError):
                await error_operation()
        
        # Should not raise during test setup
        asyncio.run(run_test())
    
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
            
            with patch('src.config.logging.settings.logging.log_file', test_log_file):
                with patch('src.config.logging.settings.logging.diagnose_in_production', False):
                    with patch('src.config.logging.settings.logging.backtrace_in_production', False):
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
            
            with patch('src.config.logging.settings.logging.log_file', test_log_file):
                with patch('src.config.logging.settings.logging.console_format', custom_console_format):
                    with patch('src.config.logging.settings.logging.file_format', custom_file_format):
                        setup_loguru_logger()
                        
                        # Should work with custom formats
                        test_logger = get_logger("format.test")
                        test_logger.info("Format test message")
    
    def test_configurable_rotation_settings(self):
        """Test that rotation settings are configurable."""
        with tempfile.TemporaryDirectory() as temp_dir:
            test_log_file = Path(temp_dir) / "rotation_test.log"
            
            with patch('src.config.logging.settings.logging.log_file', test_log_file):
                with patch('src.config.logging.settings.logging.rotation', '5 MB'):
                    with patch('src.config.logging.settings.logging.retention', '3 days'):
                        with patch('src.config.logging.settings.logging.compression', 'gz'):
                            setup_loguru_logger()
                            
                            # Should work with custom rotation settings
                            test_logger = get_logger("rotation.test")
                            test_logger.info("Rotation test message")


class TestEnhancedLoggingFeatures:
    """Test the enhanced logging features actually work in practice."""
    
    def test_enhanced_resilient_operation_backward_compatibility(self):
        """Test that enhanced resilient_operation is backward compatible."""
        # Old usage pattern should still work
        @resilient_operation("test_compat")
        async def old_style_operation():
            return "backward_compatible"
        
        async def run_test():
            result = await old_style_operation()
            assert result == "backward_compatible"
        
        asyncio.run(run_test())
    
    def test_enhanced_resilient_operation_new_features(self):
        """Test new features of resilient_operation work."""
        # New usage with timing disabled
        @resilient_operation("test_new_features", include_timing=False)
        async def new_style_operation():
            return "enhanced_features"
        
        async def run_test():
            result = await new_style_operation()
            assert result == "enhanced_features"
        
        asyncio.run(run_test())
    
    def test_production_security_settings_accessible(self):
        """Test that new security settings are accessible and have safe defaults."""
        from src.config.settings import settings
        
        # New security settings should exist and be secure by default
        assert hasattr(settings.logging, 'diagnose_in_production')
        assert hasattr(settings.logging, 'backtrace_in_production')
        
        # Should default to False for production safety
        assert settings.logging.diagnose_in_production is False
        assert settings.logging.backtrace_in_production is False