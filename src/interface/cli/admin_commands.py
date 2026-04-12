"""Admin CLI commands — operational tooling for local/self-hosted instances.

NOTE: This module is temporary. The ``reset`` command exists to support the
v0.7.x transition where users need to re-import cleanly against the new
preference schema. Delete from the codebase once migration is complete.
"""

from rich.prompt import Confirm
import typer

from src.interface.cli.async_runner import run_async
from src.interface.cli.console import brand_status, get_console

console = get_console()

# Tables preserved across a reset. User accounts live in an external table
# managed by Neon Auth and aren't part of our SQLAlchemy metadata.
_PRESERVED_TABLES = frozenset({"oauth_tokens", "oauth_states", "user_settings"})


def _data_tables() -> list[str]:
    """All tables in our schema except user-account / auth tables.

    Derived from SQLAlchemy metadata so it auto-maintains as the schema
    evolves — no risk of forgetting a new table.
    """
    from src.infrastructure.persistence.database.db_models import DatabaseModel

    return [
        t.name
        for t in DatabaseModel.metadata.tables.values()
        if t.name not in _PRESERVED_TABLES
    ]


app = typer.Typer(
    help="Operational admin commands (temporary tooling)",
    rich_help_panel="⚙️ System",
)


@app.command(name="reset")
def reset(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Truncate all data tables for ALL users.

    Preserves user accounts and service connections so you don't need to
    re-authenticate with Spotify/Last.fm after the reset.
    """
    console.print(
        "[yellow]This will delete ALL tracks, likes, history, playlists, "
        "workflows, and preferences for ALL users.[/yellow]\n"
        "[dim]User accounts and service connections will be preserved.[/dim]"
    )

    if not yes and not Confirm.ask("Continue?", default=False):
        console.print("[dim]Aborted.[/dim]")
        raise typer.Exit(code=0)

    with brand_status("Truncating data tables..."):
        run_async(_truncate_all())

    console.print("[green]✓ Reset complete. Re-import your data to rebuild.[/green]")


async def _truncate_all() -> None:
    """Issue a single TRUNCATE CASCADE for all data tables."""
    from sqlalchemy import text

    from src.infrastructure.persistence.database.db_connection import get_session

    table_list = ", ".join(_data_tables())
    async with get_session() as session:
        await session.execute(text(f"TRUNCATE TABLE {table_list} CASCADE"))
