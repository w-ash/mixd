"""CLI commands for library statistics and data integrity checks."""

from rich.panel import Panel
from rich.table import Table
import typer

from src.application.use_cases.get_match_method_health import MatchingDrift
from src.config.constants import MatchMethod
from src.interface.cli.async_runner import run_async
from src.interface.cli.cli_helpers import get_cli_user_id, handle_cli_error
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


CATEGORY_ORDER = [*MatchMethod.CATEGORY_ORDER, "Unknown"]


@app.callback(invoke_without_command=True)
def stats(
    ctx: typer.Context,
    health: bool = typer.Option(False, "--health", help="Run data integrity checks"),
    matching: bool = typer.Option(
        False, "--matching", help="Show match method health report"
    ),
) -> None:
    """Show library statistics and counts."""
    if ctx.invoked_subcommand is not None:
        return

    if health:
        _run_health_check()
        return

    if matching:
        _run_matching_report()
        return

    async def _stats_async():
        from src.application.runner import execute_use_case
        from src.application.use_cases.get_dashboard_stats import (
            GetDashboardStatsCommand,
            GetDashboardStatsUseCase,
        )

        user_id = get_cli_user_id()
        result = await execute_use_case(
            lambda uow: GetDashboardStatsUseCase().execute(
                GetDashboardStatsCommand(user_id=user_id), uow
            ),
            user_id=user_id,
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

        user_id = get_cli_user_id()
        result = await execute_use_case(
            lambda uow: CheckDataIntegrityUseCase().execute(
                CheckDataIntegrityCommand(user_id=user_id), uow
            ),
            user_id=user_id,
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


def _run_matching_report() -> None:
    """Show match method health report grouped by category."""

    async def _matching_async():
        from src.application.runner import execute_use_case
        from src.application.use_cases.get_match_method_health import (
            GetMatchMethodHealthCommand,
            GetMatchMethodHealthUseCase,
        )

        user_id = get_cli_user_id()
        result = await execute_use_case(
            lambda uow: GetMatchMethodHealthUseCase().execute(
                GetMatchMethodHealthCommand(user_id=user_id), uow
            ),
            user_id=user_id,
        )

        console.print(
            Panel(
                f"[cyan]Recent window:[/cyan] last {result.recent_days} days",
                title=f"Match Method Health Report ({result.total_mappings:,} total mappings)",
            )
        )

        if not result.stats:
            console.print("[dim]No track mappings found.[/dim]")
            return

        by_category = result.by_category
        for category in CATEGORY_ORDER:
            group = by_category.get(category)
            if not group:
                continue

            category_total = sum(s.total_count for s in group)
            table = Table(title=f"  {category} ({category_total:,} mappings)")
            table.add_column("Method", style="cyan")
            table.add_column("Connector")
            table.add_column("Total", justify="right")
            table.add_column("Last 30d", justify="right")
            table.add_column("Avg Conf", justify="right")
            table.add_column("Min", justify="right")
            table.add_column("Reject", justify="right")
            table.add_column("Review", justify="right")
            table.add_column("Accept", justify="right")
            table.add_column("Certain", justify="right")

            for stat in group:
                table.add_row(
                    stat.match_method,
                    stat.connector_name,
                    f"{stat.total_count:,}",
                    str(stat.recent_count),
                    f"{stat.avg_confidence:.1f}",
                    str(stat.min_confidence),
                    str(stat.band_reject),
                    str(stat.band_review),
                    str(stat.band_accept),
                    str(stat.band_certain),
                )

            console.print(table)

        _render_drift_panel(result.drift)

    try:
        run_async(_matching_async())
    except Exception as e:
        handle_cli_error(e, "Failed to get matching report")


def _render_drift_panel(drift: MatchingDrift) -> None:
    """Render the drift-signals panel: exploratory metrics, no fixed thresholds.

    Baseline these empirically and compare week-over-week rather than
    reading any single number as a pass/fail gate.
    """
    lines: list[str] = []

    if drift.fallback_shares:
        lines.append("[cyan]Fallback share (30d, per connector):[/cyan]")
        lines.extend(
            f"  {share.connector_name}: {share.fallback_share:.1%} "
            f"({share.recent_fallback}/{share.recent_total})"
            for share in drift.fallback_shares
        )
    else:
        lines.append("[cyan]Fallback share (30d):[/cyan] no recent mappings")

    lines.append(
        f"[cyan]Review inflow:[/cyan] 7d={drift.review_inflow_7d}, "
        f"30d={drift.review_inflow_30d}"
    )
    oldest = (
        f"{drift.review_oldest_pending_days:.1f}d"
        if drift.review_oldest_pending_days is not None
        else "n/a"
    )
    lines.extend([
        f"[cyan]Review queue:[/cyan] depth={drift.review_pending_depth}, "
        f"oldest={oldest}",
        f"[cyan]isrc_suspect pending:[/cyan] {drift.isrc_suspect_pending_count}",
        "[cyan]Confidence/evidence divergence:[/cyan] "
        f"{drift.confidence_evidence_divergence_count}",
        f"[cyan]Stale denormalized IDs:[/cyan] {drift.stale_denormalized_ids_count}",
    ])

    console.print(
        Panel(
            "\n".join(lines),
            title="Drift Signals",
            subtitle="[dim]baseline empirically, compare week-over-week — no fixed thresholds[/dim]",
        )
    )
