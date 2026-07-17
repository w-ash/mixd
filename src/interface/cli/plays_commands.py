"""Play history CLI commands — the canonical-play projection surface."""

from rich.prompt import Confirm
import typer

from src.application.use_cases.rebuild_play_history import RebuildPlayHistoryResult
from src.domain.entities.progress import NullProgressEmitter, ProgressEmitter
from src.interface.cli.async_runner import run_async
from src.interface.cli.cli_helpers import get_cli_user_id
from src.interface.cli.console import get_console, progress_coordination_context
from src.interface.cli.ui import display_operation_result

console = get_console()

app = typer.Typer(
    help="Canonical play history operations",
    rich_help_panel="🔄 Track Data Sync",
)


@app.command(name="rebuild")
def rebuild(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Report the diff (create/update/merge/delete) without writing.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Re-derive the entire canonical play history from the observation ledger.

    Canonical plays are a deterministic projection of imported source data —
    a rebuild replays that projection over the full ledger, converging any
    order-dependent duplicates and deleting canonical plays no observation
    backs. Run with --dry-run first to see what would change.
    """
    if not dry_run:
        console.print(
            "[yellow]This re-derives ALL canonical plays from the ledger: "
            "rows may be updated, merged, or deleted.[/yellow]\n"
            "[dim]The observation ledger itself is never modified. "
            "Use --dry-run to preview.[/dim]"
        )
        if not yes and not Confirm.ask("Continue?", default=False):
            console.print("[dim]Aborted.[/dim]")
            raise typer.Exit(code=0)

    async def _execute() -> RebuildPlayHistoryResult:
        from src.application.use_cases.rebuild_play_history import run_rebuild

        async with progress_coordination_context(show_live=True) as context:
            progress_broker = context.get_progress_broker()
            emitter: ProgressEmitter = progress_broker or NullProgressEmitter()
            return await run_rebuild(
                user_id=get_cli_user_id(),
                dry_run=dry_run,
                progress_emitter=emitter,
            )

    rebuild_result = run_async(_execute())
    display_operation_result(rebuild_result.result)
    if dry_run:
        console.print(
            "[dim]Dry run — nothing was written. "
            "Re-run without --dry-run to apply.[/dim]"
        )
