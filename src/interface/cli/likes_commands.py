"""Liked tracks import/export commands for Narada CLI - following Clean Architecture."""

import asyncio
from typing import Annotated

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
import typer

from src.application.use_cases.sync_likes import (
    run_lastfm_likes_export,
    run_spotify_likes_import,
)
from src.interface.shared.ui import display_operation_result

console = Console()

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
) -> None:
    """Export your liked tracks to Last.fm as loved tracks.

    This command takes tracks you've liked in your local library and marks them as
    "loved" on Last.fm. This helps sync your preferences across music services.

    The export respects Last.fm's API rate limits by processing tracks in small batches.
    Only tracks that aren't already loved on Last.fm will be exported, making the
    process efficient for incremental syncing.

    Your tracks must have accurate artist and title information for successful matching
    on Last.fm. The export will skip tracks that can't be matched.
    """
    # Execute the export
    with console.status("[bold blue]Exporting liked tracks to Last.fm..."):
        result = asyncio.run(
            run_lastfm_likes_export(
                user_id="default",  # Internal identifier, not exposed to user
                batch_size=batch_size,
                max_exports=max_exports,
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
            )
        )

    console.print("[bold green]✓ Last.fm likes export completed![/bold green]")
    if result:
        display_operation_result(result)
