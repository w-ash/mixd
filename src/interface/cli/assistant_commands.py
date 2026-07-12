"""CLI commands for the AI assistant credential (v0.9.0.1 BYO-key).

``mixd assistant connect|test|disconnect`` manage the current CLI user's own
Anthropic API key. The key is validated live with a minimal completion (which
also catches a key with no billing) and stored encrypted in the same per-user
token store the web UI uses (the v0.6.5 shared-token-access architecture), so a
key connected here also lights up the web chat panel for that user, and vice versa.
"""

from rich.table import Table
import typer

from src.interface.cli.async_runner import run_async
from src.interface.cli.cli_helpers import get_cli_user_id, handle_cli_error
from src.interface.cli.console import get_console

console = get_console()

app = typer.Typer(
    help="Connect the AI assistant with your Anthropic API key",
    no_args_is_help=False,
    invoke_without_command=True,
)

_CONSOLE_HINT = (
    "Create a key at https://console.anthropic.com (Settings → Billing must have "
    "a payment method, then API Keys → Create Key)."
)


@app.callback(invoke_without_command=True)
def assistant_status(ctx: typer.Context) -> None:
    """Show whether the AI assistant is connected for the current user."""
    if ctx.invoked_subcommand is not None:
        return

    async def _status_async() -> tuple[bool, str]:
        from src.infrastructure.chat.credentials import resolve_chat_credential

        resolved = await resolve_chat_credential(get_cli_user_id())
        if resolved is None:
            return False, "—"
        label = "your Anthropic key" if resolved[1] == "user" else "server fallback key"
        return True, label

    try:
        connected, source = run_async(_status_async())
    except Exception as e:
        handle_cli_error(e, "Failed to check assistant status")

    table = Table(title="AI Assistant")
    table.add_column("Status")
    table.add_column("Source", style="dim")
    status_str = "[green]Connected[/green]" if connected else "[red]Not connected[/red]"
    table.add_row(status_str, source)
    console.print(table)
    if not connected:
        console.print(f"[dim]{_CONSOLE_HINT}[/dim]")
        console.print("Run [cyan]mixd assistant connect[/cyan] to add your key.")


@app.command(name="connect")
def connect(
    key: str = typer.Option(
        None,
        "--key",
        help="Anthropic API key (sk-ant-…). Omit to be prompted securely.",
    ),
) -> None:
    """Validate and store your Anthropic API key for the assistant."""
    api_key = key or typer.prompt("Anthropic API key", hide_input=True)
    api_key = api_key.strip()

    async def _connect() -> bool:
        from src.infrastructure.chat.anthropic_adapter import validate_anthropic_key
        from src.infrastructure.chat.credentials import (
            looks_like_anthropic_key,
            save_user_anthropic_key,
        )

        if not looks_like_anthropic_key(api_key):
            return False
        if not await validate_anthropic_key(api_key):
            return False
        await save_user_anthropic_key(get_cli_user_id(), api_key)
        return True

    try:
        ok = run_async(_connect())
    except Exception as e:
        handle_cli_error(e, "Failed to connect the assistant")

    if ok:
        console.print("[green]AI assistant connected.[/green]")
    else:
        console.print(
            "[red]That key was rejected.[/red] Check it was copied in full and "
            "that billing is set up."
        )
        console.print(f"[dim]{_CONSOLE_HINT}[/dim]")
        raise typer.Exit(1)


@app.command(name="test")
def test() -> None:
    """Test the stored Anthropic API key with a minimal live completion."""

    async def _test() -> bool | None:
        from src.infrastructure.chat.anthropic_adapter import validate_anthropic_key
        from src.infrastructure.chat.credentials import load_user_anthropic_key

        stored = await load_user_anthropic_key(get_cli_user_id())
        if not stored:
            return None
        return await validate_anthropic_key(stored)

    try:
        result = run_async(_test())
    except Exception as e:
        handle_cli_error(e, "Failed to test the assistant key")

    if result is None:
        console.print(
            "[yellow]No API key stored.[/yellow] Run `mixd assistant connect`."
        )
        raise typer.Exit(1)
    if result:
        console.print("[green]✓ Key is valid.[/green]")
    else:
        console.print("[red]✗ Anthropic rejected the stored key.[/red]")
        raise typer.Exit(1)


@app.command(name="disconnect")
def disconnect() -> None:
    """Remove the stored Anthropic API key for the current user."""

    async def _disconnect() -> None:
        from src.infrastructure.chat.credentials import delete_user_anthropic_key

        await delete_user_anthropic_key(get_cli_user_id())

    try:
        run_async(_disconnect())
    except Exception as e:
        handle_cli_error(e, "Failed to disconnect the assistant")

    console.print("[green]AI assistant disconnected.[/green]")
