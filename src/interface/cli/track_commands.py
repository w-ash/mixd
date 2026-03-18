"""CLI commands for track operations: show, merge, list, playlists."""

from typing import Annotated

from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table
import typer

from src.domain.exceptions import NotFoundError
from src.interface.cli.async_runner import run_async
from src.interface.cli.cli_helpers import handle_cli_error
from src.interface.cli.console import GOLD, get_console, print_brand_title

console = get_console()

# Create track commands app
track_app = typer.Typer(
    name="tracks",
    help="Track management operations",
    no_args_is_help=True,
)


@track_app.command("merge")
def merge_tracks(
    winner_id: Annotated[
        int, typer.Option("--winner-id", help="Track ID that will keep all references")
    ],
    loser_id: Annotated[
        int, typer.Option("--loser-id", help="Track ID that will be merged and deleted")
    ],
    force: Annotated[
        bool, typer.Option("--force", help="Skip confirmation prompt")
    ] = False,
) -> None:
    """Merge two duplicate tracks by moving all references to the winner track.

    This operation:
    1. Moves all plays, likes, playlist entries, etc. from loser to winner
    2. Soft-deletes the loser track
    3. Cannot be undone easily

    Use with caution!
    """
    if winner_id == loser_id:
        console.print("[red]Error: Cannot merge track with itself[/red]")
        raise typer.Exit(1)

    async def _merge_tracks_async():
        from src.application.runner import execute_use_case
        from src.application.use_cases.get_track_details import (
            GetTrackDetailsCommand,
            GetTrackDetailsUseCase,
        )
        from src.application.use_cases.merge_tracks import (
            MergeTracksCommand,
            MergeTracksUseCase,
        )

        # Step 1: Fetch both tracks for confirmation display
        try:
            winner_result = await execute_use_case(
                lambda uow: GetTrackDetailsUseCase().execute(
                    GetTrackDetailsCommand(track_id=winner_id), uow
                )
            )
            loser_result = await execute_use_case(
                lambda uow: GetTrackDetailsUseCase().execute(
                    GetTrackDetailsCommand(track_id=loser_id), uow
                )
            )
        except NotFoundError as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1) from e

        winner_track = winner_result.track
        loser_track = loser_result.track

        # Step 2: Show track details
        print_brand_title("Track Merge Preview")

        table = Table(title="Tracks to Merge")
        table.add_column("Role", style="bold")
        table.add_column("ID", justify="right")
        table.add_column("Title", style="cyan")
        table.add_column("Artists")
        table.add_column("Album", style="dim")

        table.add_row(
            "Winner (keeps data)",
            str(winner_track.id),
            winner_track.title,
            winner_track.artists_display,
            winner_track.album or "—",
        )

        table.add_row(
            "Loser (will be deleted)",
            str(loser_track.id),
            loser_track.title,
            loser_track.artists_display,
            loser_track.album or "—",
        )

        console.print(table)

        if winner_track.has_same_identity_as(loser_track):
            console.print(
                "[green]Tracks have matching identifiers (safe to merge)[/green]"
            )
        else:
            console.print(
                "[yellow]Warning: Tracks don't have matching identifiers[/yellow]"
            )

        # Step 3: Confirm merge unless forced
        if not force:
            console.print("\n[yellow]This will:[/yellow]")
            console.print(
                "  - Move ALL plays, likes, playlist entries from loser to winner"
            )
            console.print("  - Soft-delete the loser track")
            console.print("  - This operation is difficult to undo")

            if not Confirm.ask(
                "\n[bold]Are you sure you want to merge these tracks?[/bold]"
            ):
                console.print("[yellow]Merge cancelled[/yellow]")
                raise typer.Exit(0)

        # Step 4: Perform merge via use case
        console.print(f"\n[{GOLD}]Merging tracks...[/]")

        result = await execute_use_case(
            lambda uow: MergeTracksUseCase().execute(
                MergeTracksCommand(winner_id=winner_id, loser_id=loser_id), uow
            )
        )

        console.print(
            f"[green]Successfully merged tracks! Winner track ID: {result.merged_track.id}[/green]"
        )

    try:
        run_async(_merge_tracks_async())
    except Exception as e:
        handle_cli_error(e, "Error during merge")


@track_app.command("show")
def show_track(
    track_id: Annotated[int, typer.Argument(help="Track ID to display")],
) -> None:
    """Show detailed information about a track including likes, plays, and playlists."""

    async def _show_track_async():
        from src.application.runner import execute_use_case
        from src.application.use_cases.get_track_details import (
            GetTrackDetailsCommand,
            GetTrackDetailsUseCase,
        )

        try:
            result = await execute_use_case(
                lambda uow: GetTrackDetailsUseCase().execute(
                    GetTrackDetailsCommand(track_id=track_id), uow
                )
            )
        except NotFoundError as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1) from e

        track = result.track

        # Basic details table
        table = Table(title=f"Track {track_id} Details")
        table.add_column("Property", style="bold")
        table.add_column("Value")

        table.add_row("ID", str(track.id))
        table.add_row("Title", track.title)
        table.add_row("Artists", track.artists_display)
        table.add_row("Album", track.album or "—")
        table.add_row(
            "Duration", f"{track.duration_ms}ms" if track.duration_ms else "—"
        )
        table.add_row("ISRC", track.isrc or "—")

        console.print(table)

        # Connector mappings
        if result.connector_mappings:
            mapping_table = Table(title="Connector Mappings")
            mapping_table.add_column("Connector", style="cyan")
            mapping_table.add_column("Track ID")
            for m in result.connector_mappings:
                mapping_table.add_row(m.connector_name, m.connector_track_id)
            console.print(mapping_table)

        # Like status
        if result.like_status:
            like_table = Table(title="Like Status")
            like_table.add_column("Service", style="cyan")
            like_table.add_column("Liked", style="green")
            like_table.add_column("Liked At", style="dim")
            for service, info in result.like_status.items():
                like_table.add_row(
                    service,
                    "Yes" if info.is_liked else "No",
                    str(info.liked_at) if info.liked_at else "—",
                )
            console.print(like_table)

        # Play summary
        play = result.play_summary
        if play.total_plays > 0:
            console.print(
                Panel(
                    f"[cyan]Total Plays:[/cyan] {play.total_plays}\n"
                    f"[cyan]First Played:[/cyan] {play.first_played or '—'}\n"
                    f"[cyan]Last Played:[/cyan] {play.last_played or '—'}",
                    title="Play Summary",
                )
            )

        # Playlist memberships
        if result.playlists:
            pl_table = Table(title="Playlist Memberships")
            pl_table.add_column("ID", justify="right", style="cyan")
            pl_table.add_column("Name", style="green")
            for p in result.playlists:
                pl_table.add_row(str(p.id), p.name)
            console.print(pl_table)

    try:
        run_async(_show_track_async())
    except Exception as e:
        handle_cli_error(e, "Failed to show track")


@track_app.command("list")
def list_tracks(
    query: Annotated[
        str | None,
        typer.Option("--query", "-q", help="Search tracks by title/artist"),
    ] = None,
    liked: Annotated[
        bool | None,
        typer.Option("--liked/--not-liked", help="Filter by liked status"),
    ] = None,
    connector: Annotated[
        str | None,
        typer.Option("--connector", "-c", help="Filter by connector (e.g., 'spotify')"),
    ] = None,
    sort: Annotated[
        str,
        typer.Option("--sort", "-s", help="Sort order (title_asc, title_desc, recent)"),
    ] = "title_asc",
    limit: Annotated[
        int,
        typer.Option("--limit", "-l", help="Number of tracks to show"),
    ] = 50,
    offset: Annotated[
        int,
        typer.Option("--offset", "-o", help="Skip this many tracks"),
    ] = 0,
    output_format: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: table or json"),
    ] = "table",
) -> None:
    """List tracks in your library with optional search and filtering."""

    async def _list_tracks_async():
        import json as json_mod

        from src.application.runner import execute_use_case
        from src.application.use_cases.list_tracks import (
            ListTracksCommand,
            ListTracksUseCase,
        )

        result = await execute_use_case(
            lambda uow: ListTracksUseCase().execute(
                ListTracksCommand(
                    query=query,
                    liked=liked,
                    connector=connector,
                    sort_by=sort,  # type: ignore[arg-type]  # validated by Typer choices
                    limit=limit,
                    offset=offset,
                ),
                uow,
            )
        )

        if not result.tracks:
            console.print("[yellow]No tracks found.[/yellow]")
            return

        if output_format == "json":
            data = [
                {
                    "id": t.id,
                    "title": t.title,
                    "artists": [a.name for a in t.artists],
                    "album": t.album,
                    "liked": t.id in result.liked_track_ids if t.id else False,
                }
                for t in result.tracks
            ]
            console.print(json_mod.dumps(data, indent=2))
            return

        table = Table(
            title=f"Tracks ({result.offset + 1}-{result.offset + len(result.tracks)} of {result.total})"
        )
        table.add_column("ID", justify="right", style="cyan")
        table.add_column("Title", style="green")
        table.add_column("Artists")
        table.add_column("Album", style="dim")
        table.add_column("Liked", justify="center")

        for t in result.tracks:
            is_liked = t.id in result.liked_track_ids if t.id else False
            table.add_row(
                str(t.id),
                t.title,
                t.artists_display,
                t.album or "—",
                "[green]Yes[/green]" if is_liked else "—",
            )

        console.print(table)

    try:
        run_async(_list_tracks_async())
    except Exception as e:
        handle_cli_error(e, "Failed to list tracks")


@track_app.command("playlists")
def track_playlists(
    track_id: Annotated[int, typer.Argument(help="Track ID to look up")],
) -> None:
    """Show which playlists contain a given track."""

    async def _track_playlists_async():
        from src.application.runner import execute_use_case
        from src.application.use_cases.get_track_playlists import (
            GetTrackPlaylistsCommand,
            GetTrackPlaylistsUseCase,
        )

        try:
            result = await execute_use_case(
                lambda uow: GetTrackPlaylistsUseCase().execute(
                    GetTrackPlaylistsCommand(track_id=track_id), uow
                )
            )
        except NotFoundError as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1) from e

        if not result.playlists:
            console.print(f"[yellow]Track {track_id} is not in any playlists.[/yellow]")
            return

        table = Table(title=f"Playlists containing track {track_id}")
        table.add_column("ID", justify="right", style="cyan")
        table.add_column("Name", style="green")
        table.add_column("Description", style="dim")
        table.add_column("Tracks", justify="right")

        for p in result.playlists:
            table.add_row(
                str(p.id),
                p.name,
                (p.description or "—")[:50],
                str(len(p.tracks)),
            )

        console.print(table)

    try:
        run_async(_track_playlists_async())
    except Exception as e:
        handle_cli_error(e, "Failed to get track playlists")
