"""Shared CLI helper utilities for command-line interface operations.

Consolidates common CLI patterns to eliminate duplication across command modules:
- Progress context setup for async operations
- Date parsing and validation
- User input prompts
"""

# pyright: reportAny=false
# Rich/Typer display types leak implicit Any

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Never

from rich.prompt import Prompt
import typer

from src.config.constants import BusinessLimits
from src.domain.entities import OperationResult
from src.domain.entities.progress import NullProgressEmitter, ProgressEmitter
from src.interface.cli.async_runner import run_async
from src.interface.cli.console import (
    get_console,
    get_error_console,
    progress_coordination_context,
)

console = get_console()
err_console = get_error_console()


def get_cli_user_id() -> str:
    """Resolve the user ID for CLI operations.

    Reads ``MIXD_USER_ID`` from settings (env var or ``.env.local``).
    Falls back to ``DEFAULT_USER_ID`` ("default") for local single-user mode.
    """
    from src.config.settings import settings

    return settings.cli.user_id or BusinessLimits.DEFAULT_USER_ID


def handle_cli_error(e: Exception, message: str) -> Never:
    """Print error message and exit with code 1.

    Database errors are classified into actionable one-line messages.
    Other errors show the exception string directly.
    """
    from sqlalchemy.exc import DatabaseError

    if isinstance(e, DatabaseError):
        from src.infrastructure.persistence.database.error_classification import (
            classify_database_error,
        )

        info = classify_database_error(e)
        err_console.print(f"[red]{message}: {info.user_message}[/red]")
        err_console.print(f"[dim]{info.detail}[/dim]")
    else:
        err_console.print(f"[red]Error: {message}: {e}[/red]")
    raise typer.Exit(1) from e


def parse_date_string(
    date_str: str | None, field_name: str = "date"
) -> datetime | None:
    """Parse and validate date string in YYYY-MM-DD format.

    Args:
        date_str: Date string to parse or None
        field_name: Name of field for error messages (e.g., "from-date")

    Returns:
        Timezone-aware datetime in UTC or None if date_str is None

    Raises:
        typer.Exit: If date string is invalid format
    """
    if not date_str:
        return None

    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        console.print(
            f"[red]Invalid {field_name} format: {date_str}. Use YYYY-MM-DD format.[/red]"
        )
        raise typer.Exit(1) from None
    else:
        return dt


def parse_iso_date(date_str: str | None) -> datetime | None:
    """Parse ISO date/datetime string with optional time component.

    Handles both 'YYYY-MM-DD' and 'YYYY-MM-DDTHH:MM:SS' formats.
    Returns None on empty input or parse failure — callers own error messages.

    Args:
        date_str: ISO date string to parse, or None/empty

    Returns:
        Timezone-aware datetime in UTC, or None if input is empty/invalid
    """
    if not date_str:
        return None
    try:
        if "T" in date_str:
            return datetime.fromisoformat(date_str)
        return datetime.fromisoformat(f"{date_str}T00:00:00+00:00")
    except ValueError:
        return None


def validate_date_range(
    from_datetime: datetime | None, to_datetime: datetime | None
) -> None:
    """Validate that from_date is not later than to_date.

    Args:
        from_datetime: Start date of range
        to_datetime: End date of range

    Raises:
        typer.Exit: If from_date > to_date
    """
    if from_datetime and to_datetime and from_datetime > to_datetime:
        console.print("[red]from-date cannot be later than to-date[/red]")
        raise typer.Exit(1)


def prompt_batch_size() -> int | None:
    """Prompt user for batch size with validation.

    Returns:
        Batch size as integer or None for default
    """
    batch_size_str = Prompt.ask(
        "Batch size (leave empty for default)",
        default="",
    )
    return int(batch_size_str) if batch_size_str else None


def validate_file_path(file_path: Path) -> None:
    """Validate that file path exists and is a file.

    Args:
        file_path: Path to validate

    Raises:
        typer.Exit: If path doesn't exist or is not a file
    """
    if not file_path.exists():
        console.print(f"[red]File not found: {file_path}[/red]")
        console.print("Make sure the path is correct and the file exists.")
        raise typer.Exit(1)

    if not file_path.is_file():
        console.print(f"[red]Path is not a file: {file_path}[/red]")
        raise typer.Exit(1)


def run_import_with_progress(
    service: Literal["lastfm", "spotify"],
    mode: Literal["recent", "incremental", "full", "file"],
    *,
    limit: int | None = None,
    username: str | None = None,
    file_path: Path | None = None,
    confirm: bool = False,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    batch_size: int | None = None,
    progress_emitter: ProgressEmitter | None = None,
) -> OperationResult:
    """Execute import with unified progress context and display.

    Consolidates the common pattern of:
    1. Setting up progress coordination context
    2. Creating progress adapter
    3. Running import use case
    4. Handling async execution

    The caller-supplied ``progress_emitter`` is accepted for protocol
    compatibility but not forwarded — this function creates its own
    adapter from the progress coordination context.

    Args:
        service: Service name ("lastfm" or "spotify")
        mode: Import mode ("incremental", "file", etc.)
        limit: Maximum tracks to import (LastFM only).
        username: LastFM username for user-specific imports.
        file_path: Path to import file (Spotify file imports).
        confirm: Whether user confirmed destructive operations.
        from_date: Start date for date range filtering.
        to_date: End date for date range filtering.
        batch_size: Batch size for chunked processing.
        progress_emitter: Fallback emitter when no progress manager is active in context.

    Returns:
        Operation result from import execution
    """

    async def _execute_with_progress() -> OperationResult:
        from src.application.use_cases.import_play_history import run_import

        async with progress_coordination_context(show_live=True) as context:
            # Get progress manager from unified context
            progress_manager = context.get_progress_manager()

            # Prefer context manager, then caller-supplied emitter, then null
            progress_adapter: ProgressEmitter = (
                progress_manager or progress_emitter or NullProgressEmitter()
            )

            return await run_import(
                user_id=get_cli_user_id(),
                service=service,
                mode=mode,
                limit=limit,
                username=username,
                file_path=file_path,
                confirm=confirm,
                from_date=from_date,
                to_date=to_date,
                progress_emitter=progress_adapter,
                batch_size=batch_size,
            )

    return run_async(_execute_with_progress())
