"""CLI commands for scheduling background data syncs (v0.8.2).

`mixd sync schedule <target>` schedules a recurring background sync — Last.fm
play-history import or a likes import/export — on a daily or weekly cadence. It
is the sync-side peer of `mixd workflow schedule`; both are thin wrappers over
the shared ``run_schedule_command`` orchestrator so validation and dispatch live
in exactly one place.

Schedulable targets are the keys of ``SYNC_DISPATCH`` (e.g. ``lastfm:plays``,
``spotify:likes``, ``lastfm:likes``) — ``spotify:plays`` is file-import-only and
therefore not schedulable.
"""

from typing import Annotated

from rich.table import Table
import typer

from src.interface.cli.cli_helpers import (
    ScheduleCommandSpec,
    describe_cadence,
    format_next_run,
    get_cli_user_id,
    handle_cli_error,
    run_schedule_command,
    validate_sync_target_arg,
)
from src.interface.cli.console import get_console, get_error_console

console = get_console()
err_console = get_error_console()

app = typer.Typer(
    help="Schedule recurring background data syncs",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@app.command(name="schedule")
def schedule_sync(
    target: Annotated[
        str | None,
        typer.Argument(
            help="Sync target, e.g. lastfm:plays / spotify:likes / lastfm:likes"
        ),
    ] = None,
    daily: Annotated[
        bool, typer.Option("--daily", help="Run every day at --at")
    ] = False,
    weekly: Annotated[
        str | None,
        typer.Option("--weekly", help="Run weekly on this weekday, e.g. sunday"),
    ] = None,
    at: Annotated[
        str | None, typer.Option("--at", help="Time of day, HH:MM (24-hour)")
    ] = None,
    tz: Annotated[
        str | None,
        typer.Option("--tz", help="IANA timezone (default: detected local zone)"),
    ] = None,
    enable: Annotated[
        bool, typer.Option("--enable", help="Enable the existing schedule")
    ] = False,
    disable: Annotated[
        bool, typer.Option("--disable", help="Disable the existing schedule")
    ] = False,
    remove: Annotated[
        bool, typer.Option("--remove", help="Remove the schedule")
    ] = False,
    list_all: Annotated[
        bool, typer.Option("--list", help="List all of your schedules")
    ] = False,
) -> None:
    """Schedule a background sync to run daily or weekly.

    With a target and no action options, prints that target's current schedule.
    Examples: `mixd sync schedule lastfm:plays --daily --at 02:00` /
    `mixd sync schedule spotify:likes --weekly sunday --at 03:30`.
    """
    if list_all:
        _list_schedules()
        return

    if target is None:
        raise typer.BadParameter("a sync target is required (or pass --list)")

    validated = validate_sync_target_arg(target)
    run_schedule_command(
        ScheduleCommandSpec(
            user_id=get_cli_user_id(),
            label=f"sync '{validated}'",
            sync_target=validated,
            daily=daily,
            weekly=weekly,
            at=at,
            tz=tz,
            enable=enable,
            disable=disable,
            remove=remove,
        )
    )


def _list_schedules() -> None:
    """Render every schedule the user has (workflow + sync) in one table."""
    from src.application.runner import execute_use_case
    from src.application.use_cases.schedules import (
        ListSchedulesCommand,
        ListSchedulesUseCase,
    )
    from src.interface.cli.async_runner import run_async

    user_id = get_cli_user_id()
    try:
        result = run_async(
            execute_use_case(
                lambda uow: ListSchedulesUseCase().execute(
                    ListSchedulesCommand(user_id=user_id), uow
                ),
                user_id=user_id,
            )
        )
    except Exception as e:
        handle_cli_error(e, "Failed to list schedules")

    if not result.entries:
        console.print("[dim]No schedules configured.[/dim]")
        return

    table = Table(title="Schedules", show_header=True, header_style="bold magenta")
    table.add_column("Target", style="cyan")
    table.add_column("Cadence", style="green")
    table.add_column("Next run", style="dim")
    table.add_column("Status")
    table.add_column("Failures", justify="right")

    for entry in result.entries:
        s = entry.schedule
        status = (
            "[green]enabled[/green]" if s.status == "enabled" else "[dim]disabled[/dim]"
        )
        failures = (
            f"[red]{s.consecutive_failures}[/red]" if s.consecutive_failures else "0"
        )
        table.add_row(
            f"{s.target_type}: {entry.target_label}",
            describe_cadence(s),
            format_next_run(s),
            status,
            failures,
        )

    console.print(table)
