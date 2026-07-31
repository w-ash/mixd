"""Admin CLI commands — operational tooling for local/self-hosted instances.

``reset`` wipes imported data so the user can rebuild their library from
scratch without re-authenticating with Spotify/Last.fm. What survives, and
the truncation itself, live in ``ResetDatabaseUseCase`` — this module only
confirms intent and reports the outcome.
"""

from rich.prompt import Confirm
import typer

from src.interface.cli.async_runner import run_async
from src.interface.cli.console import brand_status, get_console

console = get_console()

app = typer.Typer(
    help="Operational admin commands",
    rich_help_panel="⚙️ System",
)


@app.command(name="reset")
def reset(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Truncate all data tables for ALL users.

    Preserves user accounts and service connections so you don't need to
    re-authenticate with Spotify/Last.fm after the reset.
    """
    console.print(
        "[yellow]This will delete ALL tracks, likes, history, playlists, "
        "workflows, and preferences for ALL users.[/yellow]\n"
        "[dim]User accounts and service connections will be preserved.[/dim]"
    )

    if not yes and not Confirm.ask("Continue?", default=False):
        console.print("[dim]Aborted.[/dim]")
        raise typer.Exit(code=0)

    with brand_status("Truncating data tables..."):
        result = run_async(_truncate_all())

    console.print(
        f"[green]✓ Reset complete[/green] "
        f"[dim]({len(result.truncated_tables)} tables). "
        f"Re-import your data to rebuild.[/dim]"
    )


async def _truncate_all():
    """Run the reset use case."""
    from src.application.runner import execute_use_case
    from src.application.use_cases.reset_database import (
        ResetDatabaseCommand,
        ResetDatabaseUseCase,
    )

    return await execute_use_case(
        lambda uow: ResetDatabaseUseCase().execute(ResetDatabaseCommand(), uow)
    )
