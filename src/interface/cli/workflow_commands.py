"""CLI commands for workflow execution and management.

Database-backed workflow commands using the same use cases as the web API.
CLI `workflow run` creates run records for unified execution history visible
from both CLI and web UI.
"""

# pyright: reportExplicitAny=false
# Legitimate Any: Coroutine[Any,Any,T], Rich/Typer display types, **kwargs pass-through

from collections.abc import Sequence
import contextlib
from datetime import datetime
from typing import Annotated, Any, Literal

from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
import typer

from src.domain.entities.workflow import RunStatus, Workflow
from src.interface.cli.async_runner import run_async
from src.interface.cli.cli_helpers import handle_cli_error
from src.interface.cli.console import (
    brand_panel,
    get_console,
    get_error_console,
    print_banner,
    progress_coordination_context,
)
from src.interface.cli.ui import display_operation_result

console = get_console()
err_console = get_error_console()


app = typer.Typer(
    help="Execute and manage playlist workflows",
    no_args_is_help=False,
    rich_markup_mode="rich",
)


# ---------------------------------------------------------------------------
# Status updater closures (infrastructure wiring for ExecuteWorkflowRunUseCase)
# ---------------------------------------------------------------------------
# Mirrors src/interface/api/routes/workflows.py — each interface layer owns
# its own concrete implementations per interface-patterns rule.


@contextlib.asynccontextmanager
async def _run_repo_session():
    """Short-lived independent session for run/node status updates."""
    from src.infrastructure.persistence.database.db_connection import get_session
    from src.infrastructure.persistence.repositories.workflow.runs import (
        WorkflowRunRepository,
    )

    async with get_session() as session:
        yield WorkflowRunRepository(session)
        await session.commit()


async def _update_run_status(
    run_id: int,
    status: RunStatus,
    **kwargs: Any,
) -> None:
    """Concrete RunStatusUpdater for CLI."""
    async with _run_repo_session() as repo:
        await repo.update_run_status(run_id, status, **kwargs)


async def _update_node_status(
    run_id: int,
    node_id: str,
    status: RunStatus,
    *,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    duration_ms: int | None = None,
    input_track_count: int | None = None,
    output_track_count: int | None = None,
    error_message: str | None = None,
    node_details: dict[str, Any] | None = None,
) -> None:
    """Concrete NodeStatusUpdater for CLI."""
    async with _run_repo_session() as repo:
        await repo.update_node_status(
            run_id,
            node_id,
            status,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            input_track_count=input_track_count,
            output_track_count=output_track_count,
            error_message=error_message,
            node_details=node_details,
        )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def workflow_main(ctx: typer.Context) -> None:
    """Interactive workflow browser with progressive discovery."""
    if ctx.invoked_subcommand is None:
        _show_interactive_workflow_browser()


@app.command()
def run(
    workflow_id: Annotated[
        str | None, typer.Argument(help="Workflow ID (number or slug) to execute")
    ] = None,
    show_results: Annotated[bool, typer.Option("--show-results/--no-results")] = True,
    output_format: Annotated[
        Literal["table", "json"], typer.Option("--format", "-f")
    ] = "table",
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
) -> None:
    """Execute a specific workflow.

    Supports both direct execution (automation-friendly) and interactive
    selection when no workflow_id provided (progressive discovery).
    """
    workflows = _get_available_workflows()
    if not workflows:
        console.print("[red]No workflows found.[/red]")
        raise typer.Exit(1)

    if workflow_id is None:
        _display_workflows_table(workflows)
        selected = _prompt_for_workflow_selection(workflows)
        if selected is None:
            return
    else:
        selected = _resolve_workflow(workflows, workflow_id)
        if selected is None:
            err_console.print(f"[red]Error: Workflow '{workflow_id}' not found.[/red]")
            raise typer.Exit(1)

    _execute_workflow(selected, show_results, output_format, quiet)


@app.command(name="list")
def list_workflows(
    output_format: Annotated[
        Literal["table", "json"], typer.Option("--format", "-f")
    ] = "table",
) -> None:
    """List available workflow definitions."""
    workflows = _get_available_workflows()

    if not workflows:
        console.print("[red]No workflows found.[/red]")
        return

    if output_format == "json":
        import json

        print(
            json.dumps(
                [
                    {
                        "id": wf.id,
                        "slug": wf.definition.id,
                        "name": wf.definition.name,
                        "description": wf.definition.description,
                        "task_count": len(wf.definition.tasks),
                        "is_template": wf.is_template,
                    }
                    for wf in workflows
                ],
                indent=2,
            )
        )
    else:
        _display_workflows_table(workflows)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _show_interactive_workflow_browser() -> None:
    """Display interactive workflow browser."""
    workflows = _get_available_workflows()

    if not workflows:
        console.print("[red]No workflows found.[/red]")
        return

    from importlib.metadata import version

    print_banner(version("narada"))
    console.print()
    console.print(
        brand_panel(
            "🎵 [bold]Workflow Browser[/bold]\n[dim]Discover and execute playlist transformation workflows[/dim]",
            "Narada Workflows",
            emoji="⚡",
        )
    )

    _display_workflows_table(workflows)

    selected = _prompt_for_workflow_selection(workflows)
    if selected is not None:
        console.print(
            f"\n[green]Executing workflow:[/green] [bold]{selected.definition.name}[/bold]"
        )
        _execute_workflow(
            selected, show_results=True, output_format="table", quiet=False
        )


def _resolve_workflow(
    workflows: Sequence[Workflow], identifier: str
) -> Workflow | None:
    """Resolve a workflow by database ID (int) or definition slug (string)."""
    # Try as database integer ID first
    if identifier.isdigit():
        db_id = int(identifier)
        return next((wf for wf in workflows if wf.id == db_id), None)

    # Fall back to definition slug (source_template or definition.id)
    return next(
        (wf for wf in workflows if wf.definition.id == identifier),
        None,
    )


def _prompt_for_workflow_selection(workflows: Sequence[Workflow]) -> Workflow | None:
    """Interactive workflow selection — returns Workflow or None on cancel."""
    choices: list[str] = []
    index_map: dict[str, Workflow] = {}

    for i, wf in enumerate(workflows, 1):
        key = str(i)
        choices.extend((key, str(wf.id)))
        index_map[key] = wf
        index_map[str(wf.id)] = wf

    choices.extend(["q", "quit", "exit", "cancel"])

    choice = Prompt.ask(
        f"\n[bold]Select workflow[/bold] [dim](1-{len(workflows)} or ID)[/dim]",
        choices=choices,
        default="",
        show_choices=False,
    ).strip()

    if choice in ("", "q", "quit", "exit", "cancel"):
        return None

    return index_map.get(choice)


def _execute_workflow(
    workflow: Workflow,
    show_results: bool,
    output_format: Literal["table", "json"],
    quiet: bool,
) -> None:
    """Execute workflow with run record creation and Rich progress display.

    Composes RunWorkflowUseCase (PENDING record) with direct run_workflow()
    call so the CLI gets both: run history in the DB AND the OperationResult
    for detailed track display. The RunHistoryObserver handles node-level
    DB updates; ProgressNodeObserver handles Rich progress bars.
    """
    wf_def = workflow.definition
    try:
        if not quiet:
            console.print(
                brand_panel(
                    f"[bold]{wf_def.name}[/bold]\n[dim]{wf_def.description}[/dim]\n[cyan]Tasks: [bold]{len(wf_def.tasks)}[/bold][/cyan]",
                    "Executing Workflow",
                    emoji="⚡",
                )
            )

        async def _run_with_history():
            from datetime import UTC, datetime

            from src.application.runner import execute_use_case
            from src.application.use_cases.workflow_runs import (
                RunWorkflowCommand,
                RunWorkflowUseCase,
                serialize_output_tracks,
            )
            from src.application.workflows.observers import RunHistoryObserver
            from src.application.workflows.prefect import run_workflow
            from src.config.constants import WorkflowConstants

            # 1. Create PENDING run record
            run_result = await execute_use_case(
                lambda uow: RunWorkflowUseCase().execute(
                    RunWorkflowCommand(workflow_id=workflow.id or 0), uow
                )
            )
            run_id = run_result.run_id

            # 2. Update to RUNNING
            await _update_run_status(
                run_id,
                WorkflowConstants.RUN_STATUS_RUNNING,
                started_at=datetime.now(UTC),
            )

            # 3. Execute with both observers (composition handled by run_workflow)
            try:
                async with progress_coordination_context(show_live=not quiet) as ctx:
                    progress_manager = ctx.get_progress_manager()
                    observer = RunHistoryObserver(
                        run_id=run_id,
                        update_node_status=_update_node_status,
                    )
                    result = await run_workflow(
                        wf_def, progress_manager, observer=observer
                    )
            except Exception as exc:
                # 5. Update to FAILED
                with contextlib.suppress(Exception):
                    await _update_run_status(
                        run_id,
                        WorkflowConstants.RUN_STATUS_FAILED,
                        error_message=str(exc)[:500],
                    )
                raise
            else:
                # 4. Update to COMPLETED
                output_tracks, _ = serialize_output_tracks(
                    result.tracks, metrics=result.metrics
                )
                await _update_run_status(
                    run_id,
                    WorkflowConstants.RUN_STATUS_COMPLETED,
                    completed_at=datetime.now(UTC),
                    output_track_count=len(result.tracks) if result.tracks else None,
                    output_tracks=output_tracks,
                )
                return result

        result = run_async(_run_with_history())

        if not quiet:
            track_count = len(result.tracks) if result and result.tracks else 0
            console.print(
                Panel.fit(
                    f"[bold green]{wf_def.name}[/bold green]\n[cyan]Processed [bold]{track_count}[/bold] tracks[/cyan]",
                    title="[bold green]✓ Workflow Completed[/bold green]",
                    border_style="green",
                )
            )

        if show_results and result:
            display_operation_result(result, output_format=output_format)

    except Exception as e:
        if not quiet:
            handle_cli_error(e, "Workflow execution failed")
        raise typer.Exit(1) from e


def _display_workflows_table(workflows: Sequence[Workflow]) -> None:
    """Display workflows in a Rich table."""
    table = Table(
        title="Available Workflows",
        show_header=True,
        header_style="bold magenta",
        expand=True,
        width=None,
        leading=1,
    )
    table.add_column("#", min_width=3, max_width=3)
    table.add_column("ID", justify="right", min_width=4, max_width=5)
    table.add_column("Name", style="green", ratio=1)
    table.add_column("Description", style="dim", ratio=2)
    table.add_column("Tasks", justify="right", min_width=6, max_width=8)
    table.add_column("Type", min_width=10, max_width=10)

    for i, wf in enumerate(workflows, 1):
        d = wf.definition
        type_badge = "[dim]template[/dim]" if wf.is_template else "[cyan]custom[/cyan]"
        table.add_row(
            str(i),
            str(wf.id),
            d.name,
            d.description,
            str(len(d.tasks)),
            type_badge,
        )

    console.print(table)


def _get_available_workflows() -> list[Workflow]:
    """Get workflows from database (seeds templates on first call)."""

    async def _fetch():
        from src.application.runner import execute_use_case
        from src.application.use_cases.workflow_crud import (
            ListWorkflowsCommand,
            ListWorkflowsUseCase,
        )
        from src.interface.cli.db_bootstrap import ensure_cli_db_ready

        await ensure_cli_db_ready()
        result = await execute_use_case(
            lambda uow: ListWorkflowsUseCase().execute(
                ListWorkflowsCommand(include_templates=True), uow
            )
        )
        return result.workflows

    try:
        return run_async(_fetch())
    except Exception as e:
        handle_cli_error(e, "Failed to load workflows")
