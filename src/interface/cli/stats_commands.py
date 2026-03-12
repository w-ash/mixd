"""CLI commands for library statistics and data integrity checks."""

from rich.panel import Panel
from rich.table import Table
import typer

from src.interface.cli.async_runner import run_async
from src.interface.cli.cli_helpers import handle_cli_error
from src.interface.cli.console import get_console

console = get_console()

app = typer.Typer(
    help="Library statistics and dashboard",
    no_args_is_help=False,
    invoke_without_command=True,
)

STATUS_STYLE: dict[str, str] = {
    "pass": "[green]PASS[/green]",
    "warn": "[yellow]WARN[/yellow]",
    "fail": "[red]FAIL[/red]",
}


@app.callback(invoke_without_command=True)
def stats(
    ctx: typer.Context,
    health: bool = typer.Option(False, "--health", help="Run data integrity checks"),
) -> None:
    """Show library statistics and counts."""
    if ctx.invoked_subcommand is not None:
        return

    if health:
        _run_health_check()
        return

    async def _stats_async():
        from src.application.runner import execute_use_case
        from src.application.use_cases.get_dashboard_stats import (
            GetDashboardStatsCommand,
            GetDashboardStatsUseCase,
        )

        result = await execute_use_case(
            lambda uow: GetDashboardStatsUseCase().execute(
                GetDashboardStatsCommand(), uow
            )
        )

        # Summary panel
        console.print(
            Panel(
                f"[cyan]Tracks:[/cyan] {result.total_tracks}\n"
                f"[cyan]Playlists:[/cyan] {result.total_playlists}\n"
                f"[cyan]Liked Tracks:[/cyan] {result.total_liked}\n"
                f"[cyan]Total Plays:[/cyan] {result.total_plays}",
                title="Library Summary",
            )
        )

        # Tracks by connector
        if result.tracks_by_connector:
            table = Table(title="Tracks by Connector")
            table.add_column("Connector", style="cyan")
            table.add_column("Tracks", justify="right")
            for connector, count in sorted(result.tracks_by_connector.items()):
                table.add_row(connector, str(count))
            console.print(table)

        # Liked by connector
        if result.liked_by_connector:
            table = Table(title="Liked by Service")
            table.add_column("Service", style="cyan")
            table.add_column("Liked", justify="right")
            for service, count in sorted(result.liked_by_connector.items()):
                table.add_row(service, str(count))
            console.print(table)

    try:
        run_async(_stats_async())
    except Exception as e:
        handle_cli_error(e, "Failed to get stats")


def _run_health_check() -> None:
    """Run integrity checks and display results."""

    async def _health_async():
        from src.application.runner import execute_use_case
        from src.application.use_cases.check_data_integrity import (
            CheckDataIntegrityCommand,
            CheckDataIntegrityUseCase,
        )

        result = await execute_use_case(
            lambda uow: CheckDataIntegrityUseCase().execute(
                CheckDataIntegrityCommand(), uow
            )
        )

        table = Table(title="Data Integrity Report")
        table.add_column("Check", style="cyan")
        table.add_column("Status", justify="center")
        table.add_column("Issues", justify="right")

        for check in result.checks:
            table.add_row(
                check.name.replace("_", " ").title(),
                STATUS_STYLE.get(check.status, check.status),
                str(check.count),
            )

        console.print(table)

        overall_text = STATUS_STYLE.get(result.overall_status, result.overall_status)
        console.print(f"\nOverall: {overall_text} ({result.total_issues} total issues)")

    try:
        run_async(_health_async())
    except Exception as e:
        handle_cli_error(e, "Failed to run integrity checks")
