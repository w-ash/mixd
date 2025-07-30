"""CLI commands for service authentication and configuration setup."""

from rich.console import Console
import typer

console = Console()


def register_setup_commands(app: typer.Typer) -> None:
    """Register setup commands with the Typer app."""
    app.command(
        name="setup",
        help="Configure Narada services and API credentials",
        rich_help_panel="⚙️ System",
    )(setup)


def setup() -> None:
    """Configure Narada services and API credentials."""
    console.print(
        "[bold blue]🎵 Narada Setup[/bold blue]\n"
        "Configure your music service connections and API credentials.\n"
    )

    console.print(
        "[yellow]Setup functionality not yet implemented in Interface Layer.[/yellow]\n"
        "[dim]This command will be enhanced to configure:\n"
        "- Spotify API credentials\n"
        "- Last.fm API credentials\n"
        "- MusicBrainz rate limiting\n"
        "- Database initialization[/dim]"
    )
