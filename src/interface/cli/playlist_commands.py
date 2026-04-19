"""CLI commands for playlist CRUD, connector links, and Spotify import."""

from collections.abc import Mapping, Sequence
from typing import Annotated

from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table
import typer

from src.domain.entities.playlist import Playlist
from src.interface.cli.async_runner import run_async
from src.interface.cli.cli_helpers import (
    BatchOperationResult,
    get_cli_user_id,
    handle_cli_error,
    render_batch_summary,
    validate_sync_source,
)
from src.interface.cli.console import (
    GOLD,
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


@app.command(name="browse-spotify")
def browse_spotify(
    refresh: Annotated[
        bool,
        typer.Option("--refresh", help="Bypass the cache and force-fetch from Spotify"),
    ] = False,
    not_imported: Annotated[
        bool,
        typer.Option(
            "--not-imported", help="Show only playlists not yet imported into Mixd"
        ),
    ] = False,
    search: Annotated[
        str | None,
        typer.Option("--search", help="Filter by substring match on playlist name"),
    ] = None,
) -> None:
    """Browse your Spotify playlists with import status.

    Examples:
        mixd playlist browse-spotify
        mixd playlist browse-spotify --not-imported
        mixd playlist browse-spotify --search chill
        mixd playlist browse-spotify --refresh
    """
    from src.application.use_cases.list_spotify_playlists import (
        run_list_spotify_playlists,
    )

    try:
        result = run_async(
            run_list_spotify_playlists(user_id=get_cli_user_id(), force_refresh=refresh)
        )
    except Exception as e:
        handle_cli_error(e, "Failed to list Spotify playlists")

    views = result.playlists
    if not_imported:
        views = [v for v in views if v.import_status == "not_imported"]
    if search:
        needle = search.casefold()
        views = [v for v in views if needle in v.name.casefold()]

    if not views:
        console.print("[yellow]No matching Spotify playlists.[/yellow]")
        return

    table = Table(
        title=f"Spotify Playlists ({'cached' if result.from_cache else 'refreshed'})",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Name", style="green")
    table.add_column("Tracks", style="yellow", justify="right")
    table.add_column("Owner", style="dim")
    table.add_column("Status", style="cyan")
    table.add_column("ID", style="dim")

    for v in views:
        table.add_row(
            v.name,
            str(v.track_count),
            v.owner or "—",
            v.import_status,
            v.connector_playlist_identifier,
        )
    console.print(table)


def _resolve_spotify_playlist_refs(
    refs: Sequence[str],
    names_by_id: Mapping[str, str],
) -> tuple[list[str], list[str]]:
    """Resolve CLI refs to Spotify ``connector_playlist_identifier`` values.

    A ref is either an exact identifier (the 22-char Spotify base62 ID)
    or a case-insensitive match on the playlist name (exact first, then
    substring). Returns ``(resolved_ids, errors)`` — errors are
    human-readable strings the caller emits via the error console.
    """
    cased = [(ident, name, name.casefold()) for ident, name in names_by_id.items()]
    resolved: list[str] = []
    errors: list[str] = []

    for ref in refs:
        if ref in names_by_id:
            resolved.append(ref)
            continue

        needle = ref.casefold()
        exact = [(i, n) for i, n, c in cased if c == needle]
        matches = exact or [(i, n) for i, n, c in cased if needle in c]

        if not matches:
            errors.append(f"No Spotify playlist matching '{ref}'")
        elif len(matches) > 1:
            shown = ", ".join(f"'{n}'" for _, n in matches[:5])
            errors.append(
                f"'{ref}' matches multiple playlists ({shown}) — "
                "pass the ID to disambiguate"
            )
        else:
            resolved.append(matches[0][0])

    return resolved, errors


@app.command(name="import-spotify")
def import_spotify(
    refs: Annotated[
        list[str] | None,
        typer.Argument(help="Spotify playlist IDs or names (omit with --all)"),
    ] = None,
    source: Annotated[
        str,
        typer.Option(
            "--source",
            help="Which side is the source of truth: 'spotify' (pull) or 'mixd' (push)",
        ),
    ] = "spotify",
    all_not_imported: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Import all playlists (combine with --not-imported to skip existing)",
        ),
    ] = False,
    not_imported: Annotated[
        bool,
        typer.Option(
            "--not-imported",
            help="Restrict --all to playlists not yet imported into Mixd",
        ),
    ] = False,
) -> None:
    """Import one or more Spotify playlists into Mixd.

    Examples:
        mixd playlist import-spotify 37i9dQZF1DX0XUsuxWHRQd
        mixd playlist import-spotify "Chill Vibes"
        mixd playlist import-spotify "Chill" "Workout" --source spotify
        mixd playlist import-spotify --all --not-imported
    """
    sync_direction = validate_sync_source(source)

    if not all_not_imported and not refs:
        raise typer.BadParameter(
            "Provide one or more playlist IDs/names, or pass --all"
        )

    from src.application.use_cases.import_connector_playlist_as_canonical import (
        run_import_connector_playlists_as_canonical,
    )
    from src.application.use_cases.list_spotify_playlists import (
        run_list_spotify_playlists,
    )
    from src.domain.entities.playlist import SPOTIFY_CONNECTOR

    try:
        listing = run_async(run_list_spotify_playlists(user_id=get_cli_user_id()))
    except Exception as e:
        handle_cli_error(e, "Failed to list Spotify playlists")

    views = listing.playlists

    if all_not_imported:
        target_views = (
            [v for v in views if v.import_status == "not_imported"]
            if not_imported
            else views
        )
        resolved_ids = [v.connector_playlist_identifier for v in target_views]
        if not resolved_ids:
            console.print(
                "[yellow]No Spotify playlists matched the import criteria.[/yellow]"
            )
            return
        console.print(
            f"[cyan]Importing {len(resolved_ids)} playlists from Spotify...[/cyan]"
        )
    else:
        names_by_id = {v.connector_playlist_identifier: v.name for v in views}
        resolved_ids, errors = _resolve_spotify_playlist_refs(refs or [], names_by_id)
        for err in errors:
            err_console.print(f"[red]{err}[/red]")
        if not resolved_ids:
            raise typer.Exit(1)

    try:
        result = run_async(
            run_import_connector_playlists_as_canonical(
                user_id=get_cli_user_id(),
                connector_name=SPOTIFY_CONNECTOR,
                connector_playlist_ids=resolved_ids,
                sync_direction=sync_direction,
            )
        )
    except Exception as e:
        handle_cli_error(e, "Import failed")

    for failure in result.failed:
        err_console.print(
            f"[red]Failed:[/red] {failure.connector_playlist_identifier} — "
            f"{failure.message}"
        )

    summary = BatchOperationResult(
        succeeded=len(result.succeeded),
        skipped=len(result.skipped_unchanged),
        failed=[
            f"{f.connector_playlist_identifier}: {f.message}" for f in result.failed
        ],
    )
    console.print(render_batch_summary(summary, title="Spotify Import"))


# ---------------------------------------------------------------------------
# Metadata mapping commands (v0.7.4 epic 4)
# ---------------------------------------------------------------------------


@app.command(name="map")
def map_playlist(
    playlist_ref: Annotated[
        str, typer.Argument(help="Spotify playlist ID or name fragment")
    ],
    action: Annotated[
        str,
        typer.Option(
            "--action", "-a", help="set_preference or add_tag", show_default=False
        ),
    ],
    value: Annotated[
        str,
        typer.Option(
            "--value", "-v", help="hmm/nah/yah/star (preference) or tag string"
        ),
    ],
) -> None:
    """Map a Spotify playlist to a preference state or tag.

    The mapping is applied to all the playlist's tracks on the next
    ``mixd playlist import-metadata`` run.

    Examples:
        mixd playlist map "Chill Vibes" --action add_tag --value "mood:chill"
        mixd playlist map "Starred" --action set_preference --value star
    """
    from src.interface.cli.cli_helpers import (
        validate_mapping_action,
        validate_mapping_action_value,
    )

    action_type = validate_mapping_action(action)
    canonical_value = validate_mapping_action_value(value, action_type=action_type)

    cp_id = _resolve_connector_playlist_id(playlist_ref)

    from src.application.use_cases.create_playlist_metadata_mapping import (
        run_create_playlist_metadata_mapping,
    )

    try:
        result = run_async(
            run_create_playlist_metadata_mapping(
                user_id=get_cli_user_id(),
                connector_playlist_id=cp_id,
                action_type=action_type,
                raw_action_value=canonical_value,
            )
        )
    except Exception as e:
        handle_cli_error(e, "Failed to create mapping")

    if result.created:
        console.print(
            f"[green]Mapped[/green] {playlist_ref} → {action_type}={canonical_value} "
            f"(mapping id: {result.mapping.id})"
        )
    else:
        console.print(
            f"[dim]Mapping already exists for {action_type}={canonical_value}[/dim]"
        )


@app.command(name="unmap")
def unmap_playlist(
    playlist_ref: Annotated[
        str, typer.Argument(help="Spotify playlist ID or name fragment")
    ],
    action: Annotated[
        str, typer.Option("--action", "-a", help="set_preference or add_tag")
    ],
    value: Annotated[
        str, typer.Option("--value", "-v", help="The action value to remove")
    ],
) -> None:
    """Remove a mapping from a Spotify playlist.

    Does NOT clear preferences/tags already written by past imports.
    Re-run ``import-metadata`` to clear mapping-sourced metadata for
    tracks no longer covered by any mapping.

    Examples:
        mixd playlist unmap "Chill Vibes" --action add_tag --value "mood:chill"
    """
    from src.interface.cli.cli_helpers import (
        validate_mapping_action,
        validate_mapping_action_value,
    )

    action_type = validate_mapping_action(action)
    canonical_value = validate_mapping_action_value(value, action_type=action_type)

    cp_id = _resolve_connector_playlist_id(playlist_ref)

    from src.application.runner import execute_use_case
    from src.application.use_cases.delete_playlist_metadata_mapping import (
        DeletePlaylistMetadataMappingCommand,
        DeletePlaylistMetadataMappingUseCase,
    )

    user_id = get_cli_user_id()

    async def _find_and_delete() -> tuple[bool, str | None]:
        from src.domain.repositories import UnitOfWorkProtocol

        async def _inner(uow: UnitOfWorkProtocol):
            async with uow:
                repo = uow.get_playlist_metadata_mapping_repository()
                mappings = await repo.list_for_connector_playlist(
                    cp_id, user_id=user_id
                )
                match = next(
                    (
                        m
                        for m in mappings
                        if m.action_type == action_type
                        and m.action_value == canonical_value
                    ),
                    None,
                )
                if match is None:
                    return False, None
                cmd = DeletePlaylistMetadataMappingCommand(
                    user_id=user_id, mapping_id=match.id
                )
                deleted_result = await DeletePlaylistMetadataMappingUseCase().execute(
                    cmd, uow
                )
                return deleted_result.deleted, str(match.id)

        return await execute_use_case(_inner, user_id=user_id)

    try:
        deleted, mapping_id = run_async(_find_and_delete())
    except Exception as e:
        handle_cli_error(e, "Failed to remove mapping")

    if deleted:
        console.print(
            f"[green]Removed mapping[/green] {action_type}={canonical_value} "
            f"({mapping_id})"
        )
    else:
        console.print(
            f"[yellow]No mapping found[/yellow] for {action_type}={canonical_value}"
        )


@app.command(name="import-metadata")
def import_metadata() -> None:
    """Apply all configured playlist metadata mappings.

    For each mapping, walks the cached connector playlist's tracks and
    applies the action (set_preference / add_tag). Manual preferences
    are never overwritten. Tracks removed from a playlist since the
    last run have their mapping-sourced metadata cleared (manual stays).

    Re-running on unchanged playlists is idempotent — re-applies use
    ON CONFLICT DO NOTHING so duplicates are silent.
    """
    from src.application.use_cases.import_playlist_metadata import (
        run_import_playlist_metadata,
    )

    try:
        result = run_async(run_import_playlist_metadata(user_id=get_cli_user_id()))
    except Exception as e:
        handle_cli_error(e, "Metadata import failed")

    if result.mappings_processed == 0:
        console.print(
            "[yellow]No active mappings to import. "
            "Use `mixd playlist map` to create one.[/yellow]"
        )
        return

    table = Table(
        title="Playlist Metadata Import", show_header=False, header_style="bold"
    )
    table.add_column("", style="bold")
    table.add_column("", justify="right")
    table.add_row("Mappings processed", str(result.mappings_processed))
    table.add_row("Preferences applied", str(result.preferences_applied))
    table.add_row("Preferences cleared", str(result.preferences_cleared))
    table.add_row("Tags applied", str(result.tags_applied))
    table.add_row("Tags cleared", str(result.tags_cleared))
    if result.conflicts_logged:
        table.add_row(
            "Conflicts (logged)", str(result.conflicts_logged), style="yellow"
        )
    console.print(table)


def _resolve_connector_playlist_id(ref: str):
    """Resolve a CLI playlist ref to a DBConnectorPlaylist UUID.

    Accepts the Spotify base62 identifier OR a substring of the
    playlist name. Uses the cached browser listing — does not hit
    Spotify. Raises ``typer.BadParameter`` on missing or ambiguous.
    """
    from uuid import UUID

    from src.application.use_cases.list_spotify_playlists import (
        run_list_spotify_playlists,
    )

    try:
        listing = run_async(run_list_spotify_playlists(user_id=get_cli_user_id()))
    except Exception as e:
        handle_cli_error(e, "Failed to list cached Spotify playlists")

    views = listing.playlists
    names_by_id = {v.connector_playlist_identifier: v.name for v in views}
    resolved_ids, errors = _resolve_spotify_playlist_refs([ref], names_by_id)
    if errors:
        raise typer.BadParameter(errors[0])

    spotify_id = resolved_ids[0]
    # Look up the DBConnectorPlaylist UUID for this connector_playlist_identifier.

    async def _by_identifier() -> UUID:
        from src.application.runner import execute_use_case
        from src.domain.entities.playlist import SPOTIFY_CONNECTOR
        from src.domain.repositories import UnitOfWorkProtocol

        async def _inner(uow: UnitOfWorkProtocol):
            async with uow:
                cp_repo = uow.get_connector_playlist_repository()
                cps = await cp_repo.list_by_connector(SPOTIFY_CONNECTOR)
                for cp in cps:
                    if cp.connector_playlist_identifier == spotify_id:
                        return cp.id
                raise typer.BadParameter(
                    f"Spotify playlist {spotify_id!r} is not in the local cache. "
                    "Run `mixd playlist browse-spotify --refresh` first."
                )

        return await execute_use_case(_inner, user_id=get_cli_user_id())

    return run_async(_by_identifier())


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
