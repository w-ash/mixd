"""Main CLI application entry point and command routing."""

from pathlib import Path
from typing import Annotated

import typer

from src import __version__
from src.config import setup_logging
from src.interface.cli.console import get_console, print_banner

VERSION = __version__

# Use shared console for consistent CLI formatting
console = get_console()

# Initialize main app with modern configuration
app = typer.Typer(
    help=f"🎵 Mixd v{VERSION} - Your personal music integration platform",
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=True,
    pretty_exceptions_enable=True,
    pretty_exceptions_short=True,
    pretty_exceptions_show_locals=False,
)


@app.command(name="version", rich_help_panel="⚙️ System")
def version_command() -> None:
    """Show version information."""
    print_banner(VERSION)


@app.callback()
def init_cli(
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable verbose output"),
    ] = False,
) -> None:
    """Initialize Mixd CLI with Rich console management."""
    setup_logging(verbose)
    Path("data").mkdir(exist_ok=True)

    from src.config import log_startup_warnings

    log_startup_warnings()


def _register_commands() -> None:
    """Register all CLI commands."""
    # Import command modules here to avoid circular imports
    from src.interface.cli import (
        connector_commands,
        history_commands,
        likes_commands,
        playlist_commands,
        stats_commands,
        track_commands,
        workflow_commands,
    )

    # Add command groups using Typer best practices
    app.add_typer(
        workflow_commands.app,
        name="workflow",
        help="Execute and manage playlist workflows",
        rich_help_panel="⚡ Workflow Execution",
    )

    app.add_typer(
        playlist_commands.app,
        name="playlist",
        help="Manage stored playlists and data operations",
        rich_help_panel="🎵 Playlist Management",
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

    app.add_typer(
        connector_commands.app,
        name="connectors",
        help="Check music service connector status",
        rich_help_panel="⚙️ System",
    )

    app.add_typer(
        stats_commands.app,
        name="stats",
        help="Library statistics and dashboard",
        rich_help_panel="📊 Library Info",
    )


def main() -> int:
    """Application entry point."""
    try:
        # Let Typer handle command execution
        return app() or 0
    except Exception as e:
        from sqlalchemy.exc import DatabaseError

        if isinstance(e, DatabaseError):
            from src.infrastructure.persistence.database.error_classification import (
                classify_database_error,
            )

            info = classify_database_error(e)
            console.print(f"[red]{info.user_message}[/red]")
            console.print(f"[dim]{info.detail}[/dim]")
        else:
            import traceback

            console.print(f"[red]Unhandled exception occurred: {e}[/red]")
            console.print("[dim]" + traceback.format_exc() + "[/dim]")
        return 1


# Register commands at module level so they're available for tests
_register_commands()
