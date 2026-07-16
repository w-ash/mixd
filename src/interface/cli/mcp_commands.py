"""``mixd mcp`` — expose mixd's tools to MCP clients over stdio.

``serve`` runs the stateless stdio MCP server (the second consumer of the shared
tool registry); ``install`` prints the client config snippet. Identity comes from
the environment (``MIXD_USER_ID`` → ``DEFAULT_USER_ID``), matching every other
CLI command — no new credential surface. See ``docs/guides/mcp.md``.
"""

import contextlib
import json
from typing import Annotated

from rich.syntax import Syntax
import typer

from src.interface.cli.async_runner import run_async
from src.interface.cli.cli_helpers import get_cli_user_id
from src.interface.cli.console import get_console
from src.interface.mcp.install import (
    CLIENTS,
    SUPPORTED_CLIENTS,
    build_client_config,
    build_remote_client_config,
    client_location,
    remote_client_location,
)

app = typer.Typer(help="Expose mixd to MCP clients (Claude Desktop, Cursor, …)")

console = get_console()


@app.command(name="serve")
def serve() -> None:
    """Run the mixd MCP server over stdio.

    Intended to be launched by an MCP client as a subprocess, not run by hand.
    stdout carries the JSON-RPC protocol; the CLI callback has already routed all
    logging to stderr + the log file for this command so it never corrupts the
    stream.
    """
    user_id = get_cli_user_id()
    from src.interface.mcp.server import serve_stdio

    # Client-driven shutdown (the MCP client kills the subprocess) surfaces as
    # KeyboardInterrupt/EOF — exit quietly rather than dumping a traceback.
    with contextlib.suppress(KeyboardInterrupt):
        run_async(serve_stdio(user_id))


@app.command(name="install")
def install(
    client: Annotated[
        str,
        typer.Option(help=f"Target MCP client ({', '.join(SUPPORTED_CLIENTS)})."),
    ] = "claude-desktop",
    print_json: Annotated[
        bool,
        typer.Option("--print", help="Emit raw JSON to stdout only (no guidance)."),
    ] = False,
    remote: Annotated[
        bool,
        typer.Option(
            "--remote",
            help="Wire the client to the hosted server (HTTP + OAuth) instead "
            "of a local stdio subprocess.",
        ),
    ] = False,
    url: Annotated[
        str,
        typer.Option(
            help="Hosted MCP server URL for --remote (defaults to the "
            "configured MCP_RESOURCE_URI).",
        ),
    ] = "",
) -> None:
    """Print the config snippet to register mixd with an MCP client."""
    if client not in SUPPORTED_CLIENTS:
        raise typer.BadParameter(
            f"'{client}' is not supported — expected one of: "
            f"{', '.join(SUPPORTED_CLIENTS)}."
        )
    if remote:
        _install_remote(client, url, print_json=print_json)
        return

    user_id = get_cli_user_id()
    config = build_client_config(user_id)
    payload = json.dumps(config, indent=2)

    if print_json:
        # Pipe-friendly: raw JSON on stdout, nothing else, no file written.
        typer.echo(payload)
        return

    label = CLIENTS[client][0]
    location = client_location(client, user_id)
    console.print(f"[bold]Register mixd with {label}[/bold]")
    console.print(f"[dim]Config location:[/dim] {location}")
    console.print(Syntax(payload, "json", theme="ansi_dark"))
    console.print(
        "Merge the [bold]mcpServers[/bold] block above into that config "
        "(preserving any existing servers), then restart the client."
    )


def _install_remote(client: str, url: str, *, print_json: bool) -> None:
    """The ``--remote`` variant: hosted URL + OAuth consent, no local process."""
    from src.config import settings

    server_url = url or settings.mcp_oauth.resource_uri
    config = build_remote_client_config(server_url)
    payload = json.dumps(config, indent=2)

    if print_json:
        typer.echo(payload)
        return

    label = CLIENTS[client][0]
    console.print(f"[bold]Connect {label} to your hosted mixd[/bold]")
    console.print(f"[dim]Server URL:[/dim] {server_url}")
    console.print(f"[dim]How:[/dim] {remote_client_location(client, server_url)}")
    if client == "cursor":
        console.print(Syntax(payload, "json", theme="ansi_dark"))
        console.print(
            "Merge the [bold]mcpServers[/bold] block above into that config "
            "(preserving any existing servers), then restart the client."
        )
    console.print(
        "On first use the client opens your browser for a one-time consent — "
        "approve it on the mixd account whose library the agent should act on."
    )
