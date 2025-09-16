"""CLI commands for importing and managing play history from music services."""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Annotated

from rich.panel import Panel
from rich.prompt import Prompt
import typer

from src.application.services.progress_manager import AsyncProgressManagerAdapter
from src.application.use_cases.import_play_history import run_import

# Removed: workflow_progress_context - using unified live_display_context instead
from src.config import settings
from src.domain.entities.progress import NullProgressEmitter
from src.interface.cli.console import get_console, live_display_context
from src.interface.shared.ui import display_operation_result

console = get_console()

# Create history subcommand app
app = typer.Typer(
    help="Import and manage your music play history",
    rich_help_panel="📊 Play History",
)


@app.callback(invoke_without_command=True)
def history_main(ctx: typer.Context) -> None:
    """Import and manage your music play history."""
    if ctx.invoked_subcommand is None:
        _show_interactive_history_menu()


@app.command(name="import-lastfm")
def import_lastfm_cmd(
    from_date: Annotated[
        str | None,
        typer.Option(
            "--from-date",
            help="Start date for import (YYYY-MM-DD format). Establishes import window on first run.",
        ),
    ] = None,
    to_date: Annotated[
        str | None,
        typer.Option(
            "--to-date",
            help="End date for import (YYYY-MM-DD format). Defaults to now.",
        ),
    ] = None,
) -> None:
    """Import play history from Last.fm using smart daily chunking.

    Two usage patterns:
    1. Explicit range: --from-date 2025-03-01 --to-date 2025-08-01
       (establishes or expands your import window)
    2. Incremental: no parameters (imports from last checkpoint to now)

    Features:
    • Smart daily chunking with auto-scaling for power users
    • Resumable imports with checkpoint tracking
    • Comprehensive track resolution and deduplication
    • Chronological processing (oldest → newest)
    """
    # Note: Operation type determined by presence of date parameters

    # Parse and validate dates
    from datetime import UTC

    from_datetime = None
    to_datetime = None
    if from_date:
        try:
            from_datetime = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            console.print(
                f"[red]Invalid from-date format: {from_date}. Use YYYY-MM-DD format.[/red]"
            )
            raise typer.Exit(1) from None

    if to_date:
        try:
            to_datetime = datetime.strptime(to_date, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            console.print(
                f"[red]Invalid to-date format: {to_date}. Use YYYY-MM-DD format.[/red]"
            )
            raise typer.Exit(1) from None

    if from_datetime and to_datetime and from_datetime > to_datetime:
        console.print("[red]from-date cannot be later than to-date[/red]")
        raise typer.Exit(1)

    # Execute the import with Rich Live Display and progress bars
    async def _run_import_with_progress():
        async with live_display_context(show_live=True) as context:
            # Get progress manager from unified context
            progress_manager = context.get_progress_manager()

            # Create adapter to implement ProgressEmitter protocol
            progress_adapter = (
                AsyncProgressManagerAdapter(progress_manager)
                if progress_manager
                else NullProgressEmitter()
            )
            return await run_import(
                service="lastfm",
                mode="incremental",  # Always use incremental (unified approach)
                from_date=from_datetime,
                to_date=to_datetime,
                progress_emitter=progress_adapter,
            )

    result = asyncio.run(_run_import_with_progress())

    console.print("[bold green]✓ Last.fm import completed![/bold green]")
    if result:
        display_operation_result(result)


@app.command(name="import-spotify")
def import_spotify_cmd(
    file_path: Annotated[
        Path,
        typer.Argument(
            help="Path to your Spotify JSON export file (usually 'StreamingHistory.json' or similar)"
        ),
    ],
    batch_size: Annotated[
        int | None,
        typer.Option(
            "--batch-size",
            "-b",
            help="Number of tracks to process in each batch (larger = faster but more memory)",
        ),
    ] = None,
) -> None:
    """Import play history from a Spotify JSON export file.

    This command processes JSON files from Spotify's data export feature. To get your data:
    1. Go to Spotify Account Overview → Privacy Settings → Request Data
    2. Wait for Spotify to prepare your data (can take several days)
    3. Download and extract the files
    4. Use the streaming history JSON files with this command

    The import will create tracks in your local library and record when you played them.
    Large files are processed in batches to manage memory usage efficiently.
    """
    # Validate file exists and is readable
    if not file_path.exists():
        console.print(f"[red]File not found: {file_path}[/red]")
        console.print("Make sure the path is correct and the file exists.")
        raise typer.Exit(1)

    if not file_path.is_file():
        console.print(f"[red]Path is not a file: {file_path}[/red]")
        raise typer.Exit(1)

    # Check file size and warn if very large
    file_size_mb = file_path.stat().st_size / (1024 * 1024)
    if file_size_mb > settings.import_settings.file_size_warning_mb:
        console.print(
            f"[yellow]Large file detected ({file_size_mb:.1f}MB). This may take several minutes.[/yellow]"
        )

    # Execute the import with Rich Live Display and progress bars
    async def _run_spotify_import_with_progress():
        async with live_display_context(show_live=True) as context:
            # Get progress manager from unified context
            progress_manager = context.get_progress_manager()
            # Create adapter to implement ProgressEmitter protocol
            progress_adapter = (
                AsyncProgressManagerAdapter(progress_manager)
                if progress_manager
                else NullProgressEmitter()
            )
            return await run_import(
                service="spotify",
                mode="file",
                file_path=file_path,
                batch_size=batch_size,
                progress_emitter=progress_adapter,
            )

    result = asyncio.run(_run_spotify_import_with_progress())

    console.print("[bold green]✓ Spotify file import completed![/bold green]")
    if result:
        display_operation_result(result)


def _show_interactive_history_menu() -> None:
    """Display interactive history management menu."""
    console.print(
        Panel.fit(
            "📊 Import your music play history",
            title="[bold blue]Narada History[/bold blue]",
            border_style="blue",
        )
    )

    console.print("\n📥 [bold]Available Import Sources[/bold]:")
    console.print(
        "  [cyan]1[/cyan]. [bold]Last.fm[/bold] - Import scrobbled play history from your Last.fm account"
    )
    console.print(
        "  [cyan]2[/cyan]. [bold]Spotify File[/bold] - Import from Spotify data export JSON files"
    )

    choice = Prompt.ask(
        "Select import source [1-2] or type 'lastfm'/'spotify'",
        choices=["1", "2", "lastfm", "spotify", "q", "quit", "exit", "cancel"],
        default="",
        show_choices=False,
    ).strip()

    if choice in ("", "q", "quit", "exit", "cancel"):
        return

    # Handle selection
    if choice in ("1", "lastfm"):
        _interactive_lastfm_import()
    elif choice in ("2", "spotify"):
        _interactive_spotify_import()


def _interactive_lastfm_import() -> None:
    """Interactive Last.fm import configuration."""
    console.print("\n[bold]Last.fm Import Configuration[/bold]")
    console.print("[dim]Choose between date range import or incremental sync[/dim]")

    import_type = Prompt.ask(
        "Import type",
        choices=["incremental", "date-range"],
        default="incremental",
    )

    from_date = None
    to_date = None

    if import_type == "date-range":
        from_date_str = Prompt.ask("Start date (YYYY-MM-DD) or leave empty", default="")
        to_date_str = Prompt.ask("End date (YYYY-MM-DD) or leave empty", default="")

        from datetime import UTC

        if from_date_str:
            from_date = datetime.strptime(from_date_str, "%Y-%m-%d").replace(tzinfo=UTC)
        if to_date_str:
            to_date = datetime.strptime(to_date_str, "%Y-%m-%d").replace(tzinfo=UTC)

    # Execute with gathered parameters
    operation_desc = "date range" if import_type == "date-range" else "incremental"
    console.print(f"\n[green]Starting Last.fm {operation_desc} import...[/green]")
    console.print(
        "[dim]Using smart daily chunking with automatic track resolution[/dim]"
    )

    # Execute with Rich Live Display and progress bars
    async def _run_interactive_import():
        async with live_display_context(show_live=True) as context:
            # Get progress manager from unified context
            progress_manager = context.get_progress_manager()
            # Create adapter to implement ProgressEmitter protocol
            progress_adapter = (
                AsyncProgressManagerAdapter(progress_manager)
                if progress_manager
                else NullProgressEmitter()
            )
            return await run_import(
                service="lastfm",
                mode="incremental",  # Always use unified approach
                from_date=from_date,
                to_date=to_date,
                progress_emitter=progress_adapter,
            )

    result = asyncio.run(_run_interactive_import())

    console.print("[bold green]✓ Last.fm import completed![/bold green]")
    if result:
        display_operation_result(result)


def _interactive_spotify_import() -> None:
    """Interactive Spotify file import configuration."""
    console.print("\n[bold]Spotify File Import Configuration[/bold]")

    file_path_str = Prompt.ask("Path to Spotify JSON export file")
    file_path = Path(file_path_str)

    if not file_path.exists():
        console.print(f"[red]File not found: {file_path}[/red]")
        return

    batch_size_str = Prompt.ask(
        "Batch size (leave empty for default)",
        default="",
    )
    batch_size = int(batch_size_str) if batch_size_str else None

    # Execute with gathered parameters
    console.print("\n[green]Starting Spotify file import...[/green]")

    # Execute with Rich Live Display and progress bars
    async def _run_interactive_spotify_import():
        async with live_display_context(show_live=True) as context:
            # Get progress manager from unified context
            progress_manager = context.get_progress_manager()
            # Create adapter to implement ProgressEmitter protocol
            progress_adapter = (
                AsyncProgressManagerAdapter(progress_manager)
                if progress_manager
                else NullProgressEmitter()
            )
            return await run_import(
                service="spotify",
                mode="file",
                file_path=file_path,
                batch_size=batch_size,
                progress_emitter=progress_adapter,
            )

    result = asyncio.run(_run_interactive_spotify_import())

    console.print("[bold green]✓ Spotify file import completed![/bold green]")
    if result:
        display_operation_result(result)
