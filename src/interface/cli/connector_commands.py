"""CLI commands for checking connector authentication status."""

from datetime import UTC, datetime

from rich.table import Table
import typer

from src.interface.cli.async_runner import run_async
from src.interface.cli.cli_helpers import get_cli_user_id, handle_cli_error
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
        from src.infrastructure.connectors._shared.connector_status import (
            get_all_connector_statuses,
        )

        statuses = await get_all_connector_statuses(get_cli_user_id())

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


# ---------------------------------------------------------------------------
# Auth subcommands
# ---------------------------------------------------------------------------

auth_app = typer.Typer(help="Authenticate with music services")
app.add_typer(auth_app, name="auth")


@auth_app.command(name="spotify")
def auth_spotify() -> None:
    """Authenticate with Spotify via browser OAuth flow.

    Opens your browser to Spotify's authorization page. After you approve,
    the token is captured locally and stored for the current CLI user.
    """

    async def _auth():
        import asyncio

        from src.infrastructure.connectors._shared.connector_status import (
            fetch_spotify_display_name,
        )
        from src.infrastructure.connectors._shared.token_storage import (
            StoredToken,
            get_token_storage,
        )
        from src.infrastructure.connectors.spotify.auth import SpotifyTokenManager

        user_id = get_cli_user_id()
        storage = get_token_storage()
        mgr = SpotifyTokenManager(storage=storage, user_id=user_id)

        code = await asyncio.to_thread(mgr.run_browser_auth)
        token_info = await mgr.exchange_code(code)

        display_name = await fetch_spotify_display_name(token_info["access_token"])
        token_to_save = StoredToken(**token_info)
        if display_name:
            token_to_save = StoredToken(**token_info, account_name=display_name)
        await storage.save_token("spotify", user_id, token_to_save)

        return display_name

    console.print("[cyan]Opening Spotify authorization in browser...[/cyan]")
    try:
        display_name = run_async(_auth())
        if display_name:
            console.print(f"[green]Connected as {display_name}[/green]")
        else:
            console.print("[green]Spotify connected successfully.[/green]")
    except Exception as e:
        handle_cli_error(e, "Spotify authentication failed")


@auth_app.command(name="lastfm")
def auth_lastfm() -> None:
    """Authenticate with Last.fm using stored credentials.

    Uses LASTFM_USERNAME and LASTFM_PASSWORD from your environment or .env.local
    to obtain a session key via Last.fm's mobile auth API.
    """

    async def _auth():
        from src.infrastructure.connectors.lastfm.client import LastFMAPIClient

        user_id = get_cli_user_id()
        async with LastFMAPIClient(user_id=user_id) as client:
            session_key = await client.get_session_key()
            return session_key, client.lastfm_username

    try:
        _, username = run_async(_auth())
        console.print(f"[green]Last.fm connected as {username}[/green]")
    except Exception as e:
        handle_cli_error(e, "Last.fm authentication failed")
