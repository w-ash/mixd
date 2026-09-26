"""CLI commands for browsing, favoriting, minting and enriching artists."""

from typing import Annotated
from uuid import UUID

from rich.table import Table
import typer

from src.application.use_cases.enrich_artists import EnrichArtistsResult
from src.application.use_cases.mint_artists import MintArtistsResult
from src.domain.entities.progress import ProgressEmitter
from src.domain.exceptions import NotFoundError
from src.domain.repositories.artist import (
    ARTIST_SORTS,
    DEFAULT_ARTIST_SORT,
    ArtistSortBy,
)
from src.interface.cli.async_runner import run_async
from src.interface.cli.cli_helpers import (
    get_cli_user_id,
    run_with_progress,
    validate_sort,
)
from src.interface.cli.console import brand_status, get_console
from src.interface.cli.ui import display_operation_result

console = get_console()

app = typer.Typer(
    help="Browse and curate the artists in your library",
    rich_help_panel="🎵 Track Operations",
)


def _parse_artist_id(value: str) -> UUID:
    """Artists are addressed by id only — names are non-unique by design."""
    try:
        return UUID(value)
    except ValueError as e:
        raise typer.BadParameter(
            f"'{value}' is not an artist UUID — run 'mixd artists list' to find one"
        ) from e


@app.command(name="list")
def list_artists(
    search: Annotated[
        str | None, typer.Option("--search", "-q", help="Match artist name")
    ] = None,
    favorites: Annotated[
        bool, typer.Option("--favorites", help="Only favorited artists")
    ] = False,
    sort: Annotated[
        str, typer.Option("--sort", help=f"One of: {', '.join(ARTIST_SORTS)}")
    ] = DEFAULT_ARTIST_SORT,
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, max=200)] = 50,
) -> None:
    """List the artists in your library."""
    from src.application.use_cases.list_artists import run_list_artists

    sort_by: ArtistSortBy = validate_sort(
        sort, ARTIST_SORTS, default=DEFAULT_ARTIST_SORT
    )
    user_id = get_cli_user_id()

    with brand_status("Loading artists..."):
        result = run_async(
            run_list_artists(
                user_id=user_id,
                search=search,
                favorites_only=favorites,
                sort_by=sort_by,
                limit=limit,
            )
        )

    if not result.artists:
        console.print("[dim]No artists found.[/dim]")
        return

    table = Table(
        title=f"Artists ({result.total if result.total is not None else '?'})"
    )
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Name", style="cyan")
    table.add_column("Tracks", justify="right")
    table.add_column("Fav", justify="center")
    table.add_column("Connectors", style="dim")
    for artist in result.artists:
        table.add_row(
            str(artist.id),
            artist.name,
            str(result.track_counts.get(artist.id, 0)),
            "♥" if artist.id in result.favorited_ids else "",
            ", ".join(result.connector_names.get(artist.id, [])),
        )
    console.print(table)


@app.command(name="show")
def show_artist(
    artist_id: Annotated[str, typer.Argument(help="Artist UUID", metavar="ARTIST_ID")],
) -> None:
    """Show one artist with its connector links and related projects."""
    from src.application.use_cases.get_artist_detail import run_get_artist_detail

    parsed = _parse_artist_id(artist_id)
    try:
        with brand_status("Loading artist..."):
            result = run_async(
                run_get_artist_detail(user_id=get_cli_user_id(), artist_id=parsed)
            )
    except NotFoundError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e

    heart = " [magenta]♥[/magenta]" if result.is_favorited else ""
    console.print(f"[bold cyan]{result.artist.name}[/bold cyan]{heart}")
    console.print(
        f"[dim]{result.track_count} track(s)"
        + (f" · MBID {result.artist.mbid}" if result.artist.mbid else "")
        + (f" · {result.artist.kind}" if result.artist.kind else "")
        + "[/dim]"
    )

    if result.connector_mappings:
        table = Table(title="Connectors")
        table.add_column("Service", style="cyan")
        table.add_column("Name")
        table.add_column("Primary", justify="center")
        table.add_column("URL", style="dim")
        for mapping in result.connector_mappings:
            table.add_row(
                mapping.connector_name,
                mapping.name,
                "✓" if mapping.is_primary else "",
                mapping.external_url or "",
            )
        console.print(table)

    if result.related:
        console.print("[bold]Related[/bold]")
        for relation in result.related:
            console.print(
                f"  [dim]{relation.relation}[/dim] {relation.name} "
                f"[dim]({relation.connector_name})[/dim]"
            )


@app.command(name="favorite")
def favorite_artist(
    artist_id: Annotated[str, typer.Argument(help="Artist UUID", metavar="ARTIST_ID")],
) -> None:
    """Favorite an artist."""
    _set_favorite(artist_id, is_favorited=True)


@app.command(name="unfavorite")
def unfavorite_artist(
    artist_id: Annotated[str, typer.Argument(help="Artist UUID", metavar="ARTIST_ID")],
) -> None:
    """Remove an artist from your favorites."""
    _set_favorite(artist_id, is_favorited=False)


def _set_favorite(artist_id: str, *, is_favorited: bool) -> None:
    """Shared body of ``favorite`` / ``unfavorite`` — one message vocabulary."""
    from src.application.use_cases.favorite_artist import run_favorite_artist

    parsed = _parse_artist_id(artist_id)
    verb = "Favoriting" if is_favorited else "Unfavoriting"
    try:
        with brand_status(f"{verb} artist..."):
            result = run_async(
                run_favorite_artist(
                    user_id=get_cli_user_id(),
                    artist_id=parsed,
                    is_favorited=is_favorited,
                )
            )
    except NotFoundError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e

    if result.changed:
        state = "Favorited" if is_favorited else "Unfavorited"
        console.print(f"[green]{state} artist[/green]")
    else:
        state = "already favorited" if is_favorited else "not favorited"
        console.print(f"[dim]Artist {state} — no change[/dim]")


@app.command(name="enrich")
def enrich_artists(
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-n", min=1, help="Cap how many artists to process"),
    ] = None,
    refresh_older_than_days: Annotated[
        int,
        typer.Option(
            "--refresh-older-than-days",
            min=0,
            help="Also re-check artists resolved more than N days ago",
        ),
    ] = 30,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report what would change, write nothing")
    ] = False,
) -> None:
    """Resolve artist identity against MusicBrainz."""

    async def _enrich(emitter: ProgressEmitter) -> EnrichArtistsResult:
        from src.application.use_cases.enrich_artists import run_enrich_artists

        return await run_enrich_artists(
            user_id=get_cli_user_id(),
            limit=limit,
            refresh_older_than_days=refresh_older_than_days,
            dry_run=dry_run,
            progress_emitter=emitter,
        )

    result = run_with_progress(_enrich)
    display_operation_result(result.result)
    if dry_run:
        console.print("[dim]Dry run — nothing was written.[/dim]")


@app.command(name="mint")
def mint_artists(
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-n", min=1, help="Cap how many tracks to walk"),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report what would change, write nothing")
    ] = False,
) -> None:
    """Mint canonical artists from the connector credits already stored.

    For a library imported before v0.12.1: its credits carry the service artist
    ids but no canonical artist owns them yet. Reads the database only.
    """

    async def _mint(emitter: ProgressEmitter) -> MintArtistsResult:
        from src.application.use_cases.mint_artists import run_mint_artists

        return await run_mint_artists(
            user_id=get_cli_user_id(),
            limit=limit,
            dry_run=dry_run,
            progress_emitter=emitter,
        )

    result = run_with_progress(_mint)
    display_operation_result(result.result)
    if dry_run:
        console.print("[dim]Dry run — nothing was written.[/dim]")
