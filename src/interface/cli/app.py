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


@app.command(name="whoami", rich_help_panel="⚙️ System")
def whoami_command() -> None:
    """Show current user identity, database, mode, and connector status."""
    from urllib.parse import urlparse

    from rich.panel import Panel
    from rich.table import Table

    from src.config.settings import get_database_url
    from src.interface.cli.async_runner import run_async
    from src.interface.cli.cli_helpers import get_cli_user_id

    user_id = get_cli_user_id()
    db_url = get_database_url()

    # Mask password in DB URL for display
    parsed = urlparse(db_url.replace("+psycopg", ""))
    masked = db_url.replace(parsed.password, "****") if parsed.password else db_url
    db_host = parsed.hostname or "unknown"
    mode = "local" if db_host in ("localhost", "127.0.0.1") else "remote"

    lines = [
        f"[cyan]User ID:[/cyan]  {user_id}",
        f"[cyan]Mode:[/cyan]     {mode}",
        f"[cyan]Database:[/cyan] {masked}",
    ]
    console.print(Panel("\n".join(lines), title="[bold]Mixd Identity[/bold]"))

    # Connector status
    async def _status():
        from src.infrastructure.connectors._shared.connector_status import (
            get_all_connector_statuses,
        )

        return await get_all_connector_statuses(user_id)

    try:
        statuses = run_async(_status())
        table = Table(title="Connectors", show_header=True)
        table.add_column("Service", style="cyan")
        table.add_column("Status")
        table.add_column("Account", style="dim")

        for s in statuses:
            status_str = (
                "[green]Connected[/green]" if s.connected else "[red]Disconnected[/red]"
            )
            table.add_row(s.name, status_str, s.account_name or "—")

        console.print(table)
    except Exception:
        console.print("[dim]Could not check connector status.[/dim]")


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
    _warn_if_no_data()


def _warn_if_no_data() -> None:
    """Best-effort warning when MIXD_USER_ID is set but no data exists for that user."""
    try:
        from src.config.constants import BusinessLimits
        from src.config.settings import settings

        user_id = settings.cli.user_id
        if not user_id or user_id == BusinessLimits.DEFAULT_USER_ID:
            return

        from src.config.settings import get_sync_database_url

        db_url = get_sync_database_url()
        if not db_url:
            return

        import psycopg

        with psycopg.connect(db_url, autocommit=True) as conn:
            row = conn.execute(
                "SELECT count(*) FROM tracks WHERE user_id = %s", (user_id,)
            ).fetchone()
            if row and row[0] == 0:
                console.print(
                    f"[yellow]No data found for user {user_id}. "
                    f"Check MIXD_USER_ID or import data first.[/yellow]"
                )
    except Exception:
        return  # Best-effort — startup check must never break CLI


def _register_commands() -> None:
    """Register all CLI commands."""
    # Import command modules here to avoid circular imports
    from src.interface.cli import (
        connector_commands,
        history_commands,
        likes_commands,
        playlist_commands,
        review_commands,
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

    app.add_typer(
        review_commands.app,
        name="reviews",
        help="Manage pending track match reviews",
        rich_help_panel="🎵 Track Operations",
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
