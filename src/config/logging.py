"""Logging configuration and utilities using Loguru.

This module provides centralized logging setup for the Narada application,
including structured logging with Loguru, error handling decorators for
external API calls, and integration with third-party libraries like Prefect.

Key Components:
--------------
- Structured logging with Loguru
- Startup information logging
- Third-party logging integration

Public API:
----------
setup_loguru_logger(verbose: bool = False) -> None
    Configure Loguru logger for the application

get_logger(name: str) -> Logger
    Get a context-aware logger for your module
    Args: name - Usually __name__ from the calling module
    Usage: logger = get_logger(__name__)

log_startup_info() -> None
    Log system configuration and API status at startup
    Call once when application initializes

configure_prefect_logging() -> None
    Configure Prefect to use our Loguru setup

Quick Start:
-----------
1. Get a logger for your module:
    ```python
    from src.config import get_logger

    logger = get_logger(__name__)
    ```

2. Log with structured context:
    ```python
    logger.info("Starting sync", playlist_id=123)
    ```
"""

import logging
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Any, cast, override

from loguru import logger

if TYPE_CHECKING:
    from loguru import Logger

from .settings import settings

# =============================================================================
# MODULE-LEVEL STATE FOR CONSOLE OUTPUT COORDINATION
# =============================================================================

# Store state for console output coordination
_original_handlers_by_logger: dict[str, list[logging.Handler]] = {}
_bridge_handler: logging.Handler | None = None

# =============================================================================
# LOGGING CONFIGURATION
# =============================================================================


def setup_loguru_logger(verbose: bool = False) -> None:
    """Configure Loguru logger for the application.

    Args:
        verbose: Enable verbose logging with debug level and detailed tracebacks

    Note:
        - Uses modern logger.configure() for centralized configuration
        - Console format is colorized and simplified
        - File format includes full structured information
        - Security controls for production environments
        - All settings configurable via environment variables
    """
    # Create log directory structure
    log_file_path = Path(settings.logging.log_file)
    log_dir = log_file_path.parent
    log_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------------
    # Determine Security Settings
    # -------------------------------------------------------------------------
    # In verbose mode, override production security settings
    enable_backtrace = verbose or settings.logging.backtrace_in_production
    enable_diagnose = verbose or settings.logging.diagnose_in_production

    # Console level - DEBUG in verbose, otherwise from settings
    console_level = "DEBUG" if verbose else settings.logging.console_level

    # Enqueue setting for async safety
    enqueue_logs = not settings.logging.real_time_debug

    # -------------------------------------------------------------------------
    # Format Strings
    # -------------------------------------------------------------------------
    # Use custom formats if provided, otherwise use sensible defaults
    console_format = settings.logging.console_format or (
        "<green>{time:HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:"
        "<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )

    file_format = settings.logging.file_format or (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {process}:{thread} | "
        "{extra[service]} | {extra[module]} | {name}:{function}:{line} | {message}"
    )

    # -------------------------------------------------------------------------
    # Modern Loguru Configuration
    # -------------------------------------------------------------------------
    _ = logger.configure(
        handlers=[
            # Console handler
            {
                "sink": sys.stdout,
                "level": console_level,
                "format": console_format,
                "colorize": True,
                "backtrace": enable_backtrace,
                "diagnose": enable_diagnose,
            },
            # File handler
            {
                "sink": str(settings.logging.log_file),
                "level": settings.logging.file_level,
                "format": file_format,
                "rotation": settings.logging.rotation,
                "retention": settings.logging.retention,
                "compression": settings.logging.compression,
                "backtrace": enable_backtrace,
                "diagnose": enable_diagnose,
                "enqueue": enqueue_logs,
                "catch": settings.logging.catch_internal_errors,
                "serialize": settings.logging.serialize,
            },
        ],
        extra={"service": "narada", "module": "root"},
    )


# =============================================================================
# LOGGER FACTORY
# =============================================================================


def get_logger(name: str) -> Logger:
    """Get a pre-configured logger instance for the given module.

    Args:
        name: Module name (typically __name__)

    Returns:
        Pre-configured Loguru logger instance with module context

    Example:
        ```python
        logger = get_logger(__name__)
        logger.info("Operation complete", operation="sync")
        ```

    Notes:
        - Returns a bound logger with structured context
        - Inherits global Loguru configuration
        - Thread-safe for async operations
    """
    return logger.bind(
        module=name,
        service="narada",
    )


# =============================================================================
# STARTUP LOGGING
# =============================================================================


def log_startup_info() -> None:
    """Log application configuration on startup.

    Displays a startup banner and logs all configuration values at debug level.
    Should be called once during application initialization.

    Example:
        >>> log_startup_info()
    """
    local_logger = get_logger(__name__)  # Get a properly bound logger
    separator = "=" * 50

    # Startup banner and config details
    local_logger.info("")
    local_logger.info(separator, markup=True)
    local_logger.info("🎵 Narada Music Integration Platform", markup=True)
    local_logger.info(separator, markup=True)
    local_logger.info("")

    # Log configuration details in a more readable format
    local_logger.debug("Configuration:")

    # Log each config section
    config_dict = settings.model_dump()
    for section_name, section_values in config_dict.items():
        local_logger.debug(f"  {section_name.upper()}:")
        if isinstance(section_values, dict):
            for key, val in cast(dict[str, Any], section_values).items():
                display_val = str(val) if isinstance(val, Path) else val
                local_logger.debug(f"    {key.upper()}: {display_val}")
        else:
            if isinstance(section_values, Path):
                section_val = str(section_values)
            else:
                section_val = section_values
            local_logger.debug(f"    {section_val}")

    local_logger.info("")


# =============================================================================
# PROGRESS.CONSOLE UNIFIED LOGGING
# =============================================================================


def enable_unified_console_output(progress_console: Any) -> None:
    """Enable unified console output through Rich Progress.console for coordinated display.

    Routes all logging (Loguru + Python/Prefect) through Progress.console to ensure
    proper coordination between log messages and progress bars. This ensures logs
    appear above pinned progress bars rather than interfering with them.

    Args:
        progress_console: Rich Console instance from Progress (progress.console)
    """
    import logging

    from loguru import logger as loguru_logger

    try:
        # Remove all existing Loguru handlers (public API: no-arg remove clears all)
        loguru_logger.remove()

        # Create custom sink that routes to Progress.console with Rich formatting
        def progress_console_sink(message: Any) -> None:
            """Custom Loguru sink that routes logs through Progress.console with Rich markup."""
            try:
                # Extract record information from Loguru message object
                record = message.record

                # Format timestamp
                timestamp = record["time"].strftime("%H:%M:%S.%f")[:-3]

                # Map levels to Rich colors
                level_colors = {
                    "DEBUG": "blue",
                    "INFO": "white",
                    "WARNING": "yellow",
                    "ERROR": "red",
                    "CRITICAL": "bright_red",
                }
                level_name = record["level"].name
                level_color = level_colors.get(level_name, "white")

                # Create Rich formatted message (no ANSI codes)
                rich_message = (
                    f"[green]{timestamp}[/green] | "
                    f"[{level_color}]{level_name: <8}[/{level_color}] | "
                    f"[cyan]{record['name']}[/cyan]:[cyan]{record['function']}[/cyan]:[cyan]{record['line']}[/cyan] - "
                    f"[{level_color}]{record['message']}[/{level_color}]"
                )

                # Print with Rich markup (no raw ANSI codes)
                progress_console.print(rich_message, highlight=False)

            except Exception:
                # Fallback: print plain message
                progress_console.print(str(message).rstrip(), highlight=False)

        # Add Progress.console sink for application logs with Rich formatting
        _ = loguru_logger.add(
            progress_console_sink,
            level=settings.logging.console_level,
            format="{message}",  # Simple format since we handle formatting in the sink
            colorize=False,  # Disable ANSI colors - use Rich markup instead
        )

        # Keep file logging intact
        log_file_path = Path(settings.logging.log_file)
        file_format = settings.logging.file_format or (
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {process}:{thread} | "
            "{extra[service]} | {extra[module]} | {name}:{function}:{line} | {message}"
        )

        _ = loguru_logger.add(
            str(log_file_path),
            level=settings.logging.file_level,
            format=file_format,
            rotation=settings.logging.rotation,
            retention=settings.logging.retention,
            compression=settings.logging.compression,
            enqueue=not settings.logging.real_time_debug,
            catch=settings.logging.catch_internal_errors,
            serialize=settings.logging.serialize,
        )

        # Route Prefect logs through Loguru for unified formatting
        # This creates: Prefect → Loguru → Progress.console pipeline

        # Create simple handler that forwards Prefect logs to Loguru
        class PrefectToLoguruHandler(logging.Handler):
            """Routes Python logging (including Prefect) through Loguru for unified formatting."""

            @override
            def emit(self, record: logging.LogRecord) -> None:
                try:
                    # Map Python logging levels to Loguru levels
                    level_mapping = {
                        logging.DEBUG: "DEBUG",
                        logging.INFO: "INFO",
                        logging.WARNING: "WARNING",
                        logging.ERROR: "ERROR",
                        logging.CRITICAL: "CRITICAL",
                    }

                    loguru_level = level_mapping.get(record.levelno, "INFO")

                    # Forward to Loguru with module context preserved
                    loguru_logger.bind(module=record.name, service="narada").log(
                        loguru_level, record.getMessage()
                    )

                except Exception:
                    self.handleError(record)

        # Create the bridge handler
        bridge_handler = PrefectToLoguruHandler()
        bridge_handler.setLevel(getattr(logging, settings.logging.prefect_bridge_level))

        # Target specific loggers that might bypass the root logger
        # Expanded list based on Prefect 3.0 documentation to capture startup activity
        loggers_to_intercept = [
            "prefect",
            "prefect.flow_runs",
            "prefect.task_runs",
            "prefect.logging",
            "prefect.engine",
            "prefect.client",
            "prefect.worker",  # Worker logger mentioned in docs
            "prefect.workers",  # Potential plural form
            "prefect.server",  # Server-side operations
            "prefect.api",  # API operations
            "prefect.flows",  # Flow operations
            "prefect.tasks",  # Task operations
            "prefect.infrastructure",  # Infrastructure management
            "prefect.deployments",  # Deployment operations
            "prefect.blocks",  # Blocks system
            "prefect.runtime",  # Runtime operations
            "prefect.settings",  # Settings/configuration
            "prefect.orchestration",  # Orchestration engine
        ]

        # Store original handlers for restoration
        original_handlers_by_logger = {}
        for logger_name in loggers_to_intercept:
            target_logger = logging.getLogger(logger_name)
            original_handlers_by_logger[logger_name] = target_logger.handlers.copy()

            # Remove existing console handlers to prevent duplication
            target_logger.handlers = [
                h
                for h in target_logger.handlers
                if not isinstance(h, logging.StreamHandler)
            ]

            # Add our Loguru bridge handler
            target_logger.addHandler(bridge_handler)
            target_logger.setLevel(
                getattr(logging, settings.logging.prefect_logger_level)
            )
            # Prevent propagation to root logger to avoid duplicates
            target_logger.propagate = False

        # Store handlers for cleanup
        global _original_handlers_by_logger, _bridge_handler
        _original_handlers_by_logger = original_handlers_by_logger
        _bridge_handler = bridge_handler

    except Exception as e:
        # Fallback error reporting through progress console
        progress_console.print(
            f"[yellow]Warning: Failed to configure progress console logging: {e}[/yellow]"
        )


def restore_standard_console_output() -> None:
    """Restore standard console output after unified Progress.console coordination ends."""
    try:
        import logging

        from loguru import logger as loguru_logger

        global _original_handlers_by_logger, _bridge_handler

        # Remove all current Loguru handlers
        loguru_logger.remove()

        # Restore normal Loguru configuration
        setup_loguru_logger()

        # Restore Python logging for all intercepted loggers
        if _original_handlers_by_logger:
            for logger_name, original_handlers in _original_handlers_by_logger.items():
                target_logger = logging.getLogger(logger_name)
                target_logger.handlers.clear()

                # Restore original handlers and propagation for this logger
                for handler in original_handlers:
                    target_logger.addHandler(handler)

                # Restore propagation (we set it to False)
                target_logger.propagate = True

            _original_handlers_by_logger = {}

        # Clean up bridge handler reference
        _bridge_handler = None

    except Exception as e:
        # Final fallback: complete reconfiguration
        print(
            f"Warning: Failed to restore progress console logging, doing full reset: {e}"
        )
        from loguru import logger as loguru_logger

        loguru_logger.remove()
        setup_loguru_logger()


# =============================================================================
# THIRD-PARTY LOGGING INTEGRATION
# =============================================================================


def configure_prefect_logging() -> None:
    """Configure Prefect to use our Loguru setup without changing our existing patterns.

    Sets up a custom handler that forwards Prefect logs to Loguru while
    maintaining the existing logging configuration and patterns.

    Note:
        - Creates a bridge between Python's logging and Loguru
        - Preserves module context from original log records
        - Disables propagation to prevent duplicate logs
    """

    # Create a simple handler that passes Prefect logs to Loguru
    class PrefectLoguruHandler(logging.Handler):
        """Custom logging handler that forwards Prefect logs to Loguru.

        Bridges Python's standard logging with Loguru while preserving
        module context and preventing duplicate log entries.
        """

        @override
        def emit(self, record: logging.LogRecord) -> None:
            """Process a log record and forward it to Loguru.

            Args:
                record: Python logging.LogRecord to process
            """
            # Get corresponding Loguru level
            level = logger.level(record.levelname).name

            # Extract message and maintain original format
            msg = self.format(record)

            # Pass to loguru with appropriate module context
            module_name = record.name
            logger.bind(module=module_name).log(level, msg)

    # Configure the Prefect logger
    prefect_logger = logging.getLogger("prefect")
    prefect_logger.handlers = [PrefectLoguruHandler()]
    prefect_logger.propagate = False
