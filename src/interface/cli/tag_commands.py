"""CLI commands for tagging tracks and browsing the tag vocabulary."""

from typing import Annotated

from rich.table import Table
import typer

from src.interface.cli.async_runner import run_async
from src.interface.cli.cli_helpers import (
    BatchOperationResult,
    get_cli_user_id,
    render_batch_summary,
    render_tracks_table,
    resolve_playlist_ref,
    resolve_track_ref,
    validate_tag,
)
from src.interface.cli.console import brand_status, get_console

console = get_console()

app = typer.Typer(
    help="Tag tracks and browse your tag vocabulary",
    rich_help_panel="🎵 Track Operations",
)


@app.command(name="add")
def add_tag(
    track_ref: Annotated[
        str, typer.Argument(help="Track UUID or search string", metavar="TRACK")
    ],
    tag: Annotated[
        str, typer.Argument(help="Tag to add (e.g. 'mood:chill')", metavar="TAG")
    ],
) -> None:
    """Add a tag to a track."""
    from src.application.use_cases.tag_track import run_tag_track

    validated_tag = validate_tag(tag)
    user_id = get_cli_user_id()
    track = resolve_track_ref(track_ref, user_id=user_id)

    with brand_status("Adding tag..."):
        result = run_async(
            run_tag_track(
                user_id=user_id,
                track_id=track.id,
                raw_tag=validated_tag,
            )
        )

    if result.changed:
        console.print(
            f"[green]Added tag [bold]{result.tag}[/bold] to {track.title}[/green]"
        )
    else:
        console.print(
            f"[dim]Tag '{result.tag}' already on {track.title} — no change[/dim]"
        )


@app.command(name="remove")
def remove_tag(
    track_ref: Annotated[
        str, typer.Argument(help="Track UUID or search string", metavar="TRACK")
    ],
    tag: Annotated[str, typer.Argument(help="Tag to remove", metavar="TAG")],
) -> None:
    """Remove a tag from a track."""
    from src.application.use_cases.untag_track import run_untag_track

    validated_tag = validate_tag(tag)
    user_id = get_cli_user_id()
    track = resolve_track_ref(track_ref, user_id=user_id)

    with brand_status("Removing tag..."):
        result = run_async(
            run_untag_track(
                user_id=user_id,
                track_id=track.id,
                raw_tag=validated_tag,
            )
        )

    if result.changed:
        console.print(
            f"[green]Removed tag [bold]{result.tag}[/bold] from {track.title}[/green]"
        )
    else:
        console.print(f"[dim]Tag '{result.tag}' not on {track.title} — no change[/dim]")


@app.command(name="list")
def list_tags(
    query: Annotated[
        str | None,
        typer.Option("--query", "-q", help="Substring filter (trigram-indexed)"),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-l", help="Max results")] = 100,
) -> None:
    """List tags with usage counts, sorted by count desc."""
    from src.application.use_cases.list_tags import run_list_tags

    user_id = get_cli_user_id()
    with brand_status("Listing tags..."):
        result = run_async(run_list_tags(user_id=user_id, query=query, limit=limit))

    if not result.tags:
        msg = (
            f"[dim]No tags matching '{query}'[/dim]"
            if query
            else "[dim]No tags yet[/dim]"
        )
        console.print(msg)
        return

    table = Table(title="Tags")
    table.add_column("Tag", style="cyan")
    table.add_column("Count", justify="right", style="dim")
    table.add_column("Last used", style="dim")
    for tag, count, last_used_at in result.tags:
        table.add_row(tag, str(count), last_used_at.strftime("%Y-%m-%d"))
    console.print(table)
    console.print(f"[dim]{len(result.tags)} tag(s)[/dim]")


@app.command(name="tracks")
def tracks_for_tag(
    tag: Annotated[
        str,
        typer.Argument(help="Tag to filter on (e.g. 'mood:chill')", metavar="TAG"),
    ],
    limit: Annotated[int, typer.Option("--limit", "-l", help="Max results")] = 50,
) -> None:
    """List tracks carrying a given tag."""
    from src.application.runner import execute_use_case
    from src.domain.repositories import UnitOfWorkProtocol

    validated_tag = validate_tag(tag)
    user_id = get_cli_user_id()

    async def _fetch(uow: UnitOfWorkProtocol):
        async with uow:
            page = await uow.get_track_repository().list_tracks(
                user_id=user_id,
                tags=[validated_tag],
                limit=limit,
                include_total=False,
            )
            return page["tracks"]

    tracks = run_async(execute_use_case(_fetch, user_id=user_id))

    if not tracks:
        console.print(f"[dim]No tracks tagged '{validated_tag}'[/dim]")
        return

    table = render_tracks_table(tracks, title=f"Tracks tagged '{validated_tag}'")
    console.print(table)
    console.print(f"[dim]{len(tracks)} track(s)[/dim]")


@app.command(name="batch")
def batch_tag(
    tag: Annotated[
        str,
        typer.Argument(
            help="Tag to apply to every track in the playlist", metavar="TAG"
        ),
    ],
    playlist: Annotated[
        str,
        typer.Option(
            "--playlist", "-p", help="Playlist UUID or name to source tracks from"
        ),
    ],
) -> None:
    """Apply one tag to every track in a playlist."""
    from src.application.runner import execute_use_case
    from src.application.use_cases.batch_tag_tracks import run_batch_tag_tracks
    from src.domain.repositories import UnitOfWorkProtocol

    validated_tag = validate_tag(tag)
    user_id = get_cli_user_id()
    resolved = resolve_playlist_ref(playlist, user_id=user_id)

    if resolved.track_count == 0:
        console.print(
            f"[dim]Playlist '{resolved.name}' has no tracks — nothing to tag[/dim]"
        )
        return

    # resolve_playlist_ref's name branch returns lightweight rows (entries=[]
    # with track_count populated from the denormalized column); re-fetch only
    # when entries haven't been hydrated.
    if len(resolved.entries) == resolved.track_count:
        full_playlist = resolved
    else:

        async def _load_with_entries(uow: UnitOfWorkProtocol):
            async with uow:
                return await uow.get_playlist_repository().get_playlist_by_id(
                    resolved.id, user_id=user_id
                )

        full_playlist = run_async(execute_use_case(_load_with_entries, user_id=user_id))

    track_ids = [entry.track.id for entry in full_playlist.entries]

    with brand_status(f"Tagging {len(track_ids)} tracks..."):
        result = run_async(
            run_batch_tag_tracks(
                user_id=user_id,
                track_ids=track_ids,
                raw_tag=validated_tag,
            )
        )

    summary = BatchOperationResult(
        succeeded=result.tagged,
        skipped=result.requested - result.tagged,
    )
    table = render_batch_summary(
        summary,
        title=f"Tagged '{result.tag}' on '{full_playlist.name}'",
    )
    console.print(table)
