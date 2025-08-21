"""CLI commands for playlist workflow execution and management."""

import asyncio
import json
from pathlib import Path
from typing import Annotated

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
import typer

from src.interface.shared.ui import display_operation_result

console = Console()

# Create playlist subcommand app
app = typer.Typer(help="Create and manage playlists")


@app.callback(invoke_without_command=True)
def playlist_main(ctx: typer.Context) -> None:
    """Create and manage playlists."""
    if ctx.invoked_subcommand is None:
        _show_interactive_workflow_menu()


@app.command()
def run(
    workflow_id: Annotated[
        str | None, typer.Argument(help="Workflow ID to execute")
    ] = None,
    show_results: Annotated[bool, typer.Option("--show-results/--no-results")] = True,
    output_format: Annotated[str, typer.Option("--format", "-f")] = "table",
) -> None:
    """Run a workflow from available definitions."""
    _run_workflow(workflow_id, show_results, output_format)


@app.command()
def list() -> None:
    """List available workflows."""
    _list_workflows()


@app.command()
def backup(
    connector: str = typer.Argument(..., help="Connector name (e.g., 'spotify')"),
    playlist_id: str = typer.Argument(
        ..., help="Playlist ID from the connector service"
    ),
) -> None:
    """Backup a playlist from a music service to your local database.

    Downloads a playlist from the specified connector (Spotify, etc.) and saves it
    to your local database. If the playlist already exists locally, it will be updated
    with the latest tracks and metadata from the service.

    Examples:
        narada playlist backup spotify 37i9dQZF1DX0XUsuxWHRQd
        narada playlist backup spotify 1A2B3C4D5E6F7G8H9I0J1K
    """
    asyncio.run(_backup_playlist_async(connector, playlist_id))


def _show_interactive_workflow_menu() -> None:
    """Display interactive workflow selection menu."""
    workflows = _get_available_workflows()

    if not workflows:
        console.print("[red]No workflows found.[/red]")
        return

    console.print(
        Panel.fit(
            "🎵 Available Workflows",
            title="[bold blue]Narada Playlist[/bold blue]",
            border_style="blue",
        )
    )

    for i, wf in enumerate(workflows, 1):
        console.print(
            f"  [cyan]{i}[/cyan]. [bold]{wf['id']}[/bold] - {wf['name']} ([dim]{wf['task_count']} tasks[/dim])"
        )

    choices = [str(i) for i in range(1, len(workflows) + 1)]
    choices.extend([wf["id"] for wf in workflows])
    choices.extend(["q", "quit", "exit", "cancel"])

    choice = Prompt.ask(
        f"Select workflow [1-{len(workflows)}] or type name",
        choices=choices,
        default="",
        show_choices=False,
    ).strip()

    if choice in ("", "q", "quit", "exit", "cancel"):
        return

    # Parse selection
    workflow_id = None
    if choice.isdigit():
        choice_num = int(choice)
        if 1 <= choice_num <= len(workflows):
            workflow_id = workflows[choice_num - 1]["id"]
    else:
        workflow_id = choice

    if workflow_id:
        console.print(f"\n[green]Running workflow:[/green] [bold]{workflow_id}[/bold]")
        _run_workflow(workflow_id, show_results=True, output_format="table")


def _run_workflow(
    workflow_id: str | None, show_results: bool, output_format: str
) -> None:
    """Execute a workflow."""
    workflows = _get_available_workflows()

    if not workflow_id:
        console.print("[red]No workflow specified.[/red]")
        return

    # Find workflow
    workflow_info = next((wf for wf in workflows if wf["id"] == workflow_id), None)
    if not workflow_info:
        console.print(f"[red]Workflow '{workflow_id}' not found.[/red]")
        return

    try:
        # Load and execute workflow
        workflow_path = Path(workflow_info["path"])
        workflow_def = json.loads(workflow_path.read_text(encoding="utf-8"))

        console.print(
            Panel.fit(
                f"[bold]{workflow_info['name']}[/bold]\n"
                f"[dim]{workflow_info['description']}[/dim]\n"
                f"[cyan]Tasks: [bold]{workflow_info.get('task_count', 0)}[/bold][/cyan]",
                title="[bold bright_blue]⚡ Running Workflow[/bold bright_blue]",
                border_style="blue",
            )
        )

        with console.status("[bold blue]Executing workflow..."):
            # Lazy import to avoid startup dependency issues
            from src.application.workflows.prefect import (
                run_workflow as execute_workflow,
            )

            _, result = asyncio.run(execute_workflow(workflow_def))

        console.print(
            Panel.fit(
                f"[bold green]{workflow_info['name']}[/bold green]\n"
                f"[cyan]Processed [bold]{len(result.tracks) if result and hasattr(result, 'tracks') else 0}[/bold] tracks[/cyan]",
                title="[bold green]✓ Workflow Completed[/bold green]",
                border_style="green",
            )
        )

        if show_results and result:
            display_operation_result(result, output_format=output_format)

    except Exception as e:
        console.print("[bold red]✗ Workflow failed[/bold red]")
        console.print(f"[red]Error: {e}[/red]")


def _list_workflows() -> None:
    """Display available workflows."""
    workflows = _get_available_workflows()

    if not workflows:
        console.print("[red]No workflows found.[/red]")
        return

    table = Table(title="Available Workflows")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Description", style="dim")
    table.add_column("Tasks", style="yellow", justify="right")

    for wf in workflows:
        table.add_row(wf["id"], wf["name"], wf["description"], str(wf["task_count"]))

    console.print(table)


def _get_available_workflows():
    """Get available workflow definitions."""
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


async def _backup_playlist_async(connector_name: str, playlist_id: str) -> None:
    """Backup a playlist from a connector service to the local database."""
    # Import here to avoid circular dependencies
    from src.application.services.playlist_backup_service import run_playlist_backup

    console.print(
        Panel.fit(
            f"[bold]{connector_name.title()} Playlist Backup[/bold]\n"
            f"[dim]Playlist ID: {playlist_id}[/dim]",
            title="[bold bright_blue]🎵 Starting Backup[/bold bright_blue]",
            border_style="blue",
        )
    )

    try:
        with console.status(f"[bold blue]Backing up playlist from {connector_name}..."):
            result = await run_playlist_backup(
                connector_name=connector_name, playlist_id=playlist_id
            )

        # Display results based on result type
        from src.application.use_cases.update_canonical_playlist import (
            UpdateCanonicalPlaylistResult,
        )

        if isinstance(result, UpdateCanonicalPlaylistResult):
            # Updated existing playlist
            console.print(
                Panel.fit(
                    f"[bold green]✓ Playlist Updated[/bold green]\n"
                    f"[cyan]Name:[/cyan] {result.playlist.name}\n"
                    f"[cyan]Tracks:[/cyan] {len(result.playlist.tracks)}\n"
                    f"[cyan]Operations:[/cyan] {result.operations_performed} changes\n"
                    f"[cyan]Added:[/cyan] {result.tracks_added}, [cyan]Removed:[/cyan] {result.tracks_removed}",
                    title="[bold green]🎵 Backup Complete[/bold green]",
                    border_style="green",
                )
            )
        else:
            # Created new playlist
            console.print(
                Panel.fit(
                    f"[bold green]✓ Playlist Created[/bold green]\n"
                    f"[cyan]Name:[/cyan] {result.playlist.name}\n"
                    f"[cyan]ID:[/cyan] {result.playlist.id}\n"
                    f"[cyan]Tracks:[/cyan] {len(result.playlist.tracks)}\n"
                    f"[cyan]New tracks saved:[/cyan] {result.tracks_created}",
                    title="[bold green]🎵 Backup Complete[/bold green]",
                    border_style="green",
                )
            )

    except ValueError as e:
        console.print(f"❌ [red]Error: {e}[/red]")
        raise typer.Exit(1) from e
    except Exception as e:
        console.print(f"❌ [bold red]Backup failed:[/bold red] {e}")
        raise typer.Exit(1) from e
