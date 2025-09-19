"""Logging configuration and utilities using Loguru.

This module provides centralized logging setup for the Narada application,
including structured logging with Loguru, error handling decorators for
external API calls, and integration with third-party libraries like Prefect.

Key Components:
--------------
- Structured logging with Loguru
- Error handling decorators for external API calls
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

@resilient_operation(operation_name: str)
    Decorator for handling errors in external API calls
    Args: operation_name - Name for logging the operation
    Usage: @resilient_operation("spotify_sync")

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

3. Handle external API calls:
    ```python
    @resilient_operation("spotify_api")
    async def fetch_playlist(playlist_id: str):
        return await spotify.get_playlist(playlist_id)
    ```
"""

import contextlib
from functools import wraps
import logging
from pathlib import Path
import sys
import time
from typing import Any

from loguru import logger

from .constants import HTTPStatus
from .settings import settings

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
    logger.configure(
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


def get_logger(name: str) -> Any:  # Use Any for Loguru logger type
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
            for key, val in section_values.items():
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
# ERROR HANDLING DECORATORS
# =============================================================================


def resilient_operation(
    operation_name: str | None = None, *, include_timing: bool = True
):
    """Decorator for service boundary operations with standardized error handling and structured logging.

    Use on external API calls and other boundary operations to centralize
    error handling, timing, and provide structured error context.

    Args:
        operation_name: Optional name for the operation (defaults to function name)
        include_timing: Whether to log operation timing (default: True)

    Returns:
        Decorated function with enhanced error handling and context

    Example:
        >>> @resilient_operation("spotify_playlist_fetch")
        >>> async def get_spotify_playlist(playlist_id: str):
        >>>     return await spotify.get_playlist(playlist_id)

        >>> @resilient_operation("batch_import", include_timing=False)
        >>> async def import_batch(items: list):
        >>>     return await process_items(items)
    """

    def decorator(func):
        """Inner decorator function that wraps the target function with enhanced error handling.

        Args:
            func: Function to wrap with resilient error handling

        Returns:
            Wrapped async function with structured error handling
        """
        op_name = operation_name or func.__name__

        @wraps(func)
        async def wrapper(*args, **kwargs):
            """Async wrapper with structured error handling, timing, and context.

            Args:
                *args: Positional arguments for wrapped function
                **kwargs: Keyword arguments for wrapped function

            Returns:
                Result from wrapped function

            Raises:
                Exception: Re-raises all exceptions after structured logging
            """
            operation_logger = logger.bind(
                operation=op_name,
                function=func.__name__,
                module=func.__module__,
            )

            start_time = time.time() if include_timing else None

            try:
                if include_timing:
                    operation_logger.debug(f"Starting operation: {op_name}")

                result = await func(*args, **kwargs)

                if include_timing and start_time is not None:
                    duration = time.time() - start_time
                    operation_logger.info(
                        f"Operation completed successfully: {op_name}",
                        duration_seconds=round(duration, 3),
                        status="success",
                    )

                return result

            except Exception as e:
                error_context = _build_error_context(
                    e, op_name, start_time, include_timing
                )

                # Log with structured context
                operation_logger.error(
                    f"Operation failed: {op_name}", **error_context, exc_info=True
                )

                # Re-raise to maintain original behavior
                raise

        return wrapper

    return decorator


def _build_error_context(
    exception: Exception,
    operation_name: str,
    start_time: float | None,
    include_timing: bool,
) -> dict[str, Any]:
    _ = operation_name  # Mark as intentionally unused for now
    """Build structured error context for logging.

    Args:
        exception: The caught exception
        operation_name: Name of the operation that failed
        start_time: Operation start time (if timing enabled)
        include_timing: Whether timing is enabled

    Returns:
        Dictionary with structured error context
    """
    context = {
        "error_type": type(exception).__name__,
        "error_message": str(exception),
        "status": "error",
    }

    # Add timing information if available
    if include_timing and start_time is not None:
        duration = time.time() - start_time
        context["duration_seconds"] = str(round(duration, 3))

    # Classify HTTP errors using constants
    if hasattr(exception, "response"):
        response = getattr(exception, "response", None)
        if response is not None and hasattr(response, "status_code"):
            status_code = getattr(response, "status_code", None)
            if status_code is not None:
                context["http_status_code"] = status_code
                context["error_classification"] = _classify_http_error(status_code)

    # Add additional context based on exception type
    if hasattr(exception, "__cause__") and exception.__cause__:
        context["root_cause"] = str(exception.__cause__)
        context["root_cause_type"] = type(exception.__cause__).__name__

    return context


def _classify_http_error(status_code: int) -> str:
    """Classify HTTP errors for structured logging.

    Args:
        status_code: HTTP status code

    Returns:
        Human-readable error classification
    """
    if status_code == HTTPStatus.UNAUTHORIZED:
        return "authentication_error"
    elif status_code == HTTPStatus.FORBIDDEN:
        return "authorization_error"
    elif status_code == HTTPStatus.NOT_FOUND:
        return "resource_not_found"
    elif status_code == HTTPStatus.TOO_MANY_REQUESTS:
        return "rate_limit_exceeded"
    elif HTTPStatus.CLIENT_ERROR_MIN <= status_code < HTTPStatus.CLIENT_ERROR_MAX:
        return "client_error"
    elif status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return "server_error"
    else:
        return "unknown_error"


# =============================================================================
# PROGRESS.CONSOLE UNIFIED LOGGING
# =============================================================================


def enable_unified_console_output(progress_console) -> None:
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
        # Store original handlers for restoration
        original_loguru_handlers = list(loguru_logger._core.handlers.keys())  # type: ignore[attr-defined]

        # Store in module for restoration
        enable_unified_console_output._original_loguru_handlers = original_loguru_handlers  # type: ignore[attr-defined]

        # Remove all existing Loguru handlers
        for handler_id in original_loguru_handlers:
            with contextlib.suppress(ValueError):
                loguru_logger.remove(handler_id)

        # Create custom sink that routes to Progress.console with Rich formatting
        def progress_console_sink(message):
            """Custom Loguru sink that routes logs through Progress.console with Rich markup."""
            try:
                # Extract record information from Loguru message object
                record = message.record

                # Format timestamp
                timestamp = record['time'].strftime("%H:%M:%S.%f")[:-3]

                # Map levels to Rich colors
                level_colors = {
                    'DEBUG': 'blue',
                    'INFO': 'white',
                    'WARNING': 'yellow',
                    'ERROR': 'red',
                    'CRITICAL': 'bright_red'
                }
                level_name = record['level'].name
                level_color = level_colors.get(level_name, 'white')

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
        loguru_logger.add(
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

        loguru_logger.add(
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

            def emit(self, record):
                try:
                    # Map Python logging levels to Loguru levels
                    level_mapping = {
                        logging.DEBUG: 'DEBUG',
                        logging.INFO: 'INFO',
                        logging.WARNING: 'WARNING',
                        logging.ERROR: 'ERROR',
                        logging.CRITICAL: 'CRITICAL'
                    }

                    loguru_level = level_mapping.get(record.levelno, 'INFO')

                    # Forward to Loguru with module context preserved
                    loguru_logger.bind(
                        module=record.name,
                        service="narada"
                    ).log(loguru_level, record.getMessage())

                except Exception:
                    self.handleError(record)

        # Create the bridge handler
        bridge_handler = PrefectToLoguruHandler()
        bridge_handler.setLevel(getattr(logging, settings.logging.prefect_bridge_level))

        # Target specific loggers that might bypass the root logger
        # Expanded list based on Prefect 3.0 documentation to capture startup activity
        loggers_to_intercept = [
            'prefect',
            'prefect.flow_runs',
            'prefect.task_runs',
            'prefect.logging',
            'prefect.engine',
            'prefect.client',
            'prefect.worker',  # Worker logger mentioned in docs
            'prefect.workers',  # Potential plural form
            'prefect.server',  # Server-side operations
            'prefect.api',     # API operations
            'prefect.flows',   # Flow operations
            'prefect.tasks',   # Task operations
            'prefect.infrastructure',  # Infrastructure management
            'prefect.deployments',     # Deployment operations
            'prefect.blocks',          # Blocks system
            'prefect.runtime',         # Runtime operations
            'prefect.settings',        # Settings/configuration
            'prefect.orchestration',   # Orchestration engine
        ]

        # Store original handlers for restoration
        original_handlers_by_logger = {}
        for logger_name in loggers_to_intercept:
            target_logger = logging.getLogger(logger_name)
            original_handlers_by_logger[logger_name] = target_logger.handlers.copy()

            # Remove existing console handlers to prevent duplication
            target_logger.handlers = [
                h for h in target_logger.handlers
                if not isinstance(h, logging.StreamHandler)
            ]

            # Add our Loguru bridge handler
            target_logger.addHandler(bridge_handler)
            target_logger.setLevel(getattr(logging, settings.logging.prefect_logger_level))
            # Prevent propagation to root logger to avoid duplicates
            target_logger.propagate = False

        # Store handlers for cleanup
        enable_unified_console_output._original_handlers_by_logger = original_handlers_by_logger  # type: ignore[attr-defined]
        enable_unified_console_output._bridge_handler = bridge_handler  # type: ignore[attr-defined]

    except Exception as e:
        # Fallback error reporting through progress console
        progress_console.print(f"[yellow]Warning: Failed to configure progress console logging: {e}[/yellow]")


def restore_standard_console_output() -> None:
    """Restore standard console output after unified Progress.console coordination ends."""
    try:
        import logging

        from loguru import logger as loguru_logger

        # Remove all current Loguru handlers
        loguru_logger.remove()

        # Restore normal Loguru configuration
        setup_loguru_logger()

        # Restore Python logging for all intercepted loggers
        if hasattr(enable_unified_console_output, '_original_handlers_by_logger'):
            original_handlers_by_logger = enable_unified_console_output._original_handlers_by_logger  # type: ignore[attr-defined]

            for logger_name, original_handlers in original_handlers_by_logger.items():
                target_logger = logging.getLogger(logger_name)
                target_logger.handlers.clear()

                # Restore original handlers and propagation for this logger
                for handler in original_handlers:
                    target_logger.addHandler(handler)

                # Restore propagation (we set it to False)
                target_logger.propagate = True

            delattr(enable_unified_console_output, '_original_handlers_by_logger')  # type: ignore[attr-defined]

        # Clean up our stored state
        for attr in ['_original_loguru_handlers', '_bridge_handler']:
            if hasattr(enable_unified_console_output, attr):
                delattr(enable_unified_console_output, attr)  # type: ignore[attr-defined]

    except Exception as e:
        # Final fallback: complete reconfiguration
        print(f"Warning: Failed to restore progress console logging, doing full reset: {e}")
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

        def emit(self, record):
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
