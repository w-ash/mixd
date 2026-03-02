"""Shared Rich console and Live Display management for Narada CLI.

Provides app-level console management with contextual Live Display activation
for progress tracking commands. Integrates with Typer's Rich markup support
and maintains consistent formatting across the entire CLI application.
"""

from collections.abc import AsyncGenerator
import contextlib
from typing import Any

from rich.console import Console

from src.application.services.progress_manager import AsyncProgressManager
from src.config import get_logger

logger = get_logger(__name__)

# Global shared consoles for entire CLI application
_console: Console | None = None
_error_console: Console | None = None


class SimpleConsoleContext:
    """Simple context for commands that don't need progress tracking.

    Provides basic console access without progress coordination overhead.
    Used when show_live=False in progress_coordination_context.
    """

    console: Console

    def __init__(self, console: Console):
        self.console = console

    def get_progress_manager(self) -> None:
        """Return None since no progress tracking is needed."""
        return None


class ProgressDisplayContext:
    """Context for commands that need coordinated progress tracking and console output.

    Provides access to both the unified console and progress manager for commands
    that display progress bars while ensuring logs appear above progress displays.
    """

    provider: Any
    console: Console
    progress_manager: AsyncProgressManager

    def __init__(self, provider: Any, manager: AsyncProgressManager) -> None:
        self.provider = provider
        self.console = provider.get_console()
        self.progress_manager = manager

    def get_progress_manager(self) -> AsyncProgressManager:
        """Return the progress manager for workflow coordination."""
        return self.progress_manager


def get_console() -> Console:
    """Get the global shared Rich console for the CLI.

    Creates a console with consistent configuration matching the main app
    settings. All CLI components should use this shared console for
    unified formatting and Live Display integration.

    Returns:
        Shared Rich Console instance with auto-detected terminal width
    """
    global _console
    if _console is None:
        _console = Console()  # Auto-detect terminal width for table expansion
        logger.debug("Global Rich console initialized")
    return _console


def get_error_console() -> Console:
    """Get the global shared Rich error console (stderr) for the CLI.

    Returns a Console that writes to stderr, giving error messages Rich markup
    formatting while keeping them on the correct output stream for piping/scripting.

    Returns:
        Shared Rich Console instance writing to stderr
    """
    global _error_console
    if _error_console is None:
        _error_console = Console(stderr=True)
        logger.debug("Global Rich error console initialized")
    return _error_console


@contextlib.asynccontextmanager
async def progress_coordination_context(show_live: bool = True) -> AsyncGenerator[Any]:
    """Context manager for coordinated progress tracking and console output.

    Provides a context manager that coordinates Rich Progress with unified console
    logging for commands that need progress tracking. All console output is routed
    through Progress.console to ensure proper layering of logs above progress bars.

    Args:
        show_live: Whether to activate Progress display (default: True)

    Yields:
        Context object with console access and progress_manager

    Example:
        async with progress_coordination_context(show_live=True) as context:
            # Access console for output
            context.console.print("This appears above progress bars")
            # Access progress manager for workflows
            progress_manager = context.get_progress_manager()
    """
    if not show_live:
        console = get_console()
        logger.debug("Progress display disabled, using regular console")
        yield SimpleConsoleContext(console)
        return

    # Use RichProgressProvider for unified Progress.console coordination
    from .progress_provider import RichProgressProvider

    progress_provider = RichProgressProvider()

    logger.debug("Starting Progress display context with RichProgressProvider")

    try:
        async with progress_provider:
            # Get the global progress manager and subscribe our provider
            from src.application.services.progress_manager import get_progress_manager

            progress_manager = get_progress_manager()
            subscription_id = await progress_manager.subscribe(progress_provider)

            try:
                yield ProgressDisplayContext(progress_provider, progress_manager)
            finally:
                # Unsubscribe when context exits
                if subscription_id:
                    _ = await progress_manager.unsubscribe(subscription_id)
    finally:
        logger.debug("Progress display context completed")
