"""Main CLI application entry point and command routing."""

from importlib.metadata import version
from pathlib import Path
from typing import Annotated

from rich.console import Console
import typer

from src.config import setup_loguru_logger

VERSION = version("narada")

# Initialize console with reasonable width
console = Console(width=80)

# Initialize main app with modern configuration
app = typer.Typer(
    help=f"🎵 Narada v{VERSION} - Your personal music integration platform",
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=False,
    pretty_exceptions_enable=True,
    pretty_exceptions_short=True,
    pretty_exceptions_show_locals=False,
)


@app.command(name="version", rich_help_panel="⚙️ System")
def version_command() -> None:
    """Show version information."""
    console.print(
        f"[bold bright_blue]🎵 Narada[/bold bright_blue] [dim]v{VERSION}[/dim]"
    )


@app.callback()
def init_cli(
    ctx: typer.Context,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable verbose output"),
    ] = False,
) -> None:
    """Initialize Narada CLI."""
    # Store verbosity in context for subcommands
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose

    # Setup logging first
    setup_loguru_logger(verbose)

    # Create data directory
    Path("data").mkdir(exist_ok=True)


def _register_commands():
    """Register all CLI commands."""
    try:
        # Import command modules here to avoid circular imports
        from src.interface.cli import (
            history_commands,
            likes_commands,
            playlist_commands,
            setup_commands,
            status_commands,
            track_commands,
        )

        # Add command groups using Typer best practices
        app.add_typer(
            playlist_commands.app,
            name="playlist",
            help="Create and manage playlists",
            rich_help_panel="🎼 Playlist Workflows",
        )

        app.add_typer(
            history_commands.app,
            name="history",
            help="Import and manage your music play history",
            rich_help_panel="🔄 Track Data Sync",
        )

        app.add_typer(
            likes_commands.app,
            name="likes",
            help="Import and export your liked tracks across music services",
            rich_help_panel="🔄 Track Data Sync",
        )

        app.add_typer(
            track_commands.track_app,
            name="tracks",
            help="Track management operations including merging duplicates",
            rich_help_panel="🎵 Track Operations",
        )

        # Register individual utility commands
        setup_commands.register_setup_commands(app)
        status_commands.register_status_commands(app)

    except Exception as e:
        console.print(f"[red]Failed to register commands: {e}[/red]")


def main() -> int:
    """Application entry point."""
    try:
        # Let Typer handle command execution
        return app() or 0
    except Exception:
        console.print("[red]Unhandled exception occurred[/red]")
        return 1


# Register commands at module level so they're available for tests
_register_commands()
