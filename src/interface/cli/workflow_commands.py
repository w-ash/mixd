"""CLI commands for workflow execution and management following 2025 best practices.

Modern Typer implementation with progressive discovery, Rich UI, and clean separation
of concerns. Follows [tool] [noun] [verb] command patterns for consistency.
"""

from collections.abc import Sequence
import json
from pathlib import Path
from typing import Annotated, Any, Literal

from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
import typer

from src.interface.cli.async_runner import run_async
from src.interface.cli.cli_helpers import get_workflow_definitions_path
from src.interface.cli.console import (
    get_console,
    get_error_console,
    progress_coordination_context,
)
from src.interface.cli.ui import display_operation_result

console = get_console()
err_console = get_error_console()

# Create workflow app following 2025 Typer patterns
app = typer.Typer(
    help="Execute and manage playlist workflows",
    no_args_is_help=False,  # Allow bare command for interactive discovery
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def workflow_main(ctx: typer.Context) -> None:
    """Interactive workflow browser with progressive discovery.

    Provides guided discovery for new users while maintaining direct access
    for automation. Follows human-first design principles.
    """
    if ctx.invoked_subcommand is None:
        _show_interactive_workflow_browser()


@app.command()
def run(
    workflow_id: Annotated[
        str | None, typer.Argument(help="Workflow ID to execute")
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
        # Progressive discovery: show list and prompt
        _display_workflows_table(workflows)
        workflow_id = _prompt_for_workflow_selection(workflows)
        if workflow_id is None:
            return

    workflow_info = next((wf for wf in workflows if wf["id"] == workflow_id), None)
    if not workflow_info:
        err_console.print(f"[red]Error: Workflow '{workflow_id}' not found.[/red]")
        raise typer.Exit(1)

    _execute_workflow(workflow_info, show_results, output_format, quiet)


@app.command(name="list")
def list_workflows(
    format: Annotated[
        Literal["table", "json"], typer.Option("--format", "-f")
    ] = "table",
) -> None:
    """List available workflow definitions.

    Supports table and JSON output formats for both human and machine consumption.
    """
    workflows = _get_available_workflows()

    if not workflows:
        console.print("[red]No workflows found.[/red]")
        return

    if format == "json":
        # Machine-readable output for automation
        import json

        print(json.dumps(workflows, indent=2))
    else:
        # Human-readable table
        _display_workflows_table(workflows)


def _show_interactive_workflow_browser() -> None:
    """Display interactive workflow browser.

    Implements 2025 UX patterns: progressive discovery, smart defaults,
    and Rich visual hierarchy.
    """
    workflows = _get_available_workflows()

    if not workflows:
        console.print("[red]No workflows found.[/red]")
        return

    console.print(
        Panel.fit(
            "🎵 [bold]Workflow Browser[/bold]\n[dim]Discover and execute playlist transformation workflows[/dim]",
            title="[bold bright_blue]⚡ Narada Workflows[/bold bright_blue]",
            border_style="blue",
        )
    )

    _display_workflows_table(workflows)

    workflow_id = _prompt_for_workflow_selection(workflows)
    if workflow_id:
        workflow_info = next((wf for wf in workflows if wf["id"] == workflow_id), None)
        if not workflow_info:
            err_console.print(f"[red]Error: Workflow '{workflow_id}' not found.[/red]")
            return
        console.print(
            f"\n[green]Executing workflow:[/green] [bold]{workflow_id}[/bold]"
        )
        _execute_workflow(
            workflow_info, show_results=True, output_format="table", quiet=False
        )


def _prompt_for_workflow_selection(workflows: Sequence[dict[str, Any]]) -> str | None:
    """Enhanced workflow selection with fuzzy matching support."""
    # Build choices: numbers, IDs, and common exit terms
    choices: list[str] = []
    id_map: dict[str, str] = {}

    for i, wf in enumerate(workflows, 1):
        choices.extend((str(i), wf["id"]))
        id_map[str(i)] = wf["id"]

    choices.extend(["q", "quit", "exit", "cancel"])

    choice = Prompt.ask(
        f"\n[bold]Select workflow[/bold] [dim](1-{len(workflows)} or workflow ID)[/dim]",
        choices=choices,
        default="",
        show_choices=False,
    ).strip()

    if choice in ("", "q", "quit", "exit", "cancel"):
        return None

    # Parse selection: number or direct ID
    if choice.isdigit():
        return id_map.get(choice)
    else:
        return choice if choice in [wf["id"] for wf in workflows] else None


def _execute_workflow(
    workflow_info: dict[str, Any],
    show_results: bool,
    output_format: Literal["table", "json"],
    quiet: bool,
) -> None:
    """Execute workflow with Rich progress display and error handling."""
    try:
        # Load workflow definition
        workflow_path = Path(workflow_info["path"])
        workflow_def = json.loads(workflow_path.read_text(encoding="utf-8"))

        if not quiet:
            console.print(
                Panel.fit(
                    f"[bold]{workflow_info['name']}[/bold]\n[dim]{workflow_info['description']}[/dim]\n[cyan]Tasks: [bold]{workflow_info.get('task_count', 0)}[/bold][/cyan]",
                    title="[bold bright_blue]⚡ Executing Workflow[/bold bright_blue]",
                    border_style="blue",
                )
            )

        # Execute with progress coordination
        async def _run_with_progress():
            async with progress_coordination_context(show_live=not quiet) as context:
                progress_manager = context.get_progress_manager()

                # Lazy import to avoid startup dependency issues
                from src.application.workflows.prefect import (
                    run_workflow as execute_workflow,
                )

                return await execute_workflow(workflow_def, progress_manager)

        _, result = run_async(_run_with_progress())

        if not quiet:
            track_count = (
                len(result.tracks) if result and hasattr(result, "tracks") else 0
            )
            console.print(
                Panel.fit(
                    f"[bold green]{workflow_info['name']}[/bold green]\n[cyan]Processed [bold]{track_count}[/bold] tracks[/cyan]",
                    title="[bold green]✓ Workflow Completed[/bold green]",
                    border_style="green",
                )
            )

        if show_results and result:
            display_operation_result(result, output_format=output_format)

    except Exception as e:
        if not quiet:
            err_console.print(f"[red]Error: Workflow execution failed: {e}[/red]")
        raise typer.Exit(1) from e


def _display_workflows_table(workflows: Sequence[dict[str, Any]]) -> None:
    """Display workflows in a Rich table for reference."""
    table = Table(
        title="Available Workflows",
        show_header=True,
        header_style="bold magenta",
        expand=True,
        width=None,
        leading=1,
    )
    table.add_column("#", min_width=3, max_width=3)
    table.add_column("ID", style="cyan", ratio=1)
    table.add_column("Name", style="green", ratio=1)
    table.add_column("Description", style="dim", ratio=2)
    table.add_column("Tasks", justify="right", min_width=6, max_width=8)

    for i, wf in enumerate(workflows, 1):
        table.add_row(
            str(i),
            wf["id"],
            wf["name"],
            wf["description"],
            str(wf["task_count"]),
        )

    console.print(table)


def _get_available_workflows() -> list[dict[str, Any]]:
    """Get available workflow definitions with metadata.

    Returns list of workflow info dictionaries with id, name, description,
    task_count, and path fields.
    """
    definitions_path = get_workflow_definitions_path()
    workflows: list[dict[str, Any]] = []

    if not definitions_path.exists():
        return workflows

    for json_file in definitions_path.glob("*.json"):
        try:
            definition = json.loads(json_file.read_text())
            workflows.append({
                "id": definition.get("id", json_file.stem),
                "name": definition.get("name", "Unknown"),
                "description": definition.get("description", ""),
                "task_count": len(definition.get("tasks", [])),
                "path": str(json_file),
            })
        except (OSError, json.JSONDecodeError) as e:
            console.print(
                f"[yellow]Warning: Could not parse {json_file.name}: {e}[/yellow]"
            )
            continue

    return workflows
