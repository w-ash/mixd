"""CLI commands for managing match reviews (accept/reject proposed track matches)."""

from typing import Annotated, Literal
from uuid import UUID

from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
import typer

from src.interface.cli.async_runner import run_async
from src.interface.cli.cli_helpers import get_cli_user_id, handle_cli_error
from src.interface.cli.console import get_console, get_error_console

console = get_console()
err_console = get_error_console()

app = typer.Typer(
    help="Manage pending track match reviews",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# Display thresholds for confidence coloring in tables
_HIGH_CONFIDENCE = 80
_MEDIUM_CONFIDENCE = 60


@app.command(name="list")
def list_reviews(
    limit: Annotated[int, typer.Option("--limit", "-l")] = 50,
    output_format: Annotated[
        Literal["table", "json"], typer.Option("--format", "-f")
    ] = "table",
) -> None:
    """List pending match reviews."""

    async def _list():
        from src.application.runner import execute_use_case
        from src.application.use_cases.list_match_reviews import (
            ListMatchReviewsCommand,
            ListMatchReviewsUseCase,
        )

        user_id = get_cli_user_id()
        return await execute_use_case(
            lambda uow: ListMatchReviewsUseCase().execute(
                ListMatchReviewsCommand(user_id=user_id, limit=limit), uow
            ),
            user_id=user_id,
        )

    try:
        result = run_async(_list())
    except Exception as e:
        handle_cli_error(e, "Failed to list reviews")

    if not result.reviews:
        console.print("[green]No pending match reviews.[/green]")
        return

    if output_format == "json":
        import json

        print(
            json.dumps(
                [
                    {
                        "id": r.id,
                        "track_id": r.track_id,
                        "connector": r.connector_name,
                        "connector_track_id": r.connector_track_id,
                        "method": r.match_method,
                        "confidence": r.confidence,
                        "status": r.status,
                    }
                    for r in result.reviews
                ],
                indent=2,
                default=str,
            )
        )
        return

    table = Table(
        title=f"Pending Reviews ({result.total} total)",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Track", style="green")
    table.add_column("Connector")
    table.add_column("Method")
    table.add_column("Confidence", justify="right")

    for r in result.reviews:
        conf_style = (
            "[green]"
            if r.confidence >= _HIGH_CONFIDENCE
            else "[yellow]"
            if r.confidence >= _MEDIUM_CONFIDENCE
            else "[red]"
        )
        table.add_row(
            str(r.id),
            str(r.track_id),
            r.connector_name,
            r.match_method,
            f"{conf_style}{r.confidence}[/]",
        )

    console.print(table)


@app.command(name="resolve")
def resolve_review(
    review_id: Annotated[
        str | None,
        typer.Argument(help="Review UUID to resolve (omit for interactive mode)"),
    ] = None,
    action: Annotated[
        str | None,
        typer.Option("--action", "-a", help="accept or reject"),
    ] = None,
    interactive: Annotated[
        bool,
        typer.Option("--interactive", "-i", help="Step through pending reviews"),
    ] = False,
) -> None:
    """Resolve a match review (accept or reject).

    Use --interactive to step through all pending reviews one by one.
    """
    if interactive:
        _resolve_interactive()
        return

    if review_id is None:
        err_console.print("[red]Provide a review ID or use --interactive.[/red]")
        raise typer.Exit(1)

    if action not in ("accept", "reject"):
        err_console.print("[red]--action must be 'accept' or 'reject'.[/red]")
        raise typer.Exit(1)

    parsed_id = UUID(review_id)

    async def _resolve():
        from src.application.runner import execute_use_case
        from src.application.use_cases.resolve_match_review import (
            ResolveMatchReviewCommand,
            ResolveMatchReviewUseCase,
        )

        user_id = get_cli_user_id()
        return await execute_use_case(
            lambda uow: ResolveMatchReviewUseCase().execute(
                ResolveMatchReviewCommand(
                    user_id=user_id, review_id=parsed_id, action=action
                ),
                uow,
            ),
            user_id=user_id,
        )

    try:
        result = run_async(_resolve())
    except Exception as e:
        handle_cli_error(e, "Failed to resolve review")

    verb = "Accepted" if action == "accept" else "Rejected"
    extra = " (mapping created)" if result.mapping_created else ""
    console.print(f"[green]{verb} review {review_id}{extra}[/green]")


def _resolve_interactive() -> None:
    """Step through pending reviews one by one with Rich panels."""

    async def _fetch_pending():
        from src.application.runner import execute_use_case
        from src.application.use_cases.list_match_reviews import (
            ListMatchReviewsCommand,
            ListMatchReviewsUseCase,
        )

        user_id = get_cli_user_id()
        return await execute_use_case(
            lambda uow: ListMatchReviewsUseCase().execute(
                ListMatchReviewsCommand(user_id=user_id, limit=100), uow
            ),
            user_id=user_id,
        )

    try:
        result = run_async(_fetch_pending())
    except Exception as e:
        handle_cli_error(e, "Failed to load reviews")

    if not result.reviews:
        console.print("[green]No pending reviews.[/green]")
        return

    console.print(f"\n[bold]{len(result.reviews)} pending review(s)[/bold]\n")

    resolved = 0
    for i, review in enumerate(result.reviews, 1):
        console.print(
            Panel(
                f"[cyan]Track:[/cyan]      {review.track_id}\n"
                f"[cyan]Connector:[/cyan]  {review.connector_name}\n"
                f"[cyan]External:[/cyan]   {review.connector_track_id}\n"
                f"[cyan]Method:[/cyan]     {review.match_method}\n"
                f"[cyan]Confidence:[/cyan] {review.confidence}%",
                title=f"[bold]Review {i}/{len(result.reviews)}[/bold]",
            )
        )

        choice = Prompt.ask(
            "[bold]Action[/bold]",
            choices=["accept", "reject", "skip", "quit"],
            default="skip",
        )

        if choice == "quit":
            break
        if choice == "skip":
            continue

        async def _do_resolve(rid: UUID = review.id, act: str = choice):
            from src.application.runner import execute_use_case
            from src.application.use_cases.resolve_match_review import (
                ResolveMatchReviewCommand,
                ResolveMatchReviewUseCase,
            )

            user_id = get_cli_user_id()
            action: Literal["accept", "reject"] = (
                "accept" if act == "accept" else "reject"
            )
            return await execute_use_case(
                lambda uow: ResolveMatchReviewUseCase().execute(
                    ResolveMatchReviewCommand(
                        user_id=user_id, review_id=rid, action=action
                    ),
                    uow,
                ),
                user_id=user_id,
            )

        try:
            r = run_async(_do_resolve())
            verb = "Accepted" if choice == "accept" else "Rejected"
            extra = " (mapping created)" if r.mapping_created else ""
            console.print(f"  [green]{verb}{extra}[/green]\n")
            resolved += 1
        except Exception as e:
            err_console.print(f"  [red]Error: {e}[/red]\n")

    console.print(f"[bold]Resolved {resolved} review(s).[/bold]")
