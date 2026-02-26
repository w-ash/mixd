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

from src.application.services.progress_manager import AsyncProgressManager
from src.config import get_logger

logger = get_logger(__name__)

# Global shared console for entire CLI application
_console: Console | None = None


class SimpleConsoleContext:
    """Simple context for commands that don't need progress tracking.

    Provides basic console access without progress coordination overhead.
    Used when show_live=False in progress_coordination_context.
    """

    console: Console
    live_console: Console

    def __init__(self, console: Console):
        self.console = console
        self.live_console = console  # Backward compatibility

    def get_progress_manager(self):
        """Return None since no progress tracking is needed."""
        return None


class ProgressDisplayContext:
    """Context for commands that need coordinated progress tracking and console output.

    Provides access to both the unified console and progress manager for commands
    that display progress bars while ensuring logs appear above progress displays.
    """

    provider: Any
    console: Console
    live_console: Console
    progress_manager: AsyncProgressManager

    def __init__(self, provider: Any, manager: AsyncProgressManager) -> None:
        self.provider = provider
        self.console = provider.get_console()
        self.live_console = self.console  # Backward compatibility
        self.progress_manager = manager

    def get_progress_manager(self):
        """Return the progress manager for workflow coordination."""
        return self.progress_manager


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
        Shared Rich Console instance with auto-detected terminal width
    """
    global _console
    if _console is None:
        _console = Console()  # Auto-detect terminal width for table expansion
        logger.debug("Global Rich console initialized")
    return _console


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


async def run_workflow_with_progress(
    workflow_def: dict[str, Any], show_progress: bool = True, **parameters: object
) -> tuple[dict[str, Any], Any]:
    """Execute a Prefect workflow with automatic Rich progress bar setup.

    Convenience function that combines workflow execution with progress tracking
    setup. Automatically displays Rich progress bars for CLI users while maintaining
    Prefect UI artifacts for web dashboard users.

    Args:
        workflow_def: JSON workflow definition with tasks and dependencies
        show_progress: Whether to display Rich progress bars (default: True)
        **parameters: Dynamic parameters passed to workflow tasks

    Returns:
        Tuple of (execution context with all task results, structured final result)

    Example:
        # Execute workflow with automatic progress bars
        context, result = await run_workflow_with_progress(
            workflow_def=my_workflow,
            playlist_id="spotify:playlist:123",
            show_progress=True
        )
    """
    # Import here to avoid circular imports
    from src.application.workflows.prefect import run_workflow

    logger.info(
        f"Executing workflow with progress tracking: {workflow_def.get('name', 'unnamed')}"
    )

    if show_progress:
        # Execute with Rich progress context using unified display context
        async with progress_coordination_context(show_live=True) as display_context:
            progress_manager = display_context.get_progress_manager()
            # Pass progress_manager to workflow for CLI progress tracking
            context, result = await run_workflow(
                workflow_def, progress_manager=progress_manager, **parameters
            )
    else:
        # Execute without progress display
        context, result = await run_workflow(
            workflow_def, progress_manager=None, **parameters
        )

    logger.info(
        f"Workflow completed successfully: {result.operation_name} "
        + f"({result.execution_time:.2f}s, {len(result.tracks)} tracks)"
    )

    return context, result
