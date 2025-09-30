"""CLI commands for workflow execution and management following 2025 best practices.

Modern Typer implementation with progressive discovery, Rich UI, and clean separation
of concerns. Follows [tool] [noun] [verb] command patterns for consistency.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
import typer

from src.interface.cli.console import get_console, progress_coordination_context
from src.interface.shared.ui import display_operation_result

console = get_console()

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
    output_format: Annotated[str, typer.Option("--format", "-f")] = "table",
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
) -> None:
    """Execute a specific workflow.

    Supports both direct execution (automation-friendly) and interactive
    selection when no workflow_id provided (progressive discovery).
    """
    if workflow_id is None:
        # Progressive discovery: show list and prompt
        workflows = _get_available_workflows()
        if not workflows:
            console.print("[red]No workflows found.[/red]")
            raise typer.Exit(1)

        _display_workflows_table(workflows)
        workflow_id = _prompt_for_workflow_selection(workflows)
        if workflow_id is None:
            return

    _execute_workflow(workflow_id, show_results, output_format, quiet)


@app.command()
def list(
    format: Annotated[str, typer.Option("--format", "-f")] = "table",
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
            "🎵 [bold]Workflow Browser[/bold]\n"
            "[dim]Discover and execute playlist transformation workflows[/dim]",
            title="[bold bright_blue]⚡ Narada Workflows[/bold bright_blue]",
            border_style="blue",
        )
    )

    _display_workflows_table(workflows)

    workflow_id = _prompt_for_workflow_selection(workflows)
    if workflow_id:
        console.print(
            f"\n[green]Executing workflow:[/green] [bold]{workflow_id}[/bold]"
        )
        _execute_workflow(
            workflow_id, show_results=True, output_format="table", quiet=False
        )


def _prompt_for_workflow_selection(workflows: list[dict]) -> str | None:  # type: ignore[misc]
    """Enhanced workflow selection with fuzzy matching support."""
    # Build choices: numbers, IDs, and common exit terms
    choices = []
    id_map = {}

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
    workflow_id: str, show_results: bool, output_format: str, quiet: bool
) -> None:
    """Execute workflow with Rich progress display and error handling."""
    workflows = _get_available_workflows()

    # Find workflow
    workflow_info = next((wf for wf in workflows if wf["id"] == workflow_id), None)
    if not workflow_info:
        typer.echo(f"Error: Workflow '{workflow_id}' not found.", err=True)
        raise typer.Exit(1)

    try:
        # Load workflow definition
        workflow_path = Path(workflow_info["path"])
        workflow_def = json.loads(workflow_path.read_text(encoding="utf-8"))

        if not quiet:
            console.print(
                Panel.fit(
                    f"[bold]{workflow_info['name']}[/bold]\n"
                    f"[dim]{workflow_info['description']}[/dim]\n"
                    f"[cyan]Tasks: [bold]{workflow_info.get('task_count', 0)}[/bold][/cyan]",
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

        _, result = asyncio.run(_run_with_progress())

        if not quiet:
            track_count = (
                len(result.tracks) if result and hasattr(result, "tracks") else 0
            )
            console.print(
                Panel.fit(
                    f"[bold green]{workflow_info['name']}[/bold green]\n"
                    f"[cyan]Processed [bold]{track_count}[/bold] tracks[/cyan]",
                    title="[bold green]✓ Workflow Completed[/bold green]",
                    border_style="green",
                )
            )

        if show_results and result:
            display_operation_result(result, output_format=output_format)

    except Exception as e:
        if not quiet:
            typer.echo(f"Error: Workflow execution failed: {e}", err=True)
        raise typer.Exit(1) from e


def _display_workflows_table(workflows: list[dict]) -> None:  # type: ignore[misc]
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


def _get_available_workflows() -> list[dict]:  # type: ignore[misc]
    """Get available workflow definitions with metadata.

    Returns list of workflow info dictionaries with id, name, description,
    task_count, and path fields.
    """
    # Get path to workflow definitions directory
    current_file = Path(__file__)
    definitions_path = (
        current_file.parent.parent.parent / "application" / "workflows" / "definitions"
    )
    workflows = []

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
