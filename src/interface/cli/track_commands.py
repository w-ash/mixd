"""CLI commands for track operations including merging duplicates."""

import asyncio

from rich.table import Table
import typer

from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from src.interface.cli.console import get_console

console = get_console()

# Create track commands app
track_app = typer.Typer(
    name="tracks",
    help="Track management operations",
    no_args_is_help=True,
)


@track_app.command("merge")
def merge_tracks(
    winner_id: int = typer.Option(
        ..., "--winner-id", help="Track ID that will keep all references"
    ),
    loser_id: int = typer.Option(
        ..., "--loser-id", help="Track ID that will be merged and deleted"
    ),
    force: bool = typer.Option(False, "--force", help="Skip confirmation prompt"),
) -> None:
    """Merge two duplicate tracks by moving all references to the winner track.

    This operation:
    1. Moves all plays, likes, playlist entries, etc. from loser to winner
    2. Soft-deletes the loser track
    3. Cannot be undone easily

    Use with caution!
    """
    if winner_id == loser_id:
        console.print("❌ [red]Error: Cannot merge track with itself[/red]")
        raise typer.Exit(1)

    async def _merge_tracks_async():
        async with get_session() as session:
            uow = get_unit_of_work(session)
            async with uow:
                # Get track details for confirmation
                track_repo = uow.get_track_repository()

                try:
                    winner_track = await track_repo.get_by_id(winner_id)
                    loser_track = await track_repo.get_by_id(loser_id)
                except ValueError as e:
                    console.print(f"❌ [red]Error: {e}[/red]")
                    raise typer.Exit(1) from e

                # Show track details
                console.print("\n📊 [bold blue]Track Merge Preview[/bold blue]")

                # Create comparison table
                table = Table(title="Tracks to Merge")
                table.add_column("Role", style="bold")
                table.add_column("ID", justify="right")
                table.add_column("Title", style="cyan")
                table.add_column("Artists")
                table.add_column("Album", style="dim")

                table.add_row(
                    "🏆 Winner (keeps data)",
                    str(winner_track.id),
                    winner_track.title,
                    ", ".join(a.name for a in winner_track.artists),
                    winner_track.album or "—",
                )

                table.add_row(
                    "🗑️  Loser (will be deleted)",
                    str(loser_track.id),
                    loser_track.title,
                    ", ".join(a.name for a in loser_track.artists),
                    loser_track.album or "—",
                )

                console.print(table)

                # Show identity check
                if winner_track.has_same_identity_as(loser_track):
                    console.print(
                        "✅ [green]Tracks have matching identifiers (safe to merge)[/green]"
                    )
                else:
                    console.print(
                        "⚠️  [yellow]Warning: Tracks don't have matching identifiers[/yellow]"
                    )

                # Confirm merge unless forced
                if not force:
                    console.print("\n[yellow]This will:[/yellow]")
                    console.print(
                        "  • Move ALL plays, likes, playlist entries from loser to winner"
                    )
                    console.print("  • Soft-delete the loser track")
                    console.print("  • This operation is difficult to undo")

                    confirm = typer.confirm(
                        "\nAre you sure you want to merge these tracks?"
                    )
                    if not confirm:
                        console.print("❌ [yellow]Merge cancelled[/yellow]")
                        raise typer.Exit(0)

                # Perform merge
                console.print("\n🔄 [blue]Merging tracks...[/blue]")

                merge_service = uow.get_track_merge_service()
                result_track = await merge_service.merge_tracks(
                    winner_id, loser_id, uow
                )

                console.print(
                    f"✅ [green]Successfully merged tracks! Winner track ID: {result_track.id}[/green]"
                )

    try:
        asyncio.run(_merge_tracks_async())
    except Exception as e:
        console.print(f"❌ [red]Error during merge: {e}[/red]")
        raise typer.Exit(1) from e


@track_app.command("show")
def show_track(
    track_id: int = typer.Argument(..., help="Track ID to display"),
) -> None:
    """Show detailed information about a track."""

    async def _show_track_async():
        async with get_session() as session:
            uow = get_unit_of_work(session)
            async with uow:
                track_repo = uow.get_track_repository()

                try:
                    track = await track_repo.get_by_id(track_id)
                except ValueError as e:
                    console.print(f"❌ [red]Error: {e}[/red]")
                    raise typer.Exit(1) from e

                # Create track details table
                table = Table(title=f"Track {track_id} Details")
                table.add_column("Property", style="bold")
                table.add_column("Value")

                table.add_row("ID", str(track.id))
                table.add_row("Title", track.title)
                table.add_row("Artists", ", ".join(a.name for a in track.artists))
                table.add_row("Album", track.album or "—")
                table.add_row(
                    "Duration", f"{track.duration_ms}ms" if track.duration_ms else "—"
                )
                table.add_row("ISRC", track.isrc or "—")

                # Show connector track IDs
                if track.connector_track_identifiers:
                    connector_ids = ", ".join(
                        f"{k}: {v}"
                        for k, v in track.connector_track_identifiers.items()
                    )
                    table.add_row("Connector IDs", connector_ids)

                console.print(table)

    try:
        asyncio.run(_show_track_async())
    except Exception as e:
        console.print(f"❌ [red]Error: {e}[/red]")
        raise typer.Exit(1) from e
