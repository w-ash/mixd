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
from src.interface.cli.cli_helpers import get_cli_user_id, handle_cli_error
from src.interface.cli.console import (
    GOLD,
    brand_panel,
    brand_status,
    get_console,
    get_error_console,
)

console = get_console()
err_console = get_error_console()

# Create playlist data management app
app = typer.Typer(
    help="Manage stored playlists and data operations",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@app.command(name="list")
def list_playlists() -> None:
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
        mixd playlist backup spotify 37i9dQZF1DX0XUsuxWHRQd
        mixd playlist backup spotify 1A2B3C4D5E6F7G8H9I0J1K
    """
    run_async(_backup_playlist_async(connector, playlist_id))


@app.command()
def create(
    name: Annotated[str, typer.Option("--name", "-n", help="Playlist name")],
    description: Annotated[
        str | None,
        typer.Option("--description", "-d", help="Playlist description"),
    ] = None,
) -> None:
    """Create a new empty playlist in your local database.

    Examples:
        mixd playlist create --name "My Playlist"
        mixd playlist create --name "Favorites" --description "Best tracks"
    """
    run_async(_create_playlist_async(name, description))


@app.command()
def update(
    playlist_id: Annotated[str, typer.Argument(help="Playlist UUID to update")],
    name: Annotated[
        str | None,
        typer.Option("--name", "-n", help="New playlist name"),
    ] = None,
    description: Annotated[
        str | None,
        typer.Option("--description", "-d", help="New playlist description"),
    ] = None,
) -> None:
    """Update a playlist's name and/or description.

    Examples:
        mixd playlist update 5 --name "New Name"
        mixd playlist update 3 --description "Updated description"
    """
    if name is None and description is None:
        console.print(
            "[yellow]Nothing to update. Provide --name and/or --description.[/yellow]"
        )
        raise typer.Exit(0)
    run_async(_update_playlist_async(playlist_id, name, description))


@app.command()
def delete(
    playlist_id: Annotated[str, typer.Argument(help="Playlist UUID to delete")],
    force: Annotated[bool, typer.Option("--force", "-f")] = False,
) -> None:
    """Delete a playlist from your local database.

    Removes the playlist and all associated data permanently. This action
    cannot be undone. Use --force to skip confirmation prompt.

    Examples:
        mixd playlist delete 5
        mixd playlist delete 3 --force
    """
    run_async(_delete_playlist_async(playlist_id, force))


async def _list_stored_playlists() -> None:
    """List all stored playlists with metadata following DDD principles."""
    try:
        from src.application.runner import execute_use_case
        from src.application.use_cases.list_playlists import (
            ListPlaylistsCommand,
            ListPlaylistsUseCase,
        )

        user_id = get_cli_user_id()
        result = await execute_use_case(
            lambda uow: ListPlaylistsUseCase().execute(
                ListPlaylistsCommand(user_id=user_id), uow
            ),
            user_id=user_id,
        )

        if not result.has_playlists:
            console.print("[yellow]No playlists found in your database.[/yellow]")
            return

        # Display Rich table
        _display_playlists_table(result.playlists)

    except Exception as e:
        handle_cli_error(e, "Failed to list playlists")


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
    table.add_column("Last Updated", style=GOLD, no_wrap=True)

    for playlist in playlists:
        # Note: Domain entities don't expose timestamp fields by design
        # For CLI listing, we show track count as primary metadata
        updated_str = "N/A"

        # Truncate description if too long (using centralized settings)
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


async def _delete_playlist_async(playlist_id: str, force: bool) -> None:
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

        user_id = get_cli_user_id()

        # Step 1: Fetch playlist info for confirmation prompt
        try:
            read_result = await execute_use_case(
                lambda uow: ReadCanonicalPlaylistUseCase().execute(
                    ReadCanonicalPlaylistCommand(
                        user_id=user_id,
                        playlist_id=str(playlist_id),
                    ),
                    uow,
                ),
                user_id=user_id,
            )
            playlist = read_result.playlist
        except Exception as e:
            err_console.print(
                f"[red]Error: Playlist with ID {playlist_id} not found.[/red]"
            )
            raise typer.Exit(1) from e

        if not playlist:
            err_console.print(
                f"[red]Error: Playlist with ID {playlist_id} not found.[/red]"
            )
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
                    user_id=user_id,
                    playlist_id=str(playlist_id),
                    force_delete=force,
                ),
                uow,
            ),
            user_id=user_id,
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
        handle_cli_error(e, "Failed to delete playlist")


async def _backup_playlist_async(connector_name: str, playlist_id: str) -> None:
    """Backup a playlist from a connector service to the local database."""
    # Import here to avoid circular dependencies
    from src.application.services.playlist_backup_service import run_playlist_backup

    console.print(
        brand_panel(
            f"[bold]{connector_name.title()} Playlist Backup[/bold]\n"
            + f"[dim]Playlist ID: {playlist_id}[/dim]",
            "Starting Backup",
            emoji="🎵",
        )
    )

    try:
        with brand_status(f"Backing up playlist from {connector_name}..."):
            result = await run_playlist_backup(
                connector_name=connector_name,
                playlist_id=playlist_id,
                user_id=get_cli_user_id(),
            )

        from src.application.use_cases.update_canonical_playlist import (
            UpdateCanonicalPlaylistResult,
        )

        # Both result types share .playlist — isinstance narrows for type-safe access
        playlist = result.playlist
        detail_lines = [
            f"[cyan]Name:[/cyan] {playlist.name}",
            f"[cyan]Tracks:[/cyan] {len(playlist.tracks)}",
        ]

        if isinstance(result, UpdateCanonicalPlaylistResult):
            detail_lines.extend([
                f"[cyan]Operations:[/cyan] {result.operations_performed} changes",
                f"[cyan]Added:[/cyan] {result.tracks_added}, [cyan]Removed:[/cyan] {result.tracks_removed}",
            ])
            action = "Updated"
        else:
            detail_lines.append(
                f"[cyan]New tracks saved:[/cyan] {result.tracks_created}"
            )
            action = "Created"

        console.print(
            Panel.fit(
                f"[bold green]✓ Playlist {action}[/bold green]\n"
                + "\n".join(detail_lines),
                title="[bold green]🎵 Backup Complete[/bold green]",
                border_style="green",
            )
        )

    except ValueError as e:
        handle_cli_error(e, str(e))
    except Exception as e:
        handle_cli_error(e, "Backup failed")


async def _create_playlist_async(name: str, description: str | None) -> None:
    """Create a new empty playlist via use case."""
    try:
        from src.application.runner import execute_use_case
        from src.application.use_cases.create_canonical_playlist import (
            CreateCanonicalPlaylistCommand,
            CreateCanonicalPlaylistUseCase,
        )
        from src.infrastructure.connectors._shared.metric_registry import (
            MetricConfigProviderImpl,
        )

        user_id = get_cli_user_id()
        command = CreateCanonicalPlaylistCommand(
            user_id=user_id, name=name, description=description
        )
        result = await execute_use_case(
            lambda uow: CreateCanonicalPlaylistUseCase(
                metric_config=MetricConfigProviderImpl()
            ).execute(command, uow),
            user_id=user_id,
        )

        console.print(
            Panel.fit(
                f"[bold green]Playlist Created[/bold green]\n"
                f"[cyan]ID:[/cyan] {result.playlist.id}\n"
                f"[cyan]Name:[/cyan] {result.playlist.name}",
                title="[bold green]Playlist Created[/bold green]",
                border_style="green",
            )
        )

    except Exception as e:
        handle_cli_error(e, "Failed to create playlist")


async def _update_playlist_async(
    playlist_id: str, name: str | None, description: str | None
) -> None:
    """Update playlist metadata via use case."""
    try:
        from src.application.runner import execute_use_case
        from src.application.use_cases.update_canonical_playlist import (
            UpdateCanonicalPlaylistCommand,
            UpdateCanonicalPlaylistUseCase,
        )
        from src.domain.entities.track import TrackList
        from src.infrastructure.connectors._shared.metric_registry import (
            MetricConfigProviderImpl,
        )

        user_id = get_cli_user_id()
        command = UpdateCanonicalPlaylistCommand(
            user_id=user_id,
            playlist_id=str(playlist_id),
            new_tracklist=TrackList(),
            playlist_name=name,
            playlist_description=description,
        )
        result = await execute_use_case(
            lambda uow: UpdateCanonicalPlaylistUseCase(
                metric_config=MetricConfigProviderImpl()
            ).execute(command, uow),
            user_id=user_id,
        )

        console.print(
            Panel.fit(
                f"[bold green]Playlist Updated[/bold green]\n"
                f"[cyan]ID:[/cyan] {result.playlist.id}\n"
                f"[cyan]Name:[/cyan] {result.playlist.name}",
                title="[bold green]Playlist Updated[/bold green]",
                border_style="green",
            )
        )

    except Exception as e:
        handle_cli_error(e, "Failed to update playlist")


# ---------------------------------------------------------------------------
# Playlist Link Commands
# ---------------------------------------------------------------------------


def _parse_sync_direction(value: str | None):
    """Parse optional sync direction string to SyncDirection enum."""
    if not value:
        return None
    from src.domain.entities.playlist_link import SyncDirection

    return SyncDirection(value)


@app.command(name="links")
def list_links(
    playlist_id: Annotated[str, typer.Argument(help="Playlist UUID")],
) -> None:
    """List connector links for a playlist."""
    from uuid import UUID

    async def _list():
        from src.application.runner import execute_use_case
        from src.application.use_cases.list_playlist_links import (
            ListPlaylistLinksCommand,
            ListPlaylistLinksUseCase,
        )

        user_id = get_cli_user_id()
        return await execute_use_case(
            lambda uow: ListPlaylistLinksUseCase().execute(
                ListPlaylistLinksCommand(
                    user_id=user_id, playlist_id=UUID(playlist_id)
                ),
                uow,
            ),
            user_id=user_id,
        )

    try:
        result = run_async(_list())
    except Exception as e:
        handle_cli_error(e, "Failed to list links")

    if not result.links:
        console.print("[yellow]No connector links for this playlist.[/yellow]")
        return

    table = Table(title="Playlist Links", show_header=True, header_style="bold magenta")
    table.add_column("Link ID", style="cyan", no_wrap=True)
    table.add_column("Connector")
    table.add_column("External ID", style="dim")
    table.add_column("Direction")
    table.add_column("Status")
    table.add_column("Last Sync", style="dim")

    for link in result.links:
        table.add_row(
            str(link.id),
            link.connector_name,
            link.connector_playlist_identifier,
            str(link.sync_direction.value),
            str(link.sync_status.value),
            str(link.last_synced) if link.last_synced else "Never",
        )

    console.print(table)


@app.command(name="link")
def create_link(
    playlist_id: Annotated[str, typer.Argument(help="Playlist UUID")],
    connector: Annotated[
        str, typer.Option("--connector", "-c", help="Connector name (e.g., 'spotify')")
    ],
    external_id: Annotated[
        str, typer.Option("--playlist-id", help="External playlist ID")
    ],
    direction: Annotated[
        str, typer.Option("--direction", "-d", help="push or pull")
    ] = "push",
) -> None:
    """Link a playlist to an external connector playlist."""
    from uuid import UUID

    from src.domain.entities.playlist_link import SyncDirection

    sync_dir = SyncDirection(direction)

    async def _create():
        from src.application.runner import execute_use_case
        from src.application.use_cases.create_playlist_link import (
            CreatePlaylistLinkCommand,
            CreatePlaylistLinkUseCase,
        )

        user_id = get_cli_user_id()
        return await execute_use_case(
            lambda uow: CreatePlaylistLinkUseCase().execute(
                CreatePlaylistLinkCommand(
                    user_id=user_id,
                    playlist_id=UUID(playlist_id),
                    connector=connector,
                    connector_playlist_id=external_id,
                    sync_direction=sync_dir,
                ),
                uow,
            ),
            user_id=user_id,
        )

    try:
        result = run_async(_create())
    except Exception as e:
        handle_cli_error(e, "Failed to create link")

    console.print(
        f"[green]Linked[/green] playlist {playlist_id} to {connector}:{external_id} "
        f"(direction: {direction}, link ID: {result.link.id})"
    )


@app.command(name="unlink")
def delete_link(
    link_id: Annotated[str, typer.Argument(help="Link UUID to remove")],
) -> None:
    """Remove a connector link from a playlist."""
    from uuid import UUID

    async def _delete():
        from src.application.runner import execute_use_case
        from src.application.use_cases.delete_playlist_link import (
            DeletePlaylistLinkCommand,
            DeletePlaylistLinkUseCase,
        )

        user_id = get_cli_user_id()
        return await execute_use_case(
            lambda uow: DeletePlaylistLinkUseCase().execute(
                DeletePlaylistLinkCommand(user_id=user_id, link_id=UUID(link_id)),
                uow,
            ),
            user_id=user_id,
        )

    try:
        run_async(_delete())
    except Exception as e:
        handle_cli_error(e, "Failed to delete link")

    console.print(f"[green]Removed link {link_id}[/green]")


@app.command(name="sync")
def sync_link(
    link_id: Annotated[str, typer.Argument(help="Link UUID to sync")],
    direction_override: Annotated[
        str | None,
        typer.Option(
            "--direction-override", help="Override sync direction (push or pull)"
        ),
    ] = None,
    confirm: Annotated[
        bool, typer.Option("--confirm", help="Skip confirmation")
    ] = False,
) -> None:
    """Sync a linked playlist with its external connector."""
    from uuid import UUID

    dir_override = _parse_sync_direction(direction_override)

    async def _sync():
        from src.application.runner import execute_use_case
        from src.application.use_cases.sync_playlist_link import (
            SyncPlaylistLinkCommand,
            SyncPlaylistLinkUseCase,
        )

        user_id = get_cli_user_id()
        return await execute_use_case(
            lambda uow: SyncPlaylistLinkUseCase().execute(
                SyncPlaylistLinkCommand(
                    user_id=user_id,
                    link_id=UUID(link_id),
                    direction_override=dir_override,
                    confirmed=confirm,
                ),
                uow,
            ),
            user_id=user_id,
        )

    try:
        result = run_async(_sync())
    except Exception as e:
        handle_cli_error(e, "Sync failed")

    console.print(
        f"[green]Sync complete[/green] — "
        f"added: {result.tracks_added}, removed: {result.tracks_removed}"
    )


@app.command(name="sync-preview")
def sync_preview(
    link_id: Annotated[str, typer.Argument(help="Link UUID to preview")],
    direction_override: Annotated[
        str | None,
        typer.Option(
            "--direction-override", help="Override sync direction (push or pull)"
        ),
    ] = None,
) -> None:
    """Preview what a sync would do without making changes."""
    from uuid import UUID

    dir_override = _parse_sync_direction(direction_override)

    async def _preview():
        from src.application.runner import execute_use_case
        from src.application.use_cases.preview_playlist_sync import (
            PreviewPlaylistSyncCommand,
            PreviewPlaylistSyncUseCase,
        )

        user_id = get_cli_user_id()
        return await execute_use_case(
            lambda uow: PreviewPlaylistSyncUseCase().execute(
                PreviewPlaylistSyncCommand(
                    user_id=user_id,
                    link_id=UUID(link_id),
                    direction_override=dir_override,
                ),
                uow,
            ),
            user_id=user_id,
        )

    try:
        result = run_async(_preview())
    except Exception as e:
        handle_cli_error(e, "Preview failed")

    console.print(
        Panel.fit(
            f"[cyan]Direction:[/cyan] {result.direction.value}\n"
            f"[cyan]To add:[/cyan] {result.tracks_to_add}\n"
            f"[cyan]To remove:[/cyan] {result.tracks_to_remove}\n"
            f"[cyan]Unchanged:[/cyan] {result.tracks_unchanged}",
            title="[bold]Sync Preview[/bold]",
        )
    )
