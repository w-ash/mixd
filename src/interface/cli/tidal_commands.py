"""CLI commands for the Tidal connector (v0.11.3).

``mixd tidal auth`` authenticates via the device-code flow — the primary
CLI path, against Tidal's *undocumented* ``device_authorization`` endpoint
(backlog v0.11.3 decision) — and auto-falls back to a localhost browser
redirect when that endpoint turns out not to exist
(``DeviceCodeUnsupportedError``). ``--browser`` forces the fallback.

Both flows live in ``tidal/device_auth.py`` (the v0.6.5 credential
carve-out shape — sharing ``exchange_code`` with the web callback's
``tidal/auth.py``), and ``device_auth.run_auth`` owns the device→browser
fallback policy, so a token connected here also lights up the web
connector card. Disconnect is the generic ``mixd connectors disconnect
tidal``.

``mixd tidal snapshot`` renders the favorites snapshot use case: a
``TIDAL · N favorites`` header (Tidal's connect scope carries no identity,
so the service name stands where Discogs shows a username) and a Rich table
of recent favorites, with an inviting zero-state (not an error) for an
empty collection.
"""

import typer

from src.infrastructure.connectors._shared.token_storage import StoredToken
from src.interface.cli.async_runner import run_async
from src.interface.cli.cli_helpers import get_cli_user_id, handle_cli_error
from src.interface.cli.console import get_console

console = get_console()

app = typer.Typer(help="Connect Tidal and browse your collection")


@app.command(name="auth")
def auth(
    browser: bool = typer.Option(
        False,
        "--browser",
        help="Skip the device-code flow and authorize via a localhost "
        "browser redirect.",
    ),
) -> None:
    """Authenticate with Tidal (device code, with a browser fallback)."""
    from src.infrastructure.connectors.tidal.device_auth import (
        DeviceAuthorization,
        DeviceCodeExpiredError,
    )

    user_id = get_cli_user_id()

    def _show_verification(grant: DeviceAuthorization) -> None:
        console.print(
            f"Enter code [bold cyan]{grant.user_code}[/bold cyan] at "
            f"[bold]{grant.verification_uri}[/bold]"
        )
        if grant.verification_uri_complete:
            console.print(f"[dim]Or open: {grant.verification_uri_complete}[/dim]")
        console.print("[dim]Waiting for approval...[/dim]")

    def _show_fallback() -> None:
        console.print(
            "[yellow]Tidal's device-code endpoint is unavailable — "
            "falling back to the browser flow.[/yellow]"
        )
        console.print("[cyan]Opening Tidal authorization in browser...[/cyan]")

    async def _auth() -> StoredToken:
        from src.infrastructure.connectors._shared.token_storage import (
            get_token_storage,
        )
        from src.infrastructure.connectors.tidal import device_auth

        return await device_auth.run_auth(
            get_token_storage(),
            user_id,
            prefer_browser=browser,
            on_verification=_show_verification,
            on_fallback=_show_fallback,
        )

    try:
        if browser:
            console.print("[cyan]Opening Tidal authorization in browser...[/cyan]")
        _ = run_async(_auth())
    except DeviceCodeExpiredError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from None
    except Exception as e:
        handle_cli_error(e, "Tidal authentication failed")

    console.print("[green]Tidal connected.[/green]")


@app.command(name="snapshot")
def snapshot(
    limit: int = typer.Option(
        10,
        "--limit",
        min=1,
        max=25,
        help="How many recent favorites to show (each costs one track lookup).",
    ),
) -> None:
    """Show your Tidal favorites: total count and recent additions."""
    from src.application.use_cases.get_tidal_snapshot import GetTidalSnapshotResult
    from src.domain.exceptions import TidalAuthRequiredError

    async def _snapshot() -> GetTidalSnapshotResult:
        from src.application.use_cases import get_tidal_snapshot

        return await get_tidal_snapshot.run_get_tidal_snapshot(
            get_cli_user_id(), recent_limit=limit
        )

    try:
        result = run_async(_snapshot())
    except TidalAuthRequiredError as e:
        console.print(f"[red]{e}[/red]")
        console.print("[dim]Connect first: mixd tidal auth[/dim]")
        raise typer.Exit(1) from None
    except Exception as e:
        handle_cli_error(e, "Failed to fetch the Tidal snapshot")

    console.print(f"[bold]TIDAL[/bold] · {result.total_items:,} favorites")

    if result.total_items == 0:
        console.print(
            "You have no Tidal favorites yet. Heart some tracks in the "
            "Tidal app — mixd will pick them up here."
        )
        return

    from rich.table import Table

    table = Table(title="Recent Favorites")
    table.add_column("Title", style="cyan")
    table.add_column("Artists", style="green")
    table.add_column("Added", style="dim")

    for item in result.recent:
        table.add_row(item.title, item.artists, item.added_at or "—")

    console.print(table)
