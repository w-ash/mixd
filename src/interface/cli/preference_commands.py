"""CLI commands for managing track preferences (hmm, nah, yah, star)."""

from typing import Annotated
from uuid import UUID

from rich.table import Table
import typer

from src.domain.entities.preference import PREFERENCE_ORDER
from src.interface.cli.async_runner import run_async
from src.interface.cli.cli_helpers import get_cli_user_id
from src.interface.cli.console import brand_status, get_console, get_error_console

console = get_console()
err_console = get_error_console()

VALID_STATES = tuple(PREFERENCE_ORDER)

app = typer.Typer(
    help="Rate tracks and browse your preferences",
    rich_help_panel="🎵 Track Operations",
)


@app.command(name="set")
def set_preference(
    track_id: Annotated[str, typer.Argument(help="Track UUID")],
    state: Annotated[
        str,
        typer.Option("--state", "-s", help="Preference: hmm, nah, yah, or star"),
    ],
) -> None:
    """Set a preference on a track."""
    if state not in VALID_STATES:
        err_console.print(
            f"[red]Invalid state '{state}'. Must be one of: {', '.join(VALID_STATES)}[/red]"
        )
        raise typer.Exit(code=1)

    from src.application.use_cases.set_track_preference import run_set_track_preference

    with brand_status("Setting preference..."):
        result = run_async(
            run_set_track_preference(
                user_id=get_cli_user_id(),
                track_id=UUID(track_id),
                state=state,
            )
        )

    if result.changed:
        console.print(f"[green]Set preference to [bold]{state}[/bold][/green]")
    else:
        console.print(
            f"[dim]Preference already {result.state or 'unset'} — no change[/dim]"
        )


@app.command(name="clear")
def clear_preference(
    track_id: Annotated[str, typer.Argument(help="Track UUID")],
) -> None:
    """Remove a preference from a track."""
    from src.application.use_cases.set_track_preference import run_set_track_preference

    with brand_status("Clearing preference..."):
        result = run_async(
            run_set_track_preference(
                user_id=get_cli_user_id(),
                track_id=UUID(track_id),
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
    if state not in VALID_STATES:
        err_console.print(
            f"[red]Invalid state '{state}'. Must be one of: {', '.join(VALID_STATES)}[/red]"
        )
        raise typer.Exit(code=1)

    from src.application.runner import execute_use_case

    user_id = get_cli_user_id()
    prefs = run_async(
        execute_use_case(
            lambda uow: uow.get_preference_repository().list_by_state(
                state, user_id=user_id, limit=limit
            ),
            user_id=user_id,
        )
    )

    if not prefs:
        console.print(f"[dim]No tracks with preference '{state}'[/dim]")
        return

    table = Table(title=f"Tracks — {state}")
    table.add_column("Track ID", style="dim")
    table.add_column("Preferred At", style="cyan")

    for p in prefs:
        table.add_row(
            str(p.track_id),
            p.preferred_at.strftime("%Y-%m-%d %H:%M") if p.preferred_at else "—",
        )

    console.print(table)
    console.print(f"[dim]{len(prefs)} track(s)[/dim]")


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

    for s in VALID_STATES:
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
