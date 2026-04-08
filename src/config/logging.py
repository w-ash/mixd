"""Logging configuration and utilities using structlog.

Provides centralized logging setup with dual output: colorized console for
humans, flat JSON for file/agents/aggregation. Uses structlog in stdlib
integration mode so Prefect, Uvicorn, and FastAPI logs flow through
automatically — no bridge code needed.

Public API:
----------
setup_logging(verbose: bool = False) -> None
    Configure structlog + stdlib handlers for the application

get_logger(name: str) -> BoundLogger
    Get a context-aware logger for your module
    Usage: logger = get_logger(__name__)

logging_context(**kwargs) -> ContextManager
    Bind structured context for the duration of a block (async-safe via contextvars)
    Usage: with logging_context(workflow_id=42): ...
"""

from collections.abc import Iterator
from contextlib import contextmanager
import logging
import logging.handlers
from pathlib import Path
import sys
from typing import cast, override

from rich.console import Console
import structlog
from structlog.contextvars import bind_contextvars, unbind_contextvars
from structlog.stdlib import BoundLogger

from .settings import settings

# =============================================================================
# LOGGING CONFIGURATION
# =============================================================================


def _shared_processors() -> list[structlog.types.Processor]:
    """Processor chain shared by all output handlers."""
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.ExtraAdder(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.CallsiteParameterAdder([
            structlog.processors.CallsiteParameter.FUNC_NAME,
            structlog.processors.CallsiteParameter.LINENO,
        ]),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]


def setup_logging(verbose: bool = False) -> None:
    """Configure structlog with dual output: pretty console + flat JSON file.

    Uses stdlib integration mode — all Python loggers (Prefect, Uvicorn, etc.)
    flow through structlog's processor pipeline automatically.

    Args:
        verbose: Enable DEBUG console output and richer tracebacks.
    """
    console_level = "DEBUG" if verbose else settings.logging.console_level

    # Create log directory
    log_file_path = Path(settings.logging.log_file)
    log_file_path.parent.mkdir(parents=True, exist_ok=True)

    shared = _shared_processors()

    # --- Configure structlog (wraps stdlib logging) ---
    structlog.configure(
        processors=[
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # --- Console handler (colorized for humans) ---
    # Warm editorial palette matching the mixd brand identity
    # Uses 256-color ANSI escapes for muted, readable tones
    level_styles: dict[str, str] = {
        "debug": "\033[38;5;245m",  # warm gray — unobtrusive
        "info": "\033[38;5;178m",  # amber/gold — brand primary
        "warning": "\033[38;5;214m",  # bright amber — attention
        "warn": "\033[38;5;214m",
        "error": "\033[38;5;167m",  # muted red — urgent but not garish
        "critical": "\033[1;38;5;167m",  # bold muted red
        "exception": "\033[38;5;167m",
        "notset": "\033[38;5;245m",
    }

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.getLevelNamesMapping()[console_level])
    console_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(
                    colors=True,
                    level_styles=level_styles,
                ),
            ],
        )
    )

    # --- File handler (flat JSON for agents/jq/Fly.io) ---
    file_handler = logging.handlers.RotatingFileHandler(
        str(log_file_path),
        maxBytes=_parse_rotation(settings.logging.rotation),
        backupCount=_parse_retention(settings.logging.retention),
    )
    file_handler.setLevel(logging.getLevelNamesMapping()[settings.logging.file_level])
    file_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.dict_tracebacks,
                structlog.processors.JSONRenderer(),
            ],
        )
    )

    # --- Root logger ---
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(console_handler)
    root.addHandler(file_handler)
    root.setLevel(logging.DEBUG)  # Let handlers filter by their own levels

    # --- Prefect log levels (stdlib integration — no bridge needed) ---
    logging.getLogger("prefect").setLevel(
        logging.getLevelNamesMapping()[settings.logging.prefect_log_level]
    )

    # Suppress noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def setup_script_logger(_script_name: str) -> None:
    """Lightweight console-only logging for standalone scripts."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(colors=True),
            ],
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)


# =============================================================================
# LOGGER FACTORY
# =============================================================================


def get_logger(name: str) -> BoundLogger:
    """Get a pre-configured logger instance for the given module.

    Args:
        name: Module name (typically __name__)

    Returns:
        Pre-configured structlog BoundLogger with module context

    Example:
        ```python
        logger = get_logger(__name__)
        logger.info("Operation complete", operation="sync")
        ```
    """
    return structlog.stdlib.get_logger(name, service="mixd", module=name)


# =============================================================================
# CONTEXT MANAGEMENT
# =============================================================================


@contextmanager
def logging_context(**kwargs: object) -> Iterator[None]:
    """Bind structured log context for the duration of a block.

    Replaces loguru's ``logger.contextualize()``. Uses structlog's contextvars
    for async-safe propagation across ``await`` boundaries.

    Example:
        ```python
        with logging_context(workflow_id=42, run_id="abc123"):
            logger.info("Starting workflow")  # includes workflow_id, run_id
        ```
    """
    bind_contextvars(**kwargs)
    try:
        yield
    finally:
        unbind_contextvars(*kwargs.keys())


# =============================================================================
# PER-WORKFLOW-RUN JSONL SINKS
# =============================================================================

_active_run_handlers: dict[str, logging.Handler] = {}


def add_workflow_run_logger(workflow_id: str, run_id: str) -> str:
    """Add a temporary handler that writes per-run JSONL log file.

    Returns the run_id as a handle for ``remove_workflow_run_logger()``.
    The handler only captures log entries with a matching ``workflow_run_id``
    in the structlog context.
    """
    log_dir = Path(settings.workflow_log_dir) / workflow_id
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{run_id}.jsonl"

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_shared_processors(),
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
    )

    class _RunHandler(logging.FileHandler):
        """FileHandler that only writes logs with a matching workflow_run_id.

        Uses structlog's public API to read contextvars directly, avoiding
        a full context copy on every log record.
        """

        @override
        def emit(self, record: logging.LogRecord) -> None:
            from structlog.contextvars import get_contextvars

            if get_contextvars().get("workflow_run_id") == run_id:
                super().emit(record)

    handler = _RunHandler(str(log_path))
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(formatter)

    logging.getLogger().addHandler(handler)
    _active_run_handlers[run_id] = handler
    return run_id


def remove_workflow_run_logger(handle: str) -> None:
    """Remove a per-run log handler after workflow execution completes."""
    handler = _active_run_handlers.pop(handle, None)
    if handler:
        logging.getLogger().removeHandler(handler)
        handler.close()


# =============================================================================
# RICH PROGRESS CONSOLE COORDINATION
# =============================================================================

_saved_console_handler: logging.Handler | None = None


class _RichProgressHandler(logging.Handler):
    """Logging handler that routes output through a Rich Console."""

    def __init__(self, console: Console) -> None:
        super().__init__()
        self._console = console

    @override
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._console.print(msg, highlight=False)
        except Exception:
            self.handleError(record)


def enable_unified_console_output(progress_console: Console) -> None:
    """Route all console logging through Rich Progress.console.

    Ensures log messages appear above pinned progress bars rather than
    interfering with them. File logging is unaffected.

    Args:
        progress_console: Rich Console instance from Progress (progress.console)
    """
    global _saved_console_handler
    root = logging.getLogger()

    # Find and remove the current console StreamHandler
    for h in root.handlers[:]:
        if isinstance(h, logging.StreamHandler) and not isinstance(
            h, (logging.FileHandler, logging.handlers.RotatingFileHandler)
        ):
            handler = cast(logging.Handler, h)
            _saved_console_handler = handler
            root.removeHandler(handler)
            break

    # Add handler that writes through Progress.console
    rich_handler = _RichProgressHandler(progress_console)
    rich_handler.setLevel(
        logging.getLevelNamesMapping()[settings.logging.console_level]
    )
    rich_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=_shared_processors(),
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(colors=False),
            ],
        )
    )
    root.addHandler(rich_handler)


def restore_standard_console_output() -> None:
    """Restore standard console handler after Progress coordination ends."""
    global _saved_console_handler
    root = logging.getLogger()

    # Remove Rich progress handler(s)
    for h in root.handlers[:]:
        if isinstance(h, _RichProgressHandler):
            root.removeHandler(h)
            h.close()

    # Restore saved console handler
    if _saved_console_handler:
        root.addHandler(_saved_console_handler)
        _saved_console_handler = None


# =============================================================================
# ROTATION / RETENTION HELPERS
# =============================================================================


def _parse_rotation(rotation_str: str) -> int:
    """Convert '10 MB' to bytes for RotatingFileHandler.maxBytes."""
    parts = rotation_str.strip().split()
    value = float(parts[0])
    unit = parts[1].upper() if len(parts) > 1 else "B"
    multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
    return int(value * multipliers.get(unit, 1))


def _parse_retention(retention_str: str) -> int:
    """Convert '1 week' to backup count for RotatingFileHandler.backupCount."""
    lower = retention_str.lower().strip()
    if "week" in lower:
        weeks = int(lower.split()[0]) if lower[0].isdigit() else 1
        return weeks * 7
    if "day" in lower:
        return int(lower.split()[0]) if lower[0].isdigit() else 3
    if "month" in lower:
        return 30
    return 7  # default
