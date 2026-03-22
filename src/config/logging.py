"""Logging configuration and utilities using Loguru.

This module provides centralized logging setup for the Mixd application,
including structured logging with Loguru, error handling decorators for
external API calls, and integration with third-party libraries like Prefect.

Key Components:
--------------
- Structured logging with Loguru
- Progress.console unified output coordination

Public API:
----------
setup_loguru_logger(verbose: bool = False) -> None
    Configure Loguru logger for the application

get_logger(name: str) -> Logger
    Get a context-aware logger for your module
    Args: name - Usually __name__ from the calling module
    Usage: logger = get_logger(__name__)

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

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: Pydantic settings validators, loguru config

import logging
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Any, ClassVar, override

from loguru import logger
from rich.console import Console

if TYPE_CHECKING:
    from loguru import Logger

from .settings import settings

# =============================================================================
# PREFECT → LOGURU BRIDGE
# =============================================================================


class PrefectToLoguruHandler(logging.Handler):
    """Routes Python logging (including Prefect) through Loguru for unified formatting.

    Extracted to top-level so it can be used by both `enable_unified_console_output()`
    (CLI progress coordination) and `intercept_prefect_loggers()` (API server).
    """

    _LEVEL_MAPPING: ClassVar[dict[int, str]] = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.WARNING: "WARNING",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "CRITICAL",
    }

    @override
    def emit(self, record: logging.LogRecord) -> None:
        try:
            loguru_level = self._LEVEL_MAPPING.get(record.levelno, "INFO")
            bound = logger.bind(module=record.name, service="mixd")

            # Forward exception tracebacks so they appear in loguru output
            if record.exc_info and record.exc_info[0] is not None:
                bound.opt(exception=record.exc_info).log(
                    loguru_level, record.getMessage()
                )
            else:
                bound.log(loguru_level, record.getMessage())

        except Exception:
            self.handleError(record)


# Prefect logger names to intercept — covers Prefect 3.0 subsystems
_PREFECT_LOGGERS = [
    "prefect",
    "prefect.flow_runs",
    "prefect.task_runs",
    "prefect.logging",
    "prefect.engine",
    "prefect.client",
    "prefect.worker",
    "prefect.workers",
    "prefect.server",
    "prefect.api",
    "prefect.flows",
    "prefect.tasks",
    "prefect.infrastructure",
    "prefect.deployments",
    "prefect.blocks",
    "prefect.runtime",
    "prefect.settings",
    "prefect.orchestration",
]


def intercept_prefect_loggers() -> None:
    """Attach PrefectToLoguruHandler to all Prefect loggers.

    Safe to call multiple times — clears existing bridge handlers before
    re-attaching. Used by both CLI (via enable_unified_console_output) and
    API server (via lifespan).
    """
    bridge_handler = PrefectToLoguruHandler()
    bridge_handler.setLevel(getattr(logging, settings.logging.prefect_bridge_level))

    original_handlers: dict[str, list[logging.Handler]] = {}
    for logger_name in _PREFECT_LOGGERS:
        target_logger = logging.getLogger(logger_name)
        original_handlers[logger_name] = target_logger.handlers.copy()

        # Remove existing console handlers to prevent duplication
        target_logger.handlers = [
            h
            for h in target_logger.handlers
            if not isinstance(h, logging.StreamHandler)
        ]

        target_logger.addHandler(bridge_handler)
        target_logger.setLevel(getattr(logging, settings.logging.prefect_logger_level))
        target_logger.propagate = False

    global _original_handlers_by_logger, _bridge_handler
    _original_handlers_by_logger = original_handlers
    _bridge_handler = bridge_handler


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
        extra={"service": "mixd", "module": "root"},
    )


def setup_script_logger(script_name: str) -> None:
    """Lightweight logging config for standalone scripts — console only, no file."""
    logger.configure(
        handlers=[
            {
                "sink": sys.stderr,
                "level": "DEBUG",
                "format": "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
                "colorize": True,
            },
        ],
        extra={"service": script_name, "module": script_name},
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
        service="mixd",
    )


# =============================================================================
# PROGRESS.CONSOLE UNIFIED LOGGING
# =============================================================================


def enable_unified_console_output(progress_console: Console) -> None:
    """Enable unified console output through Rich Progress.console for coordinated display.

    Routes all logging (Loguru + Python/Prefect) through Progress.console to ensure
    proper coordination between log messages and progress bars. This ensures logs
    appear above pinned progress bars rather than interfering with them.

    Args:
        progress_console: Rich Console instance from Progress (progress.console)
    """

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
        intercept_prefect_loggers()

    except Exception as e:
        # Fallback error reporting through progress console
        progress_console.print(
            f"[yellow]Warning: Failed to configure progress console logging: {e}[/yellow]"
        )


def add_workflow_run_logger(workflow_id: str, run_id: str) -> int:
    """Add a temporary Loguru sink that writes per-run JSONL log file.

    Returns the sink ID so the caller can remove it when the run completes.
    The sink only captures log entries that have a matching ``workflow_run_id``
    in their ``extra`` dict — other log entries are filtered out.
    """
    log_dir = Path(settings.workflow_log_dir) / workflow_id
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{run_id}.jsonl"

    def _run_filter(record: Any) -> bool:
        return record["extra"].get("workflow_run_id") == run_id

    sink_id: int = logger.add(
        str(log_path),
        level="DEBUG",
        serialize=True,
        filter=_run_filter,
        enqueue=False,  # Real-time writes for crash safety
    )
    return sink_id


def remove_workflow_run_logger(sink_id: int) -> None:
    """Remove a per-run log sink after workflow execution completes."""
    import contextlib

    with contextlib.suppress(ValueError):
        logger.remove(sink_id)


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
