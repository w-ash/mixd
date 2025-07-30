"""CLI commands for importing and managing play history from music services."""

import asyncio
from pathlib import Path
from typing import Annotated

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
import typer

from src.application.use_cases.import_play_history import ImportMode, run_import
from src.interface.shared.ui import display_operation_result


def _validate_import_mode(mode_str: str) -> ImportMode | None:
    """Validate import mode string."""
    valid_modes: list[ImportMode] = ["recent", "incremental", "full"]
    if mode_str in valid_modes:
        return mode_str  # Now properly typed as ImportMode
    return None


console = Console()

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
    mode: Annotated[
        str,
        typer.Option(
            "--mode",
            "-m",
            help="Import mode: 'recent' for latest plays, 'incremental' for new plays since last sync, 'full' for complete history (destructive)",
        ),
    ] = "incremental",
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit",
            "-l",
            help="Number of recent plays to import (only used with 'recent' mode)",
        ),
    ] = None,
    resolve_tracks: Annotated[
        bool,
        typer.Option(
            "--resolve/--no-resolve",
            help="Enable track identity resolution for better matching (recommended)",
        ),
    ] = True,
) -> None:
    """Import play history from Last.fm using the configured account.

    This command connects to Last.fm and imports your scrobbled tracks into your local library.
    Different modes allow you to control how much data is imported:

    • recent: Import the most recent N plays (specify with --limit)
    • incremental: Import only new plays since your last sync (default, efficient)
    • full: Import your entire play history, resetting any existing sync state

    Track resolution improves matching accuracy by looking up additional metadata.
    """
    # Validate mode parameter
    validated_mode = _validate_import_mode(mode)
    if not validated_mode:
        console.print(
            f"[red]Invalid mode: {mode}. Must be 'recent', 'incremental', or 'full'[/red]"
        )
        raise typer.Exit(1)

    # Validate limit parameter
    if validated_mode == "recent" and limit is None:
        limit = 100  # Default for recent mode
    elif validated_mode != "recent" and limit is not None:
        console.print(
            "[yellow]Warning: --limit is only used with 'recent' mode[/yellow]"
        )

    # Show confirmation for full mode
    if validated_mode == "full":
        console.print("[yellow]⚠️  Full History Import Warning[/yellow]")
        console.print("This will:")
        console.print("• Import your entire Last.fm play history")
        console.print("• Reset any existing sync checkpoint")
        console.print("• Make many API calls (may take 10+ minutes)")

        if not typer.confirm("Do you want to proceed?"):
            console.print("[dim]Full history import cancelled[/dim]")
            return

    # Execute the import
    with console.status(f"[bold blue]Importing {validated_mode} plays from Last.fm..."):
        result = asyncio.run(
            run_import(
                service="lastfm",
                mode=validated_mode,  # type: ignore[arg-type]
                limit=limit,
                resolve_tracks=resolve_tracks,
                confirm=True,  # Already handled confirmation above
            )
        )

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
    if file_size_mb > 100:
        console.print(
            f"[yellow]Large file detected ({file_size_mb:.1f}MB). This may take several minutes.[/yellow]"
        )

    # Execute the import
    with console.status(
        f"[bold blue]Processing Spotify export file: {file_path.name}..."
    ):
        result = asyncio.run(
            run_import(
                service="spotify",
                mode="file",
                file_path=file_path,
                batch_size=batch_size,
            )
        )

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

    mode_str = Prompt.ask(
        "Import mode",
        choices=["recent", "incremental", "full"],
        default="incremental",
    )
    mode = _validate_import_mode(mode_str)
    if not mode:
        console.print(f"[red]Invalid mode: {mode_str}[/red]")
        return

    limit = None
    if mode == "recent":
        limit_str = Prompt.ask("Number of recent plays to import", default="100")
        limit = int(limit_str)

    resolve_str = Prompt.ask(
        "Enable track resolution for better matching?",
        choices=["y", "n"],
        default="y",
    )
    resolve_tracks = resolve_str.lower() == "y"

    # Execute with gathered parameters
    console.print(f"\n[green]Starting Last.fm {mode} import...[/green]")

    with console.status(f"[bold blue]Importing {mode} plays from Last.fm..."):
        result = asyncio.run(
            run_import(
                service="lastfm",
                mode=mode,
                limit=limit,
                resolve_tracks=resolve_tracks,
                confirm=mode == "full",  # Auto-confirm for full mode in interactive
            )
        )

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

    with console.status(f"[bold blue]Processing {file_path.name}..."):
        result = asyncio.run(
            run_import(
                service="spotify",
                mode="file",
                file_path=file_path,
                batch_size=batch_size,
            )
        )

    console.print("[bold green]✓ Spotify file import completed![/bold green]")
    if result:
        display_operation_result(result)
