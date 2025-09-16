"""Shared Rich console and Live Display management for Narada CLI.

Provides app-level console management with contextual Live Display activation
for progress tracking commands. Integrates with Typer's Rich markup support
and maintains consistent formatting across the entire CLI application.
"""

from collections.abc import AsyncGenerator
import contextlib
from typing import Any

from rich.console import Console
import typer

from src.config import get_logger

logger = get_logger(__name__)

# Global shared console for entire CLI application
_console: Console | None = None

# Commands that should use Live Display for progress tracking
LIVE_DISPLAY_COMMANDS = {
    "playlist.run",
    "history.import", 
    "likes.sync",
    # Add other long-running commands as needed
}


def get_console() -> Console:
    """Get the global shared Rich console for the CLI.
    
    Creates a console with consistent configuration matching the main app
    settings. All CLI components should use this shared console for
    unified formatting and Live Display integration.
    
    Returns:
        Shared Rich Console instance with 80-character width
    """
    global _console
    if _console is None:
        _console = Console(width=80)  # Match existing app.py configuration
        logger.debug("Global Rich console initialized")
    return _console


@contextlib.asynccontextmanager
async def live_display_context(show_live: bool = True) -> AsyncGenerator[Any]:
    """App-level Progress console context for progress tracking commands.

    Provides a context manager that activates Rich Progress with unified console
    logging for commands that need progress tracking. All console output is routed
    through Progress.console to ensure proper layering of logs above progress bars.

    Args:
        show_live: Whether to activate Progress display (default: True)

    Yields:
        Context object with console access and progress_manager

    Example:
        async with live_display_context(show_live=True) as context:
            # Access console for output
            context.console.print("This appears above progress bars")
            # Access progress manager for workflows
            progress_manager = context.get_progress_manager()
    """
    if not show_live:
        # Create a simple object that just provides console access
        class SimpleContext:
            def __init__(self, console: Console):
                self.console = console
                self.live_console = console  # Backward compatibility

            def get_progress_manager(self):
                return None

        console = get_console()
        logger.debug("Progress display disabled, using regular console")
        yield SimpleContext(console)
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
                # Create context object that provides console and progress manager access
                class ProgressContext:
                    def __init__(self, provider: RichProgressProvider, manager):
                        self.provider = provider
                        self.console = provider.get_console()
                        self.live_console = self.console  # Backward compatibility
                        self.progress_manager = manager

                    def get_progress_manager(self):
                        return self.progress_manager

                yield ProgressContext(progress_provider, progress_manager)
            finally:
                # Unsubscribe when context exits
                if subscription_id:
                    await progress_manager.unsubscribe(subscription_id)
    finally:
        logger.debug("Progress display context completed")


def should_use_live_display(ctx: typer.Context) -> bool:
    """Determine if current command should use Live Display.
    
    Analyzes the command context to decide whether Live Display should be
    activated. This enables smart resource management by only using Live
    Display for commands that actually need progress tracking.
    
    Args:
        ctx: Typer command context
        
    Returns:
        True if command should use Live Display, False otherwise
    """
    if ctx.info_name in LIVE_DISPLAY_COMMANDS:
        logger.debug(f"Command {ctx.info_name} requires Live Display")
        return True
    
    # Check for parent commands (e.g., "playlist run" -> "playlist.run")
    if ctx.parent and ctx.parent.info_name:
        command_path = f"{ctx.parent.info_name}.{ctx.info_name}"
        if command_path in LIVE_DISPLAY_COMMANDS:
            logger.debug(f"Command path {command_path} requires Live Display")
            return True
    
    # Default to no Live Display for simple commands
    return False
