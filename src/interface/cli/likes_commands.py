"""CLI commands for importing, exporting, and syncing liked tracks across services."""

import asyncio
from datetime import datetime
from typing import Annotated

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
import typer

from src.application.use_cases.sync_likes import (
    get_sync_checkpoint_status,
    run_lastfm_likes_export,
    run_spotify_likes_import,
)
from src.interface.shared.ui import display_operation_result

console = Console()


def _get_lastfm_checkpoint_info() -> str | None:
    """Get Last.fm checkpoint information for display."""
    try:
        checkpoint_status = asyncio.run(
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
    # Execute the import
    with console.status("[bold blue]Importing liked tracks from Spotify..."):
        result = asyncio.run(
            run_spotify_likes_import(
                user_id="default",  # Internal identifier, not exposed to user
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
    override_date = None
    if date:
        try:
            # Try parsing with time first, then date only
            if "T" in date:
                override_date = datetime.fromisoformat(date)
            else:
                override_date = datetime.fromisoformat(f"{date}T00:00:00+00:00")
        except ValueError:
            console.print(
                f"[red]Error: Invalid date format '{date}'. Use ISO format like 2025-08-01 or 2025-08-01T10:00:00[/red]"
            )
            raise typer.Exit(1) from None

    # Execute the export
    with console.status("[bold blue]Exporting liked tracks to Last.fm..."):
        result = asyncio.run(
            run_lastfm_likes_export(
                user_id="default",  # Internal identifier, not exposed to user
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
    console.print(
        Panel.fit(
            "💚 Manage your liked tracks across services",
            title="[bold blue]Narada Likes[/bold blue]",
            border_style="blue",
        )
    )

    # Show Last.fm checkpoint info if available
    checkpoint_info = _get_lastfm_checkpoint_info()
    if checkpoint_info:
        console.print(f"\nLast export: {checkpoint_info}")
    else:
        console.print("\n[dim]No previous Last.fm export found[/dim]")

    console.print("\n🔄 [bold]Available Operations[/bold]:")
    console.print(
        "  [cyan]1[/cyan]. [bold]Import from Spotify[/bold] - Bring your Spotify liked tracks into your library"
    )
    console.print(
        "  [cyan]2[/cyan]. [bold]Export to Last.fm[/bold] - Mark your liked tracks as loved on Last.fm"
    )

    choice = Prompt.ask(
        "Select operation [1-2] or type 'import'/'export'",
        choices=["1", "2", "import", "export", "q", "quit", "exit", "cancel"],
        default="",
        show_choices=False,
    ).strip()

    if choice in ("", "q", "quit", "exit", "cancel"):
        return

    # Handle selection
    if choice in ("1", "import"):
        _interactive_spotify_import()
    elif choice in ("2", "export"):
        _interactive_lastfm_export()


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

    # Execute with gathered parameters
    console.print("\n[green]Starting Spotify likes import...[/green]")

    with console.status("[bold blue]Importing liked tracks from Spotify..."):
        result = asyncio.run(
            run_spotify_likes_import(
                user_id="default",
                limit=limit,
                max_imports=max_imports,
            )
        )

    console.print("[bold green]✓ Spotify likes import completed![/bold green]")
    if result:
        display_operation_result(result)


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

    override_date = None
    if date_str:
        try:
            # Parse the date
            if "T" in date_str:
                override_date = datetime.fromisoformat(date_str)
            else:
                override_date = datetime.fromisoformat(f"{date_str}T00:00:00+00:00")
            console.print(
                f"[green]Using override date: {override_date.strftime('%Y-%m-%d %H:%M:%S')}[/green]"
            )
        except ValueError:
            console.print(
                f"[red]Invalid date format '{date_str}'. Using {default_msg} instead.[/red]"
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

    # Execute with gathered parameters
    console.print("\n[green]Starting Last.fm likes export...[/green]")

    with console.status("[bold blue]Exporting liked tracks to Last.fm..."):
        result = asyncio.run(
            run_lastfm_likes_export(
                user_id="default",
                batch_size=batch_size,
                max_exports=max_exports,
                override_date=override_date,
            )
        )

    console.print("[bold green]✓ Last.fm likes export completed![/bold green]")
    if result:
        display_operation_result(result)
