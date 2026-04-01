"""CLI commands for checking connector authentication status."""

from datetime import UTC, datetime

from rich.table import Table
import typer

from src.interface.cli.async_runner import run_async
from src.interface.cli.cli_helpers import handle_cli_error
from src.interface.cli.console import get_console

console = get_console()

app = typer.Typer(
    help="Manage music service connectors",
    no_args_is_help=False,
    invoke_without_command=True,
)


@app.callback(invoke_without_command=True)
def connectors_status(ctx: typer.Context) -> None:
    """Show authentication status of all configured connectors."""
    if ctx.invoked_subcommand is not None:
        return

    async def _status_async():
        from src.config.constants import BusinessLimits
        from src.infrastructure.connectors._shared.connector_status import (
            get_all_connector_statuses,
        )

        statuses = await get_all_connector_statuses(BusinessLimits.DEFAULT_USER_ID)

        table = Table(title="Connector Status")
        table.add_column("Name", style="cyan")
        table.add_column("Status")
        table.add_column("Account", style="dim")
        table.add_column("Token Expiry", style="dim")

        for s in statuses:
            status_str = (
                "[green]Connected[/green]" if s.connected else "[red]Disconnected[/red]"
            )
            expiry_str = "—"
            if s.token_expires_at:
                expiry_dt = datetime.fromtimestamp(s.token_expires_at, tz=UTC)
                expiry_str = expiry_dt.strftime("%Y-%m-%d %H:%M UTC")

            table.add_row(
                s.name,
                status_str,
                s.account_name or "—",
                expiry_str,
            )

        console.print(table)

    try:
        run_async(_status_async())
    except Exception as e:
        handle_cli_error(e, "Failed to check connector status")
