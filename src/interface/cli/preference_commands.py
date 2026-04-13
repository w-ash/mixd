"""CLI commands for managing track preferences (hmm, nah, yah, star)."""

from typing import Annotated

from rich.table import Table
import typer

from src.domain.entities.preference import PREFERENCE_ORDER
from src.interface.cli.async_runner import run_async
from src.interface.cli.cli_helpers import (
    get_cli_user_id,
    render_tracks_table,
    resolve_track_ref,
    validate_preference_state,
)
from src.interface.cli.console import brand_status, get_console

console = get_console()

app = typer.Typer(
    help="Rate tracks and browse your preferences",
    rich_help_panel="🎵 Track Operations",
)


@app.command(name="set")
def set_preference(
    track_ref: Annotated[
        str, typer.Argument(help="Track UUID or search string", metavar="TRACK")
    ],
    state: Annotated[
        str,
        typer.Option("--state", "-s", help="Preference: hmm, nah, yah, or star"),
    ],
) -> None:
    """Set a preference on a track."""
    from src.application.use_cases.set_track_preference import run_set_track_preference

    validated_state = validate_preference_state(state)
    user_id = get_cli_user_id()
    track = resolve_track_ref(track_ref, user_id=user_id)

    with brand_status("Setting preference..."):
        result = run_async(
            run_set_track_preference(
                user_id=user_id,
                track_id=track.id,
                state=validated_state,
            )
        )

    if result.changed:
        console.print(
            f"[green]Set preference to [bold]{validated_state}[/bold][/green]"
        )
    else:
        console.print(
            f"[dim]Preference already {result.state or 'unset'} — no change[/dim]"
        )


@app.command(name="clear")
def clear_preference(
    track_ref: Annotated[
        str, typer.Argument(help="Track UUID or search string", metavar="TRACK")
    ],
) -> None:
    """Remove a preference from a track."""
    from src.application.use_cases.set_track_preference import run_set_track_preference

    user_id = get_cli_user_id()
    track = resolve_track_ref(track_ref, user_id=user_id)

    with brand_status("Clearing preference..."):
        result = run_async(
            run_set_track_preference(
                user_id=user_id,
                track_id=track.id,
                state=None,
            )
        )

    if result.changed:
        console.print("[green]Preference cleared[/green]")
    else:
        console.print("[dim]No preference to clear[/dim]")


@app.command(name="list")
def list_preferences(
    state: Annotated[
        str,
        typer.Option("--state", "-s", help="Filter by state: hmm, nah, yah, star"),
    ],
    limit: Annotated[int, typer.Option("--limit", "-l", help="Max results")] = 50,
) -> None:
    """List tracks with a specific preference."""
    from src.application.runner import execute_use_case
    from src.domain.repositories import UnitOfWorkProtocol

    validated_state = validate_preference_state(state)
    user_id = get_cli_user_id()

    async def _fetch(uow: UnitOfWorkProtocol):
        async with uow:
            prefs = await uow.get_preference_repository().list_by_state(
                validated_state, user_id=user_id, limit=limit
            )
            by_id = await uow.get_track_repository().find_tracks_by_ids([
                p.track_id for p in prefs
            ])
            # Preserve the preferred_at ordering from list_by_state.
            return [
                (by_id[p.track_id], p.preferred_at)
                for p in prefs
                if p.track_id in by_id
            ]

    rows = run_async(execute_use_case(_fetch, user_id=user_id))

    if not rows:
        console.print(f"[dim]No tracks with preference '{validated_state}'[/dim]")
        return

    tracks = [r[0] for r in rows]
    times = {t.id: r[1] for t, r in zip(tracks, rows, strict=True)}
    table = render_tracks_table(
        tracks,
        title=f"Tracks — {validated_state}",
        extra_columns=[
            (
                "Preferred At",
                lambda t: (
                    times[t.id].strftime("%Y-%m-%d %H:%M") if times[t.id] else "—"
                ),
            )
        ],
    )
    console.print(table)
    console.print(f"[dim]{len(tracks)} track(s)[/dim]")


@app.command(name="stats")
def preference_stats() -> None:
    """Show preference counts by state."""
    from src.application.runner import execute_use_case

    user_id = get_cli_user_id()
    with brand_status("Counting..."):
        counts = run_async(
            execute_use_case(
                lambda uow: uow.get_preference_repository().count_by_state(
                    user_id=user_id
                ),
                user_id=user_id,
            )
        )

    if not counts:
        console.print("[dim]No preferences set yet[/dim]")
        return

    table = Table(title="Preference Stats")
    table.add_column("State", style="bold")
    table.add_column("Count", justify="right", style="cyan")

    for s in PREFERENCE_ORDER:
        table.add_row(s, str(counts.get(s, 0)))

    total = sum(counts.values())
    table.add_section()
    table.add_row("Total", str(total), style="bold")

    console.print(table)


@app.command(name="sync-from-likes")
def sync_from_likes() -> None:
    """Create preferences from existing Spotify/Last.fm likes."""
    from src.application.use_cases.sync_preferences_from_likes import (
        run_sync_preferences_from_likes,
    )

    with brand_status("Syncing preferences from likes..."):
        result = run_async(run_sync_preferences_from_likes(user_id=get_cli_user_id()))

    console.print(
        f"[green]Synced:[/green] {result.created} created, "
        f"{result.upgraded} upgraded, {result.skipped} skipped"
    )
