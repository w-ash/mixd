"""Shared CLI helper utilities for command-line interface operations.

Consolidates common CLI patterns to eliminate duplication across command modules:
- Progress context setup for async operations
- Date parsing and validation
- User input prompts
- Argument validators (preference state, tag)
- Reference resolvers (track by UUID-or-search, playlist by UUID-or-name)
- Shared Rich renderers (track tables, batch-operation summaries)

Prefer `typer.BadParameter` over ad-hoc `typer.Exit(1)` for argument validation
— Typer formats the message consistently with its own error output and never
leaks a stack trace.
"""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Never
from uuid import UUID

from attrs import define, field
from rich.prompt import Prompt
from rich.table import Table
import typer

from src.config.constants import BusinessLimits
from src.domain.entities import OperationResult, Playlist, Track
from src.domain.entities.playlist_link import SyncDirection
from src.domain.entities.playlist_metadata_mapping import (
    MAPPING_ACTION_TYPES,
    MappingActionType,
    validate_action_value,
)
from src.domain.entities.preference import PREFERENCE_ORDER, PreferenceState
from src.domain.entities.progress import NullProgressEmitter, ProgressEmitter
from src.domain.entities.tag import normalize_tag
from src.domain.repositories import UnitOfWorkProtocol
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


# ---------------------------------------------------------------------------
# Argument validators
# ---------------------------------------------------------------------------
# Raise `typer.BadParameter` on invalid input so Typer prints a clean,
# single-line error and exits with code 2 (conventional for CLI usage errors).


_VALID_PREFERENCE_STATES: tuple[PreferenceState, ...] = tuple(PREFERENCE_ORDER)


def validate_preference_state(raw: str) -> PreferenceState:
    """Return a typed ``PreferenceState`` or raise ``typer.BadParameter``."""
    if raw not in _VALID_PREFERENCE_STATES:
        raise typer.BadParameter(
            f"'{raw}' is not a valid state — expected one of: "
            f"{', '.join(_VALID_PREFERENCE_STATES)}."
        )
    return raw  # runtime-narrowed to PreferenceState


def validate_tag(raw: str) -> str:
    """Return the normalized tag or raise ``typer.BadParameter``.

    Delegates to ``normalize_tag`` (lowercase / trim / charset / length /
    colon-boundary rules) so CLI and API share a single source of truth.
    """
    try:
        return normalize_tag(raw)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e


def validate_mapping_action(raw: str) -> MappingActionType:
    """Return a typed ``MappingActionType`` or raise ``typer.BadParameter``."""
    if raw not in MAPPING_ACTION_TYPES:
        raise typer.BadParameter(
            f"'{raw}' is not a valid mapping action — expected one of: "
            f"{', '.join(sorted(MAPPING_ACTION_TYPES))}."
        )
    return raw  # narrowed to MappingActionType by the membership check


def validate_mapping_action_value(raw: str, *, action_type: MappingActionType) -> str:
    """Validate + canonicalize an ``action_value`` for the given action type.

    Delegates to the domain ``validate_action_value`` so CLI, API schemas,
    and direct domain callers share a single source of truth.
    """
    try:
        return validate_action_value(action_type, raw)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e


def validate_sync_source(raw: str) -> SyncDirection:
    """Map ``--source {spotify,mixd}`` to a ``SyncDirection`` or raise.

    ``spotify`` means "source is Spotify" → pull into Mixd.
    ``mixd`` means "source is Mixd" → push out to Spotify.
    """
    match raw:
        case "spotify":
            return SyncDirection.PULL
        case "mixd":
            return SyncDirection.PUSH
        case _:
            raise typer.BadParameter(
                f"'{raw}' is not a valid source — expected 'spotify' or 'mixd'."
            )


# ---------------------------------------------------------------------------
# Reference resolvers (accept UUID OR search / name)
# ---------------------------------------------------------------------------
# CLI commands take `<id_or_search>` arguments — the user should be able to
# paste a UUID or type a memorable fragment of the title / artist / playlist
# name. Resolvers normalize that into a domain entity with consistent
# ambiguity handling (zero matches → error, one match → return, many →
# error listing the candidates so the user can retry with a UUID).


_MAX_CANDIDATES_SHOWN = 10


def _render_candidate_list(items: Sequence[str]) -> str:
    shown = list(items[:_MAX_CANDIDATES_SHOWN])
    extra = len(items) - len(shown)
    lines = "\n  - ".join(shown)
    suffix = f"\n  … and {extra} more" if extra > 0 else ""
    return f"\n  - {lines}{suffix}"


def resolve_track_ref(ref: str, *, user_id: str) -> Track:
    """Resolve a CLI ``<id_or_search>`` track argument to a domain ``Track``.

    If ``ref`` parses as a UUID, fetches the track directly. Otherwise runs
    the track list search (``q=``) and returns the unique match, or raises
    ``typer.BadParameter`` with a candidate list when the search is
    ambiguous or empty.
    """
    from src.application.runner import execute_use_case

    try:
        track_id = UUID(ref)
    except ValueError:
        track_id = None

    if track_id is not None:

        async def _by_id(uow: UnitOfWorkProtocol) -> Track:
            async with uow:
                return await uow.get_track_repository().get_track_by_id(
                    track_id, user_id=user_id
                )

        from src.domain.exceptions import NotFoundError

        try:
            return run_async(execute_use_case(_by_id, user_id=user_id))
        except NotFoundError as e:
            raise typer.BadParameter(f"No track with id {ref}") from e

    async def _by_search(uow: UnitOfWorkProtocol) -> list[Track]:
        async with uow:
            page = await uow.get_track_repository().list_tracks(
                user_id=user_id,
                query=ref,
                limit=_MAX_CANDIDATES_SHOWN + 1,
                include_total=False,
            )
            return page["tracks"]

    tracks: list[Track] = run_async(execute_use_case(_by_search, user_id=user_id))
    if not tracks:
        raise typer.BadParameter(f"No track matching '{ref}'")
    if len(tracks) > 1:
        candidates = [
            f"{t.title} — {', '.join(a.name for a in t.artists)} [{t.id}]"
            for t in tracks
        ]
        raise typer.BadParameter(
            f"'{ref}' matches multiple tracks — pass a UUID to disambiguate:"
            + _render_candidate_list(candidates)
        )
    return tracks[0]


def resolve_playlist_ref(ref: str, *, user_id: str) -> Playlist:
    """Resolve a ``--playlist <name_or_id>`` argument to a domain ``Playlist``.

    Parses ``ref`` as UUID first; otherwise case-insensitively matches
    ``name`` across the user's playlists. Ambiguous or missing matches
    raise ``typer.BadParameter`` with candidate names.
    """
    from src.application.runner import execute_use_case

    try:
        playlist_id = UUID(ref)
    except ValueError:
        playlist_id = None

    if playlist_id is not None:

        async def _by_id(uow: UnitOfWorkProtocol) -> Playlist:
            async with uow:
                return await uow.get_playlist_repository().get_playlist_by_id(
                    playlist_id, user_id=user_id
                )

        from src.domain.exceptions import NotFoundError

        try:
            return run_async(execute_use_case(_by_id, user_id=user_id))
        except NotFoundError as e:
            raise typer.BadParameter(f"No playlist with id {ref}") from e

    async def _all(uow: UnitOfWorkProtocol) -> list[Playlist]:
        async with uow:
            return await uow.get_playlist_repository().list_all_playlists(
                user_id=user_id
            )

    all_playlists: list[Playlist] = run_async(execute_use_case(_all, user_id=user_id))
    needle = ref.casefold()
    matches = [p for p in all_playlists if p.name.casefold() == needle]
    if not matches:
        # Fall back to substring match so users don't need exact casing.
        matches = [p for p in all_playlists if needle in p.name.casefold()]

    if not matches:
        raise typer.BadParameter(f"No playlist matching '{ref}'")
    if len(matches) > 1:
        raise typer.BadParameter(
            f"'{ref}' matches multiple playlists — pass a UUID to disambiguate:"
            + _render_candidate_list([f"{p.name} [{p.id}]" for p in matches])
        )
    return matches[0]


# ---------------------------------------------------------------------------
# Shared Rich renderers
# ---------------------------------------------------------------------------


TrackColumn = tuple[str, Callable[[Track], str]]


def render_tracks_table(
    tracks: Sequence[Track],
    *,
    title: str,
    extra_columns: Sequence[TrackColumn] = (),
) -> Table:
    """Build a consistent ``Track`` listing table.

    Default columns: Title, Artist, ID (truncated). Callers pass
    ``extra_columns`` as ``(label, accessor)`` tuples to append
    feature-specific fields (e.g. preference state, tag count).
    """
    table = Table(title=title)
    table.add_column("Title", style="cyan")
    table.add_column("Artist", style="dim")
    for label, _ in extra_columns:
        table.add_column(label)
    table.add_column("ID", style="dim")

    for track in tracks:
        row = [
            track.title,
            ", ".join(a.name for a in track.artists),
            *[accessor(track) for _, accessor in extra_columns],
            str(track.id),
        ]
        table.add_row(*row)
    return table


@define(frozen=True, slots=True)
class BatchOperationResult:
    """Summary of a CLI batch operation with per-item outcomes.

    ``succeeded`` is the count of items that completed as requested.
    ``skipped`` covers idempotent no-ops (tag already attached, preference
    already at target state). ``failed`` carries human-readable error
    messages — one per item — so the user sees exactly which items
    couldn't be processed.
    """

    succeeded: int = 0
    skipped: int = 0
    failed: list[str] = field(factory=list)

    @property
    def total(self) -> int:
        return self.succeeded + self.skipped + len(self.failed)


def render_batch_summary(result: BatchOperationResult, *, title: str) -> Table:
    """Three-row summary table: succeeded / skipped / failed counts.

    Callers print this after a batch operation so the user can see at a
    glance how many items landed, how many were no-ops, and how many need
    follow-up.
    """
    table = Table(title=title, show_header=False)
    table.add_column("", style="bold")
    table.add_column("", justify="right")
    table.add_row("Succeeded", str(result.succeeded), style="green")
    table.add_row("Skipped", str(result.skipped), style="dim")
    table.add_row(
        "Failed",
        str(len(result.failed)),
        style="red" if result.failed else "dim",
    )
    table.add_section()
    table.add_row("Total", str(result.total), style="bold")
    return table
