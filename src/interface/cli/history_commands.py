"""CLI commands for importing and managing play history from music services."""

from pathlib import Path
from typing import Annotated

from rich.prompt import Prompt
import typer

from src.config import settings
from src.interface.cli.async_runner import run_async
from src.interface.cli.cli_helpers import (
    parse_date_string,
    prompt_batch_size,
    run_import_with_progress,
    validate_date_range,
    validate_file_path,
)
from src.interface.cli.console import get_console, print_brand_title
from src.interface.cli.interactive_menu import MenuOption, run_interactive_menu
from src.interface.cli.ui import display_operation_result

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
    # Parse and validate dates
    from_datetime = parse_date_string(from_date, "from-date")
    to_datetime = parse_date_string(to_date, "to-date")
    validate_date_range(from_datetime, to_datetime)

    # Execute import with unified progress context
    result = run_import_with_progress(
        service="lastfm",
        mode="incremental",
        from_date=from_datetime,
        to_date=to_datetime,
    )

    console.print("[bold green]✓ Last.fm import completed![/bold green]")
    if result:
        display_operation_result(result)


@app.command(name="import-spotify")
def import_spotify_cmd(
    file_path: Annotated[
        Path | None,
        typer.Argument(
            help="Path to Spotify JSON export file. If not provided, processes all Streaming_History_Audio_*.json files in data/imports/"
        ),
    ] = None,
    batch_size: Annotated[
        int | None,
        typer.Option(
            "--batch-size",
            "-b",
            help="Number of tracks to process in each batch (larger = faster but more memory)",
        ),
    ] = None,
) -> None:
    """Import play history from Spotify JSON export file(s).

    This command processes JSON files from Spotify's data export feature. To get your data:
    1. Go to Spotify Account Overview → Privacy Settings → Request Data
    2. Wait for Spotify to prepare your data (can take several days)
    3. Download and extract the files
    4. Place files in data/imports/ or provide a specific path

    Without a file path: Processes all Streaming_History_Audio_*.json files in data/imports/
    and moves them to data/imports/imported/ when complete.

    With a file path: Processes the specified file only (does not move it).

    The import will create tracks in your local library and record when you played them.
    Large files are processed in batches to manage memory usage efficiently.
    """
    if file_path:
        # Single file mode (original behavior)
        _import_single_spotify_file(file_path, batch_size)
    else:
        # Batch mode: process all pending files
        _import_all_spotify_files(batch_size)


def _import_single_spotify_file(file_path: Path, batch_size: int | None) -> None:
    """Import a single Spotify JSON file."""
    # Validate file
    validate_file_path(file_path)

    # Check file size and warn if very large
    file_size_mb = file_path.stat().st_size / (1024 * 1024)
    if file_size_mb > settings.import_settings.file_size_warning_mb:
        console.print(
            f"[yellow]Large file detected ({file_size_mb:.1f}MB). This may take several minutes.[/yellow]"
        )

    console.print(f"[cyan]Processing:[/cyan] {file_path.name}")

    # Execute import with unified progress context
    result = run_import_with_progress(
        service="spotify",
        mode="file",
        file_path=file_path,
        batch_size=batch_size,
    )

    console.print(f"[bold green]✓ Imported {file_path.name}[/bold green]")
    if result:
        display_operation_result(result)


def _import_all_spotify_files(batch_size: int | None) -> None:
    """Import all Spotify JSON files from the imports directory."""
    from src.application.services.batch_file_import_service import (
        BatchFileImportService,
    )

    imports_dir = settings.import_settings.imports_dir
    imported_dir = settings.import_settings.imported_dir
    pattern = "Streaming_History_Audio_*.json"

    # Create batch import service with import executor
    service = BatchFileImportService(import_executor=run_import_with_progress)

    # Discover files
    pending_files = service.discover_files(imports_dir, pattern)

    if not pending_files:
        console.print(
            f"[yellow]No Spotify history files found in {imports_dir}[/yellow]"
        )
        console.print(f"[dim]Looking for files matching pattern: {pattern}[/dim]")
        return

    # Display files to be imported
    print_brand_title(f"Found {len(pending_files)} file(s) to import")
    for idx, file_path in enumerate(pending_files, 1):
        console.print(f"  {idx}. {file_path.name}")

    console.print()

    # Execute batch import with progress tracking
    from src.domain.entities.progress import NullProgressEmitter

    # Note: Using NullProgressEmitter since each file import creates its own progress
    result = service.import_files_batch(
        service="spotify",
        imports_dir=imports_dir,
        imported_dir=imported_dir,
        pattern=pattern,
        batch_size=batch_size,
        progress_emitter=NullProgressEmitter(),
    )

    # Display summary
    console.print("\n[bold]Import Summary[/bold]")
    console.print(f"  [green]✓ Successful:[/green] {result.successful}")
    if result.failed > 0:
        console.print(f"  [red]✗ Failed:[/red] {result.failed}")
        console.print("\n[bold]Failed Files:[/bold]")
        for failed_file in result.failed_files:
            console.print(f"  [red]• {failed_file}[/red]")
    console.print("[bold green]✓ Batch import completed![/bold green]")


@app.command(name="checkpoints")
def checkpoints_cmd() -> None:
    """Show sync checkpoint status for all service/entity combinations.

    Displays when each service was last synced and whether a previous sync exists,
    helping you decide if an incremental import is needed.
    """
    run_async(_show_checkpoints_async())


async def _show_checkpoints_async() -> None:
    """Display checkpoint statuses in a Rich table."""
    try:
        from rich.table import Table

        from src.application.use_cases.sync_likes import get_all_checkpoint_statuses

        statuses = await get_all_checkpoint_statuses()

        if not statuses:
            console.print("[yellow]No sync checkpoints found.[/yellow]")
            return

        table = Table(title="Sync Checkpoints")
        table.add_column("Service", style="cyan")
        table.add_column("Entity", style="green")
        table.add_column("Last Sync", style="dim")
        table.add_column("Has Previous", justify="center")

        for s in statuses:
            table.add_row(
                s.service,
                s.entity_type,
                str(s.last_sync_timestamp) if s.last_sync_timestamp else "Never",
                "[green]Yes[/green]" if s.has_previous_sync else "[dim]No[/dim]",
            )

        console.print(table)

    except Exception as e:
        from src.interface.cli.cli_helpers import handle_cli_error

        handle_cli_error(e, "Failed to get checkpoints")


def _show_interactive_history_menu() -> None:
    """Display interactive history management menu."""
    run_interactive_menu(
        title="Narada History",
        subtitle="📊 Import your music play history",
        options=[
            MenuOption(
                key="1",
                aliases=["lastfm"],
                label="[bold]Last.fm[/bold] - Import scrobbled play history from your Last.fm account",
                handler=_interactive_lastfm_import,
            ),
            MenuOption(
                key="2",
                aliases=["spotify"],
                label="[bold]Spotify File[/bold] - Import from Spotify data export JSON files",
                handler=_interactive_spotify_import,
            ),
        ],
    )


def _interactive_lastfm_import() -> None:
    """Interactive Last.fm import configuration."""
    console.print("\n[bold]Last.fm Import Configuration[/bold]")
    console.print("[dim]Choose between date range import or incremental sync[/dim]")

    import_type = Prompt.ask(
        "Import type",
        choices=["incremental", "date-range"],
        default="incremental",
    )

    from_date_str: str | None = None
    to_date_str: str | None = None

    if import_type == "date-range":
        from_date_str = (
            Prompt.ask("Start date (YYYY-MM-DD) or leave empty", default="") or None
        )
        to_date_str = (
            Prompt.ask("End date (YYYY-MM-DD) or leave empty", default="") or None
        )

    operation_desc = "date range" if import_type == "date-range" else "incremental"
    console.print(f"\n[green]Starting Last.fm {operation_desc} import...[/green]")
    console.print(
        "[dim]Using smart daily chunking with automatic track resolution[/dim]"
    )

    import_lastfm_cmd(from_date=from_date_str, to_date=to_date_str)


def _interactive_spotify_import() -> None:
    """Interactive Spotify file import configuration."""
    console.print("\n[bold]Spotify File Import Configuration[/bold]")

    # Check for pending files in imports directory
    imports_dir = settings.import_settings.imports_dir
    pending_files = sorted(imports_dir.glob("Streaming_History_Audio_*.json"))

    if pending_files:
        console.print(
            f"[cyan]Found {len(pending_files)} file(s) in {imports_dir}[/cyan]"
        )
        import_mode = Prompt.ask(
            "Import mode",
            choices=["batch", "single"],
            default="batch",
        )
    else:
        import_mode = "single"

    if import_mode == "batch":
        batch_size = prompt_batch_size()
        _import_all_spotify_files(batch_size)
    else:
        # Single file mode
        file_path_str = Prompt.ask("Path to Spotify JSON export file")
        file_path = Path(file_path_str)

        if not file_path.exists():
            console.print(f"[red]File not found: {file_path}[/red]")
            return

        batch_size = prompt_batch_size()
        _import_single_spotify_file(file_path, batch_size)
