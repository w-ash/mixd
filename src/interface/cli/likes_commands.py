"""CLI commands for importing, exporting, and syncing liked tracks across services."""

from typing import Annotated

from rich.prompt import Prompt
import typer

from src.config.constants import BusinessLimits
from src.interface.cli.async_runner import run_async
from src.interface.cli.cli_helpers import parse_iso_date
from src.interface.cli.console import get_console, get_error_console
from src.interface.cli.interactive_menu import MenuOption, run_interactive_menu
from src.interface.cli.ui import display_operation_result

console = get_console()
err_console = get_error_console()


def _get_lastfm_checkpoint_info() -> str | None:
    """Get Last.fm checkpoint information for display."""
    try:
        from src.application.use_cases.sync_likes import get_sync_checkpoint_status

        checkpoint_status = run_async(
            get_sync_checkpoint_status(service="lastfm", entity_type="likes")
        )
        return checkpoint_status.format_timestamp()
    except Exception:
        # If we can't get checkpoint info, don't break the UI
        return None


# Create likes subcommand app
app = typer.Typer(
    help="Import and export your liked tracks across music services",
    rich_help_panel="💚 Liked Tracks",
)


@app.callback(invoke_without_command=True)
def likes_main(ctx: typer.Context) -> None:
    """Import and export your liked tracks across music services."""
    if ctx.invoked_subcommand is None:
        _show_interactive_likes_menu()


@app.command(name="import-spotify")
def import_spotify_cmd(
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit",
            "-l",
            help="Number of liked tracks to fetch per API request batch (higher = fewer API calls)",
        ),
    ] = None,
    max_imports: Annotated[
        int | None,
        typer.Option(
            "--max-imports",
            "-m",
            help="Maximum total number of liked tracks to import (unlimited if not specified)",
        ),
    ] = None,
) -> None:
    """Import your liked tracks from Spotify into your local library.

    This command connects to your Spotify account and imports all tracks you've liked.
    The tracks are added to your local library and marked as liked, allowing you to
    export them to other services or use them in playlist workflows.

    The import uses Spotify's API efficiently by batching requests. If you have many
    liked tracks, you can limit the total import to test the process first.

    Tracks that already exist and are marked as liked will be skipped automatically.
    """
    from src.application.use_cases.sync_likes import run_spotify_likes_import

    # Execute the import
    with console.status("[bold blue]Importing liked tracks from Spotify..."):
        result = run_async(
            run_spotify_likes_import(
                user_id=BusinessLimits.DEFAULT_USER_ID,
                limit=limit,
                max_imports=max_imports,
            )
        )

    console.print("[bold green]✓ Spotify likes import completed![/bold green]")
    if result:
        display_operation_result(result)


@app.command(name="export-lastfm")
def export_lastfm_cmd(
    batch_size: Annotated[
        int | None,
        typer.Option(
            "--batch-size",
            "-b",
            help="Number of tracks to process per API request batch (Last.fm has rate limits)",
        ),
    ] = None,
    max_exports: Annotated[
        int | None,
        typer.Option(
            "--max-exports",
            "-m",
            help="Maximum total number of tracks to export as loves (unlimited if not specified)",
        ),
    ] = None,
    date: Annotated[
        str | None,
        typer.Option(
            "--date",
            help="Override checkpoint date - export tracks liked since this date (ISO format: 2025-08-01 or 2025-08-01T10:00:00)",
        ),
    ] = None,
) -> None:
    """Export your liked tracks to Last.fm as loved tracks.

    This command takes tracks you've liked in your local library and marks them as
    "loved" on Last.fm. This helps sync your preferences across music services.

    The export respects Last.fm's API rate limits by processing tracks in small batches.
    By default, only tracks liked since the last successful export will be processed
    (incremental sync). Use --date to override this and export tracks since a specific date.

    Your tracks must have accurate artist and title information for successful matching
    on Last.fm. The export will skip tracks that can't be matched.
    """
    # Parse the date if provided
    override_date = parse_iso_date(date)
    if date and override_date is None:
        err_console.print(
            f"[red]Error: Invalid date format '{date}'. Use ISO format like 2025-08-01 or 2025-08-01T10:00:00[/red]"
        )
        raise typer.Exit(1)

    from src.application.use_cases.sync_likes import run_lastfm_likes_export

    # Execute the export
    with console.status("[bold blue]Exporting liked tracks to Last.fm..."):
        result = run_async(
            run_lastfm_likes_export(
                user_id=BusinessLimits.DEFAULT_USER_ID,
                batch_size=batch_size,
                max_exports=max_exports,
                override_date=override_date,
            )
        )

    console.print("[bold green]✓ Last.fm likes export completed![/bold green]")
    if result:
        display_operation_result(result)


def _show_interactive_likes_menu() -> None:
    """Display interactive likes management menu."""

    def _show_checkpoint_info() -> None:
        checkpoint_info = _get_lastfm_checkpoint_info()
        if checkpoint_info:
            console.print(f"\nLast export: {checkpoint_info}")
        else:
            console.print("\n[dim]No previous Last.fm export found[/dim]")

    run_interactive_menu(
        title="Narada Likes",
        subtitle="💚 Manage your liked tracks across services",
        options=[
            MenuOption(
                key="1",
                aliases=["import"],
                label="[bold]Import from Spotify[/bold] - Bring your Spotify liked tracks into your library",
                handler=_interactive_spotify_import,
            ),
            MenuOption(
                key="2",
                aliases=["export"],
                label="[bold]Export to Last.fm[/bold] - Mark your liked tracks as loved on Last.fm",
                handler=_interactive_lastfm_export,
            ),
        ],
        pre_menu=_show_checkpoint_info,
    )


def _interactive_spotify_import() -> None:
    """Interactive Spotify likes import configuration."""
    console.print("\n[bold]Spotify Likes Import Configuration[/bold]")

    limit_str = Prompt.ask(
        "API batch size (tracks per request, leave empty for default)",
        default="",
    )
    limit = int(limit_str) if limit_str else None

    max_imports_str = Prompt.ask(
        "Maximum tracks to import (leave empty for unlimited)",
        default="",
    )
    max_imports = int(max_imports_str) if max_imports_str else None

    console.print("\n[green]Starting Spotify likes import...[/green]")
    import_spotify_cmd(limit=limit, max_imports=max_imports)


def _interactive_lastfm_export() -> None:
    """Interactive Last.fm likes export configuration."""
    console.print("\n[bold]Last.fm Likes Export Configuration[/bold]")

    # Show current checkpoint and ask for date override
    checkpoint_info = _get_lastfm_checkpoint_info()
    if checkpoint_info:
        default_msg = f"incremental since {checkpoint_info}"
    else:
        default_msg = "full export"

    date_str = Prompt.ask(
        f"Override date (leave empty for {default_msg})",
        default="",
    )

    # Validate date interactively — only pass valid dates to command
    override_date = parse_iso_date(date_str)
    if date_str and override_date is None:
        console.print(
            f"[red]Invalid date format '{date_str}'. Using {default_msg} instead.[/red]"
        )
        date_str = None  # Don't pass invalid date to command
    elif override_date is not None:
        console.print(
            f"[green]Using override date: {override_date.strftime('%Y-%m-%d %H:%M:%S')}[/green]"
        )

    batch_size_str = Prompt.ask(
        "API batch size (tracks per request, leave empty for default)",
        default="",
    )
    batch_size = int(batch_size_str) if batch_size_str else None

    max_exports_str = Prompt.ask(
        "Maximum tracks to export (leave empty for unlimited)",
        default="",
    )
    max_exports = int(max_exports_str) if max_exports_str else None

    console.print("\n[green]Starting Last.fm likes export...[/green]")
    export_lastfm_cmd(batch_size=batch_size, max_exports=max_exports, date=date_str)
