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

import logging
from pathlib import Path
import sys
from typing import Any

from loguru import logger

from .settings import settings

# =============================================================================
# LOGGING CONFIGURATION
# =============================================================================


def setup_loguru_logger(verbose: bool = False) -> None:
    """Configure Loguru logger for the application.

    Args:
        verbose: Enable verbose logging with debug level and detailed tracebacks

    Note:
        - Removes default logger and sets up console and file handlers
        - Console format is colorized and simplified
        - File format includes full structured information
        - Log rotation and retention are automatically managed
    """
    # -------------------------------------------------------------------------
    # Setup
    # -------------------------------------------------------------------------
    # Remove default logger
    logger.remove()

    # Create log directory structure
    log_file_path = Path(settings.logging.log_file)
    log_dir = log_file_path.parent
    log_dir.mkdir(parents=True, exist_ok=True)

    # Add contextual info to all log records
    logger.configure(extra={"service": "narada", "module": "root"})

    # -------------------------------------------------------------------------
    # Console Handler
    # -------------------------------------------------------------------------
    # Console handler - adjust level based on verbose flag
    console_level = "DEBUG" if verbose else settings.logging.console_level
    logger.add(
        sink=sys.stdout,
        level=console_level,
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:"
            "<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        colorize=True,
        backtrace=verbose,  # Show more detail in verbose mode
        diagnose=verbose,  # Enable diagnostics in verbose mode
    )

    # -------------------------------------------------------------------------
    # File Handler
    # -------------------------------------------------------------------------
    # File handler - detailed format with full debug info
    # Use real-time logging for performance debugging if enabled
    enqueue_logs = not settings.logging.real_time_debug
    logger.add(
        sink=str(settings.logging.log_file),  # Convert Path to string
        level=settings.logging.file_level,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {process}:{thread} | {extra[service]} | {extra[module]} | {name}:{function}:{line} | {message}",
        rotation="10 MB",  # Rotate at 10 MB
        retention="1 week",  # Keep logs for 1 week
        compression="zip",
        backtrace=True,  # Always show full traceback
        diagnose=True,  # Show variables in traceback
        enqueue=enqueue_logs,  # Disable buffering for real-time debugging
        catch=True,  # Catch errors within logger
        serialize=True,  # Enable JSON structured logging
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


async def log_startup_info() -> None:  # noqa: RUF029
    """Log application configuration on startup.

    Displays a startup banner and logs all configuration values at debug level.
    Should be called once during application initialization.

    Example:
        >>> await log_startup_info()
    """
    local_logger = get_logger(__name__)  # Get a properly bound logger
    separator = "=" * 50

    # Startup banner and config details
    local_logger.info("")
    local_logger.info("{}", separator, markup=True)
    local_logger.info("🎵 Narada Music Integration Platform", markup=True)
    local_logger.info("{}", separator, markup=True)
    local_logger.info("")

    # Log configuration details in a more readable format
    local_logger.debug("Configuration:")

    # Log each config section
    config_dict = settings.model_dump()
    for section_name, section_values in config_dict.items():
        local_logger.debug("  {}:", section_name.upper())
        if isinstance(section_values, dict):
            for key, value in section_values.items():
                if isinstance(value, Path):
                    value = str(value)
                local_logger.debug("    {}: {}", key.upper(), value)
        else:
            if isinstance(section_values, Path):
                section_values = str(section_values)
            local_logger.debug("    {}", section_values)

    local_logger.info("")


# =============================================================================
# ERROR HANDLING DECORATORS
# =============================================================================


def resilient_operation(operation_name=None):
    """Decorator for service boundary operations with standardized error handling.

    Use on external API calls and other boundary operations to centralize
    error handling and avoid repetitive try/except blocks.

    Args:
        operation_name: Optional name for the operation (defaults to function name)

    Returns:
        Decorated function with error handling

    Example:
        >>> @resilient_operation("spotify_playlist_fetch")
        >>> async def get_spotify_playlist(playlist_id):
        >>> # Implementation that can raise exceptions
        >>>     return await spotify.get_playlist(playlist_id)
    """

    def decorator(func):
        op_name = operation_name or func.__name__

        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.exception(f"Error in {op_name}: {e!s}")
                # Re-raise certain exceptions, swallow others based on type
                raise

        return wrapper

    return decorator


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
        def emit(self, record):
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
