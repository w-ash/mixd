"""CLI commands for workflow execution and management.

Database-backed workflow commands using the same use cases as the web API.
CLI `workflow run` creates run records for unified execution history visible
from both CLI and web UI. Full CRUD commands support `--format json` for
machine-readable output consumed by the workflow-manager agent.
"""

from collections.abc import Sequence
import contextlib
from datetime import datetime
from pathlib import Path
import sys
from typing import Annotated, Literal, Unpack, cast
from uuid import UUID

from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
import typer

from src.application.workflows.protocols import RunStatusKwargs
from src.domain.entities.shared import JsonDict
from src.domain.entities.workflow import RunStatus, Workflow
from src.interface.cli.async_runner import run_async
from src.interface.cli.cli_helpers import get_cli_user_id, handle_cli_error
from src.interface.cli.completions import complete_workflow_id
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
    run_id: UUID,
    status: RunStatus,
    **kwargs: Unpack[RunStatusKwargs],
) -> None:
    """Concrete RunStatusUpdater for CLI."""
    async with _run_repo_session() as repo:
        await repo.update_run_status(run_id, status, **kwargs)


async def _update_node_status(
    run_id: UUID,
    node_id: str,
    status: RunStatus,
    *,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    duration_ms: int | None = None,
    input_track_count: int | None = None,
    output_track_count: int | None = None,
    error_message: str | None = None,
    node_details: dict[str, object] | None = None,
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
        str | None,
        typer.Argument(
            help="Workflow ID (number or slug) to execute",
            autocompletion=complete_workflow_id,
        ),
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


@app.command()
def get(
    workflow_id: Annotated[
        str,
        typer.Argument(
            help="Workflow ID (number or slug)",
            autocompletion=complete_workflow_id,
        ),
    ],
    output_format: Annotated[
        Literal["table", "json"], typer.Option("--format", "-f")
    ] = "table",
) -> None:
    """Show a workflow's full definition."""
    workflows = _get_available_workflows()
    selected = _resolve_workflow(workflows, workflow_id)
    if selected is None:
        err_console.print(f"[red]Error: Workflow '{workflow_id}' not found.[/red]")
        raise typer.Exit(1)

    if output_format == "json":
        print(_serialize_workflow_json(selected))
    else:
        _display_workflow_detail(selected)


@app.command()
def create(
    file: Annotated[
        Path | None,
        typer.Option(
            "--file", "-f", help="JSON definition file (reads stdin if omitted)"
        ),
    ] = None,
    output_format: Annotated[
        Literal["table", "json"], typer.Option("--format")
    ] = "table",
) -> None:
    """Create a workflow from a JSON definition."""
    import json as json_mod

    from src.domain.entities.workflow import parse_workflow_def

    raw_json = _read_json_input(file)
    try:
        raw = cast("JsonDict", json_mod.loads(raw_json))
        definition = parse_workflow_def(raw)
    except (json_mod.JSONDecodeError, KeyError, TypeError) as e:
        err_console.print(f"[red]Error: Invalid JSON definition: {e}[/red]")
        raise typer.Exit(1) from e

    async def _create():
        from src.application.runner import execute_use_case
        from src.application.use_cases.workflow_crud import (
            CreateWorkflowCommand,
            CreateWorkflowUseCase,
        )
        from src.interface.cli.db_bootstrap import ensure_cli_db_ready

        user_id = get_cli_user_id()
        await ensure_cli_db_ready()
        result = await execute_use_case(
            lambda uow: CreateWorkflowUseCase().execute(
                CreateWorkflowCommand(user_id=user_id, definition=definition),
                uow,
            ),
            user_id=user_id,
        )
        return result.workflow

    try:
        workflow = run_async(_create())
    except Exception as e:
        handle_cli_error(e, "Failed to create workflow")

    if output_format == "json":
        print(_serialize_workflow_json(workflow))
    else:
        console.print(
            f"[green]Created workflow[/green] [bold]{workflow.definition.name}[/bold] (ID: {workflow.id})"
        )


@app.command()
def update(
    workflow_id: Annotated[
        str,
        typer.Argument(
            help="Workflow ID (number or slug)",
            autocompletion=complete_workflow_id,
        ),
    ],
    file: Annotated[
        Path | None,
        typer.Option(
            "--file", "-f", help="JSON definition file (reads stdin if omitted)"
        ),
    ] = None,
    output_format: Annotated[
        Literal["table", "json"], typer.Option("--format")
    ] = "table",
) -> None:
    """Update a workflow's definition from JSON.

    Template workflows cannot be modified. Task changes auto-version.
    """
    import json as json_mod

    from src.domain.entities.workflow import parse_workflow_def

    # Resolve to DB id first
    workflows = _get_available_workflows()
    selected = _resolve_workflow(workflows, workflow_id)
    if selected is None:
        err_console.print(f"[red]Error: Workflow '{workflow_id}' not found.[/red]")
        raise typer.Exit(1)

    raw_json = _read_json_input(file)
    try:
        raw = cast("JsonDict", json_mod.loads(raw_json))
        definition = parse_workflow_def(raw)
    except (json_mod.JSONDecodeError, KeyError, TypeError) as e:
        err_console.print(f"[red]Error: Invalid JSON definition: {e}[/red]")
        raise typer.Exit(1) from e

    async def _update():
        from src.application.runner import execute_use_case
        from src.application.use_cases.workflow_crud import (
            UpdateWorkflowCommand,
            UpdateWorkflowUseCase,
        )

        user_id = get_cli_user_id()
        result = await execute_use_case(
            lambda uow: UpdateWorkflowUseCase().execute(
                UpdateWorkflowCommand(
                    user_id=user_id,
                    workflow_id=selected.id or 0,
                    definition=definition,
                ),
                uow,
            ),
            user_id=user_id,
        )
        return result.workflow

    try:
        workflow = run_async(_update())
    except Exception as e:
        handle_cli_error(e, "Failed to update workflow")

    if output_format == "json":
        print(_serialize_workflow_json(workflow))
    else:
        console.print(
            f"[green]Updated workflow[/green] [bold]{workflow.definition.name}[/bold] "
            f"(v{workflow.definition_version})"
        )


@app.command()
def delete(
    workflow_id: Annotated[
        str,
        typer.Argument(
            help="Workflow ID (number or slug)",
            autocompletion=complete_workflow_id,
        ),
    ],
) -> None:
    """Delete a workflow. Template workflows cannot be deleted."""
    workflows = _get_available_workflows()
    selected = _resolve_workflow(workflows, workflow_id)
    if selected is None:
        err_console.print(f"[red]Error: Workflow '{workflow_id}' not found.[/red]")
        raise typer.Exit(1)

    async def _delete():
        from src.application.runner import execute_use_case
        from src.application.use_cases.workflow_crud import (
            DeleteWorkflowCommand,
            DeleteWorkflowUseCase,
        )

        user_id = get_cli_user_id()
        await execute_use_case(
            lambda uow: DeleteWorkflowUseCase().execute(
                DeleteWorkflowCommand(user_id=user_id, workflow_id=selected.id or 0),
                uow,
            ),
            user_id=user_id,
        )

    try:
        run_async(_delete())
    except Exception as e:
        handle_cli_error(e, "Failed to delete workflow")

    console.print(
        f"[green]Deleted workflow[/green] [bold]{selected.definition.name}[/bold] (ID: {selected.id})"
    )


@app.command()
def export(
    all_workflows: Annotated[
        bool, typer.Option("--all", help="Export all non-template workflows")
    ] = False,
    workflow_id: Annotated[
        str | None, typer.Option("--id", help="Export a single workflow by ID or slug")
    ] = None,
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Output directory")
    ] = Path(),
) -> None:
    """Export workflow definitions to JSON files."""
    if not all_workflows and workflow_id is None:
        err_console.print("[red]Error: Provide either --all or --id.[/red]")
        raise typer.Exit(1)
    if all_workflows and workflow_id is not None:
        err_console.print("[red]Error: --all and --id are mutually exclusive.[/red]")
        raise typer.Exit(1)

    workflows = _get_available_workflows()
    if not workflows:
        console.print("[red]No workflows found.[/red]")
        raise typer.Exit(1)

    if all_workflows:
        to_export = [wf for wf in workflows if not wf.is_template]
        if not to_export:
            console.print("[yellow]No non-template workflows to export.[/yellow]")
            return
    else:
        selected = _resolve_workflow(workflows, workflow_id)  # type: ignore[arg-type]
        if selected is None:
            err_console.print(f"[red]Error: Workflow '{workflow_id}' not found.[/red]")
            raise typer.Exit(1)
        to_export = [selected]

    output_dir.mkdir(parents=True, exist_ok=True)

    for wf in to_export:
        filename = f"{wf.definition.id}.json"
        (output_dir / filename).write_text(
            _serialize_workflow_json(wf), encoding="utf-8"
        )

    console.print(
        f"[green]Exported {len(to_export)} workflow(s) to {output_dir}[/green]"
    )


@app.command()
def validate(
    file: Annotated[
        Path | None,
        typer.Option(
            "--file", "-f", help="JSON definition file (reads stdin if omitted)"
        ),
    ] = None,
    output_format: Annotated[
        Literal["table", "json"], typer.Option("--format")
    ] = "table",
) -> None:
    """Validate a workflow definition without saving."""
    import json as json_mod

    from src.application.workflows.validation import (
        is_validation_error,
        validate_workflow_def_detailed,
    )
    from src.domain.entities.workflow import parse_workflow_def

    raw_json = _read_json_input(file)
    try:
        raw = cast("JsonDict", json_mod.loads(raw_json))
        definition = parse_workflow_def(raw)
    except (json_mod.JSONDecodeError, KeyError, TypeError) as e:
        err_console.print(f"[red]Error: Invalid JSON: {e}[/red]")
        raise typer.Exit(1) from e

    issues = validate_workflow_def_detailed(definition)
    errors = [i for i in issues if is_validation_error(i)]
    warnings = [i for i in issues if not is_validation_error(i)]

    if output_format == "json":
        print(
            json_mod.dumps(
                {
                    "valid": len(errors) == 0,
                    "errors": errors,
                    "warnings": warnings,
                },
                indent=2,
            )
        )
    else:
        if errors:
            for e in errors:
                console.print(
                    f"[red]ERROR[/red] [{e.get('task_id', '')}] {e.get('field', '')}: {e['message']}"
                )
        if warnings:
            for w in warnings:
                console.print(
                    f"[yellow]WARN[/yellow]  [{w.get('task_id', '')}] {w.get('field', '')}: {w['message']}"
                )
        if not errors and not warnings:
            console.print("[green]Valid[/green] — no errors or warnings")
        elif not errors:
            console.print(f"[green]Valid[/green] with {len(warnings)} warning(s)")

    if errors:
        raise typer.Exit(1)


@app.command()
def nodes(
    output_format: Annotated[
        Literal["table", "json"], typer.Option("--format", "-f")
    ] = "table",
) -> None:
    """List available node types with their config fields."""
    import json as json_mod

    from attrs import asdict

    from src.application.workflows.node_config_fields import get_node_config_fields
    from src.application.workflows.node_registry import list_nodes

    all_nodes = list_nodes()
    config_fields = get_node_config_fields()

    if output_format == "json":
        catalog: list[dict[str, object]] = []
        for node_id, meta in sorted(all_nodes.items()):
            fields = config_fields.get(node_id, ())
            catalog.append({
                "id": node_id,
                "category": meta.get("category", ""),
                "description": meta.get("description", ""),
                "config_fields": [asdict(f) for f in fields],
            })
        print(json_mod.dumps(catalog, indent=2, default=str))
    else:
        table = Table(
            title="Available Node Types",
            show_header=True,
            header_style="bold magenta",
            expand=True,
        )
        table.add_column("Node Type", style="green", ratio=1)
        table.add_column("Category", min_width=10)
        table.add_column("Description", style="dim", ratio=2)
        table.add_column("Required Config", ratio=1)

        for node_id, meta in sorted(all_nodes.items()):
            fields = config_fields.get(node_id, ())
            required = [f.key for f in fields if f.required]
            table.add_row(
                node_id,
                str(meta.get("category", "")),
                str(meta.get("description", "")),
                ", ".join(required) if required else "[dim]none[/dim]",
            )

        console.print(table)


@app.command(name="runs")
def list_runs(
    workflow_id: Annotated[
        str | None,
        typer.Argument(
            help="Workflow ID (number or slug)",
            autocompletion=complete_workflow_id,
        ),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-l")] = 20,
    output_format: Annotated[
        Literal["table", "json"], typer.Option("--format", "-f")
    ] = "table",
) -> None:
    """List workflow execution runs."""
    # Resolve workflow_id to UUID if provided
    resolved_wf_id: UUID | None = None
    if workflow_id is not None:
        workflows = _get_available_workflows()
        selected = _resolve_workflow(workflows, workflow_id)
        if selected is None:
            err_console.print(f"[red]Error: Workflow '{workflow_id}' not found.[/red]")
            raise typer.Exit(1)
        resolved_wf_id = selected.id

    async def _list_runs():
        from src.application.runner import execute_use_case
        from src.application.use_cases.workflow_runs import (
            ListWorkflowRunsCommand,
            ListWorkflowRunsUseCase,
        )

        user_id = get_cli_user_id()
        if resolved_wf_id is None:
            err_console.print("[red]Error: --workflow-id is required.[/red]")
            raise typer.Exit(1)
        wf_id: UUID = resolved_wf_id
        result = await execute_use_case(
            lambda uow: ListWorkflowRunsUseCase().execute(
                ListWorkflowRunsCommand(
                    user_id=user_id,
                    workflow_id=wf_id,
                    limit=limit,
                ),
                uow,
            ),
            user_id=user_id,
        )
        return result

    try:
        result = run_async(_list_runs())
    except Exception as e:
        handle_cli_error(e, "Failed to list runs")

    if not result.runs:
        console.print("[yellow]No workflow runs found.[/yellow]")
        return

    if output_format == "json":
        import json

        print(
            json.dumps(
                [
                    {
                        "id": r.id,
                        "workflow_id": r.workflow_id,
                        "status": r.status,
                        "started_at": str(r.started_at) if r.started_at else None,
                        "duration_ms": r.duration_ms,
                        "output_track_count": r.output_track_count,
                        "error_message": r.error_message,
                    }
                    for r in result.runs
                ],
                indent=2,
                default=str,
            )
        )
        return

    table = Table(
        title=f"Workflow Runs ({result.total_count} total)",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Run ID", style="cyan", no_wrap=True)
    table.add_column("Workflow", style="green")
    table.add_column("Status")
    table.add_column("Started", style="dim")
    table.add_column("Duration", justify="right")
    table.add_column("Tracks", justify="right")

    status_styles = {
        "completed": "[green]completed[/green]",
        "failed": "[red]failed[/red]",
        "running": "[yellow]running[/yellow]",
        "pending": "[dim]pending[/dim]",
    }

    for r in result.runs:
        duration = f"{r.duration_ms}ms" if r.duration_ms else "—"
        tracks = str(r.output_track_count) if r.output_track_count is not None else "—"
        started = r.started_at.strftime("%Y-%m-%d %H:%M") if r.started_at else "—"
        table.add_row(
            str(r.id),
            r.definition_snapshot.name,
            status_styles.get(r.status, r.status),
            started,
            duration,
            tracks,
        )

    console.print(table)


@app.command(name="versions")
def list_versions(
    workflow_id: Annotated[
        str,
        typer.Argument(
            help="Workflow ID (number or slug)",
            autocompletion=complete_workflow_id,
        ),
    ],
    output_format: Annotated[
        Literal["table", "json"], typer.Option("--format", "-f")
    ] = "table",
) -> None:
    """List version history of a workflow."""
    workflows = _get_available_workflows()
    selected = _resolve_workflow(workflows, workflow_id)
    if selected is None:
        err_console.print(f"[red]Error: Workflow '{workflow_id}' not found.[/red]")
        raise typer.Exit(1)

    async def _list_versions():
        from src.application.runner import execute_use_case
        from src.application.use_cases.workflow_versions import (
            ListWorkflowVersionsCommand,
            ListWorkflowVersionsUseCase,
        )

        user_id = get_cli_user_id()
        result = await execute_use_case(
            lambda uow: ListWorkflowVersionsUseCase().execute(
                ListWorkflowVersionsCommand(
                    user_id=user_id,
                    workflow_id=selected.id or 0,
                ),
                uow,
            ),
            user_id=user_id,
        )
        return result

    try:
        result = run_async(_list_versions())
    except Exception as e:
        handle_cli_error(e, "Failed to list versions")

    if not result.versions:
        console.print(
            "[yellow]No versions found (workflow has not been updated).[/yellow]"
        )
        return

    if output_format == "json":
        import json

        print(
            json.dumps(
                [
                    {
                        "version": v.version,
                        "created_at": str(v.created_at) if v.created_at else None,
                        "change_summary": v.change_summary,
                        "task_count": len(v.definition.tasks),
                    }
                    for v in result.versions
                ],
                indent=2,
                default=str,
            )
        )
        return

    table = Table(
        title=f"Versions of '{selected.definition.name}'",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Version", justify="right", style="cyan")
    table.add_column("Created", style="dim")
    table.add_column("Tasks", justify="right")
    table.add_column("Changes")

    for v in result.versions:
        created = v.created_at.strftime("%Y-%m-%d %H:%M") if v.created_at else "—"
        table.add_row(
            f"v{v.version}",
            created,
            str(len(v.definition.tasks)),
            v.change_summary or "—",
        )

    console.print(table)


@app.command(name="revert")
def revert_version(
    workflow_id: Annotated[
        str,
        typer.Argument(
            help="Workflow ID (number or slug)",
            autocompletion=complete_workflow_id,
        ),
    ],
    version: Annotated[int, typer.Argument(help="Version number to revert to")],
    output_format: Annotated[
        Literal["table", "json"], typer.Option("--format", "-f")
    ] = "table",
) -> None:
    """Revert a workflow to a previous version."""
    from rich.prompt import Confirm

    workflows = _get_available_workflows()
    selected = _resolve_workflow(workflows, workflow_id)
    if selected is None:
        err_console.print(f"[red]Error: Workflow '{workflow_id}' not found.[/red]")
        raise typer.Exit(1)

    if not Confirm.ask(
        f"Revert [bold]{selected.definition.name}[/bold] to v{version}?"
    ):
        console.print("[yellow]Revert cancelled.[/yellow]")
        return

    async def _revert():
        from src.application.runner import execute_use_case
        from src.application.use_cases.workflow_versions import (
            RevertWorkflowVersionCommand,
            RevertWorkflowVersionUseCase,
        )

        user_id = get_cli_user_id()
        result = await execute_use_case(
            lambda uow: RevertWorkflowVersionUseCase().execute(
                RevertWorkflowVersionCommand(
                    user_id=user_id,
                    workflow_id=selected.id or 0,
                    version=version,
                ),
                uow,
            ),
            user_id=user_id,
        )
        return result.workflow

    try:
        workflow = run_async(_revert())
    except Exception as e:
        handle_cli_error(e, "Failed to revert workflow")

    if output_format == "json":
        print(_serialize_workflow_json(workflow))
    else:
        console.print(
            f"[green]Reverted[/green] [bold]{workflow.definition.name}[/bold] "
            f"to v{version} (now v{workflow.definition_version})"
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _show_interactive_workflow_browser() -> None:
    """Display interactive workflow browser."""
    workflows = _get_available_workflows()

    if not workflows:
        console.print("[red]No workflows found.[/red]")
        return

    from src import __version__

    print_banner(__version__)
    console.print()
    console.print(
        brand_panel(
            "🎵 [bold]Workflow Browser[/bold]\n[dim]Discover and execute playlist transformation workflows[/dim]",
            "Mixd Workflows",
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
            from src.config.constants import WorkflowConstants, truncate_error_message

            # 1. Create PENDING run record
            user_id = get_cli_user_id()
            run_result = await execute_use_case(
                lambda uow: RunWorkflowUseCase().execute(
                    RunWorkflowCommand(
                        user_id=user_id,
                        workflow_id=workflow.id or 0,
                    ),
                    uow,
                ),
                user_id=user_id,
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
                        error_message=truncate_error_message(str(exc), 500),
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

        user_id = get_cli_user_id()
        await ensure_cli_db_ready()
        result = await execute_use_case(
            lambda uow: ListWorkflowsUseCase().execute(
                ListWorkflowsCommand(user_id=user_id, include_templates=True),
                uow,
            ),
            user_id=user_id,
        )
        return result.workflows

    try:
        return run_async(_fetch())
    except Exception as e:
        handle_cli_error(e, "Failed to load workflows")


def _read_json_input(file: Path | None) -> str:
    """Read JSON from --file path or stdin."""
    if file is not None:
        if not file.exists():
            err_console.print(f"[red]Error: File not found: {file}[/red]")
            raise typer.Exit(1)
        return file.read_text(encoding="utf-8")
    if sys.stdin.isatty():
        err_console.print(
            "[red]Error: No input. Use --file or pipe JSON via stdin.[/red]"
        )
        raise typer.Exit(1)
    return sys.stdin.read()


def _serialize_workflow_json(workflow: Workflow) -> str:
    """Serialize a Workflow entity to JSON string for CLI output."""
    import json as json_mod

    from attrs import asdict

    data = asdict(workflow)
    return json_mod.dumps(data, indent=2, default=str)


def _display_workflow_detail(workflow: Workflow) -> None:
    """Display a single workflow's full definition in Rich panels."""
    d = workflow.definition
    type_badge = (
        "[dim]template[/dim]" if workflow.is_template else "[cyan]custom[/cyan]"
    )

    header = (
        f"[bold]{d.name}[/bold] {type_badge}\n"
        f"[dim]{d.description}[/dim]\n"
        f"[cyan]DB ID: {workflow.id} | Slug: {d.id} | Version: v{workflow.definition_version} | Tasks: {len(d.tasks)}[/cyan]"
    )
    console.print(Panel(header, title="Workflow Detail", border_style="green"))

    # Task pipeline
    table = Table(
        title="Tasks",
        show_header=True,
        header_style="bold magenta",
        expand=True,
    )
    table.add_column("ID", style="green")
    table.add_column("Type")
    table.add_column("Upstream", style="dim")
    table.add_column("Config", style="dim", ratio=2)

    for task in d.tasks:
        config_summary = ", ".join(f"{k}={v!r}" for k, v in task.config.items())
        table.add_row(
            task.id,
            task.type,
            ", ".join(task.upstream) if task.upstream else "-",
            config_summary or "-",
        )

    console.print(table)
