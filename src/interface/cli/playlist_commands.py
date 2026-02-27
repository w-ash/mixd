"""CLI commands for playlist data management and CRUD operations.

Clean implementation focused solely on stored playlist operations:
list, backup, delete. Workflow functionality moved to workflow_commands.py.
"""

from collections.abc import Sequence
from typing import Annotated

from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table
import typer

from src.domain.entities.playlist import Playlist
from src.interface.cli.async_runner import run_async
from src.interface.cli.console import get_console

console = get_console()

# Create playlist data management app
app = typer.Typer(
    help="Manage stored playlists and data operations",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@app.command()
def list() -> None:
    """List all playlists stored in your local database.

    Shows playlist ID, name, description, and track count in a Rich table.
    """
    run_async(_list_stored_playlists())


@app.command()
def backup(
    connector: Annotated[str, typer.Argument(help="Connector name (e.g., 'spotify')")],
    playlist_id: Annotated[
        str, typer.Argument(help="Playlist ID from the connector service")
    ],
) -> None:
    """Backup a playlist from a music service to your local database.

    Downloads a playlist from the specified connector (Spotify, etc.) and saves it
    to your local database. If the playlist already exists locally, it will be updated
    with the latest tracks and metadata from the service.

    Examples:
        narada playlist backup spotify 37i9dQZF1DX0XUsuxWHRQd
        narada playlist backup spotify 1A2B3C4D5E6F7G8H9I0J1K
    """
    run_async(_backup_playlist_async(connector, playlist_id))


@app.command()
def delete(
    playlist_id: Annotated[int, typer.Argument(help="Playlist ID to delete")],
    force: Annotated[bool, typer.Option("--force", "-f")] = False,
) -> None:
    """Delete a playlist from your local database.

    Removes the playlist and all associated data permanently. This action
    cannot be undone. Use --force to skip confirmation prompt.

    Examples:
        narada playlist delete 5
        narada playlist delete 3 --force
    """
    run_async(_delete_playlist_async(playlist_id, force))


async def _list_stored_playlists() -> None:
    """List all stored playlists with metadata following DDD principles."""
    try:
        from src.application.runner import execute_use_case
        from src.application.use_cases.list_playlists import ListPlaylistsUseCase

        result = await execute_use_case(lambda uow: ListPlaylistsUseCase(uow).execute())

        if not result.has_playlists:
            console.print("[yellow]No playlists found in your database.[/yellow]")
            return

        # Display Rich table
        _display_playlists_table(result.playlists)

    except Exception as e:
        typer.echo(f"Error: Failed to list playlists: {e}", err=True)
        raise typer.Exit(1) from e


def _display_playlists_table(playlists: Sequence[Playlist]) -> None:
    """Display playlists in a Rich table format."""
    from src.config.settings import settings

    table = Table(
        title="Stored Playlists", show_header=True, header_style="bold magenta"
    )
    table.add_column("ID", style="cyan", no_wrap=True, justify="right")
    table.add_column(
        "Name", style="green", min_width=settings.cli.playlist_name_min_width
    )
    table.add_column(
        "Description",
        style="dim",
        max_width=settings.cli.playlist_description_max_width,
    )
    table.add_column("Tracks", style="yellow", justify="right")
    table.add_column("Last Updated", style="blue", no_wrap=True)

    for playlist in playlists:
        # Note: Domain entities don't expose timestamp fields by design
        # For CLI listing, we show track count as primary metadata
        updated_str = "N/A"

        # Truncate description if too long (using centralized settings)
        from src.config.settings import settings

        max_length = settings.cli.playlist_description_max_width
        truncation_length = settings.cli.playlist_description_truncation_length
        description = playlist.description or ""
        if len(description) > max_length:
            description = description[:truncation_length] + "..."

        table.add_row(
            str(playlist.id),
            playlist.name,
            description,
            str(len(playlist.tracks)),
            updated_str,
        )

    console.print(table)


async def _delete_playlist_async(playlist_id: int, force: bool) -> None:
    """Delete a playlist with confirmation unless forced."""
    try:
        from src.application.runner import execute_use_case
        from src.application.use_cases.delete_canonical_playlist import (
            DeleteCanonicalPlaylistCommand,
            DeleteCanonicalPlaylistUseCase,
        )
        from src.application.use_cases.read_canonical_playlist import (
            ReadCanonicalPlaylistCommand,
            ReadCanonicalPlaylistUseCase,
        )

        # Step 1: Fetch playlist info for confirmation prompt
        try:
            read_result = await execute_use_case(
                lambda uow: ReadCanonicalPlaylistUseCase().execute(
                    ReadCanonicalPlaylistCommand(playlist_id=str(playlist_id)), uow
                )
            )
            playlist = read_result.playlist
        except Exception as e:
            typer.echo(f"Error: Playlist with ID {playlist_id} not found.", err=True)
            raise typer.Exit(1) from e

        if not playlist:
            typer.echo(f"Error: Playlist with ID {playlist_id} not found.", err=True)
            raise typer.Exit(1)  # noqa: TRY301

        # Step 2: Confirmation unless forced
        if not force:
            console.print(
                Panel.fit(
                    f"[bold]{playlist.name}[/bold]\n"
                    + f"[dim]{playlist.description or 'No description'}[/dim]\n"
                    + f"[cyan]Tracks: [bold]{len(playlist.tracks)}[/bold][/cyan]",
                    title="[bold red]⚠️  Delete Playlist[/bold red]",
                    border_style="red",
                )
            )

            if not Confirm.ask(
                "[bold red]Are you sure you want to delete this playlist?[/bold red]\n"
                + "[dim]This action cannot be undone.[/dim]"
            ):
                console.print("[yellow]Delete cancelled.[/yellow]")
                return

        # Step 3: Delete via use case
        delete_result = await execute_use_case(
            lambda uow: DeleteCanonicalPlaylistUseCase().execute(
                DeleteCanonicalPlaylistCommand(
                    playlist_id=str(playlist_id), force_delete=force
                ),
                uow,
            )
        )

        console.print(
            Panel.fit(
                "[bold green]✓ Playlist Deleted[/bold green]\n"
                + f"[cyan]Name:[/cyan] {delete_result.deleted_playlist_name}\n"
                + f"[cyan]ID:[/cyan] {delete_result.deleted_playlist_id}",
                title="[bold green]🗑️  Deletion Complete[/bold green]",
                border_style="green",
            )
        )

    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error: Failed to delete playlist: {e}", err=True)
        raise typer.Exit(1) from e


async def _backup_playlist_async(connector_name: str, playlist_id: str) -> None:
    """Backup a playlist from a connector service to the local database."""
    # Import here to avoid circular dependencies
    from src.application.services.playlist_backup_service import run_playlist_backup

    console.print(
        Panel.fit(
            f"[bold]{connector_name.title()} Playlist Backup[/bold]\n"
            + f"[dim]Playlist ID: {playlist_id}[/dim]",
            title="[bold bright_blue]🎵 Starting Backup[/bold bright_blue]",
            border_style="blue",
        )
    )

    try:
        with console.status(f"[bold blue]Backing up playlist from {connector_name}..."):
            result = await run_playlist_backup(
                connector_name=connector_name, playlist_id=playlist_id
            )

        # Display results based on result type
        from src.application.use_cases.update_canonical_playlist import (
            UpdateCanonicalPlaylistResult,
        )

        if isinstance(result, UpdateCanonicalPlaylistResult):
            # Updated existing playlist
            console.print(
                Panel.fit(
                    "[bold green]✓ Playlist Updated[/bold green]\n"
                    + f"[cyan]Name:[/cyan] {result.playlist.name}\n"
                    + f"[cyan]Tracks:[/cyan] {len(result.playlist.tracks)}\n"
                    + f"[cyan]Operations:[/cyan] {result.operations_performed} changes\n"
                    + f"[cyan]Added:[/cyan] {result.tracks_added}, [cyan]Removed:[/cyan] {result.tracks_removed}",
                    title="[bold green]🎵 Backup Complete[/bold green]",
                    border_style="green",
                )
            )
        else:
            # Created new playlist
            console.print(
                Panel.fit(
                    "[bold green]✓ Playlist Created[/bold green]\n"
                    + f"[cyan]Name:[/cyan] {result.playlist.name}\n"
                    + f"[cyan]ID:[/cyan] {result.playlist.id}\n"
                    + f"[cyan]Tracks:[/cyan] {len(result.playlist.tracks)}\n"
                    + f"[cyan]New tracks saved:[/cyan] {result.tracks_created}",
                    title="[bold green]🎵 Backup Complete[/bold green]",
                    border_style="green",
                )
            )

    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from e
    except Exception as e:
        typer.echo(f"Error: Backup failed: {e}", err=True)
        raise typer.Exit(1) from e
