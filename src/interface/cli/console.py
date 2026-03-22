"""Shared Rich console and Live Display management for Mixd CLI.

Provides app-level console management with contextual Live Display activation
for progress tracking commands. Integrates with Typer's Rich markup support
and maintains consistent formatting across the entire CLI application.
"""

from collections.abc import AsyncGenerator
import contextlib
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel

from src.application.services.progress_manager import AsyncProgressManager
from src.config import get_logger

if TYPE_CHECKING:
    from .progress_provider import RichProgressProvider

logger = get_logger(__name__)

# Brand colors for Rich CLI output (warm gold identity)
GOLD = "#C59A2B"
GOLD_BRIGHT = "#D4AC35"
GOLD_DIM = "#9E7B1F"


# Block-letter MIXD art (raw, no markup — colorized at render time)
_BANNER_ART = (
    "██░   ██░ ██░ ██░  ██░ ██████░  ",
    "███░ ███░ ██░  ██░██░  ██░  ██░ ",
    "████████░ ██░   ███░   ██░  ██░ ",
    "██░█░███░ ██░  ██░██░  ██░  ██░ ",
    "██░   ██░ ██░ ██░  ██░ ██████░  ",
    " ░░    ░░  ░░  ░░   ░░  ░░░░░░  ",
)


def _colorize_blocks(line: str) -> str:
    """Convert raw block art to Rich markup: █ → GOLD_BRIGHT, ░ → GOLD_DIM."""
    result: list[str] = []
    i = 0
    while i < len(line):
        char = line[i]
        if char in ("█", "░"):
            j = i + 1
            while j < len(line) and line[j] == char:
                j += 1
            color = GOLD_BRIGHT if char == "█" else GOLD_DIM
            result.append(f"[{color}]{line[i:j]}[/]")
            i = j
        else:
            result.append(char)
            i += 1
    return "".join(result)


def print_banner(version: str) -> None:
    """Print the block-letter MIXD banner with gold color tiers."""
    console = get_console()
    pad = " " * 13
    lines = [pad + _colorize_blocks(line) for line in _BANNER_ART]
    lines.append("")
    version_text = f"v{version}"
    inner_pad = (len(_BANNER_ART[0]) - len(version_text)) // 2
    lines.append(f"{pad}{' ' * inner_pad}[dim]{version_text}[/]")
    console.print("\n".join(lines))


def brand_panel(content: str, title: str, *, emoji: str = "") -> Panel:
    """Create a Panel with gold brand styling."""
    prefix = f"{emoji} " if emoji else ""
    return Panel.fit(
        content,
        title=f"[bold {GOLD}]{prefix}{title}[/]",
        border_style=GOLD,
    )


def brand_status(message: str):
    """Create a status spinner with gold styling."""
    return get_console().status(f"[bold {GOLD}]{message}")


def print_brand_title(text: str) -> None:
    """Print a section title in bold gold."""
    get_console().print(f"\n[bold {GOLD}]{text}[/]")


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

    provider: RichProgressProvider
    console: Console
    progress_manager: AsyncProgressManager

    def __init__(
        self, provider: RichProgressProvider, manager: AsyncProgressManager
    ) -> None:
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
async def progress_coordination_context(
    show_live: bool = True,
) -> AsyncGenerator[SimpleConsoleContext | ProgressDisplayContext]:
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
