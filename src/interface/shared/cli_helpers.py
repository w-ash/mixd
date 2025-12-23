"""Shared CLI helper utilities for command-line interface operations.

Consolidates common CLI patterns to eliminate duplication across command modules:
- Progress context setup for async operations
- Date parsing and validation
- User input prompts
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from rich.prompt import Prompt
import typer

from src.application.services.progress_manager import AsyncProgressManagerAdapter
from src.infrastructure.connectors import run_async_with_connector_executor
from src.application.use_cases.import_play_history import run_import
from src.domain.entities import OperationResult
from src.domain.entities.progress import NullProgressEmitter, ProgressEmitter
from src.interface.cli.console import get_console, progress_coordination_context

console = get_console()


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
        return dt
    except ValueError:
        console.print(
            f"[red]Invalid {field_name} format: {date_str}. Use YYYY-MM-DD format.[/red]"
        )
        raise typer.Exit(1) from None


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
    **import_params: Any,
) -> OperationResult:
    """Execute import with unified progress context and display.

    Consolidates the common pattern of:
    1. Setting up progress coordination context
    2. Creating progress adapter
    3. Running import use case
    4. Handling async execution

    Args:
        service: Service name ("lastfm" or "spotify")
        mode: Import mode ("incremental", "file", etc.)
        **import_params: Additional parameters for run_import()

    Returns:
        Operation result from import execution
    """

    async def _execute_with_progress() -> OperationResult:
        async with progress_coordination_context(show_live=True) as context:
            # Get progress manager from unified context
            progress_manager = context.get_progress_manager()

            # Create adapter to implement ProgressEmitter protocol
            progress_adapter: ProgressEmitter = (
                AsyncProgressManagerAdapter(progress_manager)
                if progress_manager
                else NullProgressEmitter()
            )

            return await run_import(
                service=service,
                mode=mode,
                progress_emitter=progress_adapter,
                **import_params,
            )

    return run_async_with_connector_executor(_execute_with_progress())
