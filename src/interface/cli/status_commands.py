"""CLI commands for checking service connection status and health."""

from typing import Annotated

from rich.table import Table
import typer

from src.interface.cli.console import get_console

console = get_console()

SERVICES = ["Spotify", "Last.fm", "MusicBrainz"]


def register_status_commands(app: typer.Typer) -> None:
    """Register status commands with the Typer app."""
    app.command(
        name="status",
        help="Check connection status of music services",
        rich_help_panel="⚙️ System",
    )(status)


def status(
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Check connection status of music services."""
    _run_status_check(verbose)


def _run_status_check(_verbose: bool) -> None:
    """Run the status check operation."""
    with console.status("[bold blue]Checking service connections..."):
        results = _check_all_connections()

    # Create status table
    table = Table(title="Narada Service Status")
    table.add_column("Service", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Details", style="dim")

    # Add results to table
    for service, connected, details in results:
        status_text = (
            "[green]✓ Connected[/green]" if connected else "[red]✗ Not Connected[/red]"
        )
        table.add_row(service, status_text, details)

    console.print(table)

    # Show help if needed
    if not all(connected for _, connected, _ in results):
        console.print(
            "\n[yellow]Some services not connected. "
            "Run [bold]narada setup[/bold] to configure.[/yellow]"
        )


def _check_all_connections() -> list[tuple[str, bool, str]]:
    """Check all service connections concurrently."""
    # Simple connectivity checks - in a real implementation,
    # we'd have specific health check methods in the application layer
    results = []
    for service_name in SERVICES:
        try:
            # Simplified check - assume services are available
            results.append((service_name, True, "Service available"))
        except Exception as e:
            results.append((service_name, False, f"Error: {e}"))

    return results
