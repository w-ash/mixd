"""Admin CLI commands — operational tooling for local/self-hosted instances.

``reset`` wipes imported data so the user can rebuild their library from
scratch without re-authenticating with Spotify/Last.fm. What survives, and
the truncation itself, live in ``ResetDatabaseUseCase`` — this module only
confirms intent and reports the outcome.

``repair-primaries`` clears the ``missing_primary_mappings`` failure
``mixd stats --health`` reports, for libraries carrying vacancies an older
writer left behind.
"""

from rich.prompt import Confirm
from rich.table import Table
import typer

from src.application.use_cases.repair_missing_primaries import (
    RepairMissingPrimariesResult,
)
from src.config.settings import database_host_and_mode, get_database_url
from src.interface.cli.async_runner import run_async
from src.interface.cli.cli_helpers import get_cli_user_id, handle_cli_error
from src.interface.cli.console import brand_status, get_console

console = get_console()

app = typer.Typer(
    help="Operational admin commands",
    rich_help_panel="⚙️ System",
)


@app.command(name="reset")
def reset(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    remote_ok: str | None = typer.Option(
        None,
        "--remote-ok",
        metavar="HOST",
        help="Allow the reset when the database is remote; must match its host",
    ),
) -> None:
    """Truncate all data tables for ALL users.

    Preserves user accounts and service connections so you don't need to
    re-authenticate with Spotify/Last.fm after the reset.

    Refuses a remote database unless ``--remote-ok`` names its host. The reset
    runs without a user, so the default-user guard does not apply to it.
    """
    host, mode = database_host_and_mode(get_database_url())
    if mode == "remote" and remote_ok != host:
        console.print(
            f"[red]Refusing to reset the remote database at {host}.[/red]\n"
            f"[dim]Pass --remote-ok {host} to reset it.[/dim]"
        )
        raise typer.Exit(code=1)

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


@app.command(name="repair-primaries")
def repair_primaries(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be repaired, write nothing"
    ),
) -> None:
    """Elect a primary mapping for every (track, connector) pair missing one.

    Clears the ``missing_primary_mappings`` integrity failure. Pairs that
    already hold a primary — including one you pinned yourself — are untouched.
    """
    try:
        result = run_async(_repair_primaries(dry_run=dry_run))
    except Exception as e:
        handle_cli_error(e, "Failed to repair primary mappings")

    if not result.repaired:
        console.print("[dim]No vacant primary mappings found.[/dim]")
        return

    table = Table(
        title=(
            "Primary mappings that would be elected"
            if result.dry_run
            else "Primary mappings elected"
        )
    )
    table.add_column("Track", style="cyan")
    table.add_column("Connector")
    table.add_column("Confidence", justify="right")
    for row in result.repaired:
        table.add_row(str(row.owner_id), row.connector_name, str(row.confidence))
    console.print(table)

    verb = "would be repaired" if result.dry_run else "repaired"
    console.print(f"\n[green]{len(result.repaired)} pair(s) {verb}.[/green]")


async def _repair_primaries(*, dry_run: bool) -> RepairMissingPrimariesResult:
    """Run the vacancy repair use case."""
    from src.application.runner import execute_use_case
    from src.application.use_cases.repair_missing_primaries import (
        RepairMissingPrimariesCommand,
        RepairMissingPrimariesUseCase,
    )

    user_id = get_cli_user_id()
    return await execute_use_case(
        lambda uow: RepairMissingPrimariesUseCase().execute(
            RepairMissingPrimariesCommand(user_id=user_id, dry_run=dry_run), uow
        ),
        user_id=user_id,
    )
