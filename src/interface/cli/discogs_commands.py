"""CLI commands for the Discogs connector (v0.11.1 BYO personal access token).

``mixd discogs connect`` validates the token live through the shared
``discogs/token_service`` (the same validator the web route uses — the
v0.6.5 credential carve-out shape) and stores it encrypted in the per-user
token store, so a token connected here also lights up the web connector
card, and vice versa. Disconnect is the generic
``mixd connectors disconnect discogs``.

``mixd discogs snapshot`` renders the collection snapshot use case: a
``{username} · N releases`` header and a Rich table of recent additions,
with an inviting zero-state (not an error) for an empty collection.
"""

import typer

from src.infrastructure.connectors._shared.token_storage import StoredToken
from src.interface.cli.async_runner import run_async
from src.interface.cli.cli_helpers import get_cli_user_id, handle_cli_error
from src.interface.cli.console import get_console

console = get_console()

app = typer.Typer(help="Connect Discogs and browse your record collection")

_CONNECT_HINT = (
    "Generate a personal access token at "
    "https://www.discogs.com/settings/developers (Generate new token)."
)


@app.command(name="connect")
def connect(
    token: str = typer.Option(
        None,
        "--token",
        help="Discogs personal access token. Omit to be prompted securely.",
    ),
) -> None:
    """Validate and store your Discogs personal access token."""
    from src.domain.exceptions import DiscogsAuthRequiredError

    access_token = (
        token or typer.prompt("Discogs personal access token", hide_input=True)
    ).strip()

    async def _connect() -> StoredToken:
        from src.infrastructure.connectors._shared.token_storage import (
            get_token_storage,
        )
        from src.infrastructure.connectors.discogs import token_service

        stored = await token_service.validate_and_build_token(access_token)
        await get_token_storage().save_token("discogs", get_cli_user_id(), stored)
        return stored

    try:
        stored = run_async(_connect())
    except DiscogsAuthRequiredError as e:
        console.print(f"[red]{e}[/red]")
        console.print(f"[dim]{_CONNECT_HINT}[/dim]")
        raise typer.Exit(1) from None
    except Exception as e:
        handle_cli_error(e, "Failed to connect Discogs")

    name = stored.get("account_name") or "your account"
    count = (stored.get("extra_data") or {}).get("collection_count")
    suffix = f" · {count:,} releases" if isinstance(count, int) else ""
    console.print(f"[green]Discogs connected as {name}{suffix}.[/green]")


@app.command(name="snapshot")
def snapshot(
    limit: int = typer.Option(
        10,
        "--limit",
        min=1,
        max=100,
        help="How many recent additions to show.",
    ),
) -> None:
    """Show your Discogs collection: total count and recent additions."""
    from src.application.use_cases.get_discogs_snapshot import (
        GetDiscogsSnapshotResult,
    )
    from src.domain.exceptions import DiscogsAuthRequiredError

    async def _snapshot() -> GetDiscogsSnapshotResult:
        from src.application.use_cases import get_discogs_snapshot

        return await get_discogs_snapshot.run_get_discogs_snapshot(
            get_cli_user_id(), recent_limit=limit
        )

    try:
        result = run_async(_snapshot())
    except DiscogsAuthRequiredError as e:
        console.print(f"[red]{e}[/red]")
        console.print("[dim]Connect first: mixd discogs connect[/dim]")
        raise typer.Exit(1) from None
    except Exception as e:
        handle_cli_error(e, "Failed to fetch the Discogs snapshot")

    console.print(f"[bold]{result.username}[/bold] · {result.total_items:,} releases")

    if result.total_items == 0:
        console.print(
            "Your Discogs collection is [bold]empty[/bold]. Start cataloguing "
            "at discogs.com — mixd will pick it up here."
        )
        return

    from rich.table import Table

    table = Table(title="Recent Additions")
    table.add_column("Title", style="cyan")
    table.add_column("Artists", style="green")
    table.add_column("Year", justify="right")
    table.add_column("Format", style="dim")
    table.add_column("Added", style="dim")

    for item in result.recent:
        table.add_row(
            item.title,
            item.artists,
            str(item.year) if item.year is not None else "—",
            item.formats,
            item.date_added,
        )

    console.print(table)
