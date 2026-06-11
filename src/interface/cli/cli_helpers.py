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

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Never
from uuid import UUID

from attrs import define, field
from rich.prompt import Prompt
from rich.table import Table
import typer

from src.application.pagination import TRACK_SORT_COLUMNS, TrackSortBy
from src.config.constants import BusinessLimits
from src.domain.entities import OperationResult, Playlist, Track
from src.domain.entities.playlist_assignment import (
    ASSIGNMENT_ACTION_TYPES,
    AssignmentActionType,
    validate_action_value,
)
from src.domain.entities.playlist_link import SyncDirection
from src.domain.entities.preference import PREFERENCE_ORDER, PreferenceState
from src.domain.entities.progress import NullProgressEmitter, ProgressEmitter
from src.domain.entities.schedule import Schedule, validate_time_of_day
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


def validate_track_sort(raw: str) -> TrackSortBy:
    """Return a typed ``TrackSortBy`` or raise ``typer.BadParameter``."""
    if raw not in TRACK_SORT_COLUMNS:
        raise typer.BadParameter(
            f"'{raw}' is not a valid sort — expected one of: "
            f"{', '.join(TRACK_SORT_COLUMNS)}."
        )
    return raw  # narrowed to TrackSortBy by the membership check


def validate_tag(raw: str) -> str:
    """Return the normalized tag or raise ``typer.BadParameter``.

    Delegates to ``normalize_tag`` (lowercase / trim / charset / length /
    colon-boundary rules) so CLI and API share a single source of truth.
    """
    try:
        return normalize_tag(raw)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e


def validate_assignment_action(raw: str) -> AssignmentActionType:
    """Return a typed ``AssignmentActionType`` or raise ``typer.BadParameter``."""
    if raw not in ASSIGNMENT_ACTION_TYPES:
        raise typer.BadParameter(
            f"'{raw}' is not a valid assignment action — expected one of: "
            f"{', '.join(sorted(ASSIGNMENT_ACTION_TYPES))}."
        )
    return raw  # narrowed to AssignmentActionType by the membership check


def validate_assignment_action_value(
    raw: str, *, action_type: AssignmentActionType
) -> str:
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


# ---------------------------------------------------------------------------
# Schedule helpers (v0.8.2) — shared by `workflow schedule` and `sync schedule`
# ---------------------------------------------------------------------------

# Weekday name ↔ cron day_of_week (0=Sunday … 6=Saturday — the schedule entity's
# convention). Lets the CLI speak "sunday" while the domain stores the int.
_WEEKDAY_TO_DOW: dict[str, int] = {
    "sunday": 0,
    "monday": 1,
    "tuesday": 2,
    "wednesday": 3,
    "thursday": 4,
    "friday": 5,
    "saturday": 6,
}
_DOW_TO_WEEKDAY: dict[int, str] = {v: k for k, v in _WEEKDAY_TO_DOW.items()}


def parse_time_of_day(value: str) -> tuple[int, int]:
    """Parse a 24-hour ``HH:MM`` string → ``(hour, minute)`` or raise.

    The range check delegates to the domain ``validate_time_of_day`` so the CLI
    and the entity backstop reject the same out-of-range times identically.
    """
    hour_str, sep, minute_str = value.partition(":")
    if not sep:
        raise typer.BadParameter(
            f"invalid time {value!r}; use HH:MM (24-hour), e.g. 06:30"
        )
    try:
        hour, minute = int(hour_str), int(minute_str)
    except ValueError:
        raise typer.BadParameter(
            f"invalid time {value!r}; use HH:MM (24-hour), e.g. 06:30"
        ) from None
    try:
        return validate_time_of_day(hour, minute)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e


def parse_weekday(name: str) -> int:
    """Map a weekday name (case-insensitive) → ``day_of_week`` (0=Sun…6=Sat)."""
    dow = _WEEKDAY_TO_DOW.get(name.strip().lower())
    if dow is None:
        valid = ", ".join(_WEEKDAY_TO_DOW)
        raise typer.BadParameter(f"invalid weekday {name!r}; expected one of: {valid}")
    return dow


def weekday_name(day_of_week: int) -> str:
    """Render a ``day_of_week`` int back to a capitalized weekday name."""
    return _DOW_TO_WEEKDAY.get(day_of_week, "?").capitalize()


def validate_timezone_arg(tz: str) -> str:
    """``--tz`` validator → IANA name or ``typer.BadParameter``."""
    from src.application.use_cases._shared.schedule_validators import (
        validate_iana_timezone,
    )

    try:
        return validate_iana_timezone(tz)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e


def validate_sync_target_arg(target: str) -> str:
    """Sync-target argument validator → the target or ``typer.BadParameter``."""
    from src.application.use_cases._shared.sync_targets import validate_sync_target

    try:
        return validate_sync_target(target)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e


def resolve_default_timezone() -> str:
    """Best-effort local IANA timezone for the schedule default; UTC on failure.

    No new dependency: tries ``$TZ`` then the ``/etc/localtime`` symlink (which
    points into the zoneinfo tree on Linux/macOS). Falls back to UTC with a
    warning so the user knows to pass ``--tz`` if the guess was wrong.
    """
    import os

    from src.application.use_cases._shared.schedule_validators import (
        validate_iana_timezone,
    )

    env_tz = os.environ.get("TZ")
    if env_tz:
        try:
            return validate_iana_timezone(env_tz)
        except ValueError:
            pass
    try:
        link = str(Path("/etc/localtime").readlink())
        if "zoneinfo/" in link:
            return validate_iana_timezone(link.rsplit("zoneinfo/", maxsplit=1)[-1])
    except OSError, ValueError:
        pass
    err_console.print(
        "[yellow]Could not detect local timezone; defaulting to UTC. "
        "Pass --tz to override.[/yellow]"
    )
    return "UTC"


def format_next_run(schedule: Schedule) -> str:
    """Render a schedule's next fire time in its own timezone for display."""
    if schedule.next_run_at is None:
        return "—"
    from zoneinfo import ZoneInfo

    local = schedule.next_run_at.astimezone(ZoneInfo(schedule.timezone))
    return local.strftime("%Y-%m-%d %H:%M %Z")


def describe_cadence(schedule: Schedule) -> str:
    """Plain-English cadence summary, e.g. "Weekly on Sunday at 06:30 (UTC)"."""
    at = f"{schedule.hour:02d}:{schedule.minute:02d}"
    if schedule.day_of_week is None:
        return f"Daily at {at} ({schedule.timezone})"
    return (
        f"Weekly on {weekday_name(schedule.day_of_week)} at {at} ({schedule.timezone})"
    )


# ---------------------------------------------------------------------------
# Shared schedule command orchestration (the single codepath behind both
# `mixd workflow schedule` and `mixd sync schedule`).
# ---------------------------------------------------------------------------


def _render_schedule(schedule: Schedule, *, label: str) -> None:
    """Print a schedule's cadence, next run, status, and failure state."""
    console.print(f"[bold]{label}[/bold]")
    console.print(f"  Cadence: {describe_cadence(schedule)}")
    console.print(f"  Next run: {format_next_run(schedule)}")
    status_color = "green" if schedule.status == "enabled" else "dim"
    console.print(f"  Status: [{status_color}]{schedule.status}[/{status_color}]")
    if schedule.consecutive_failures > 0:
        console.print(
            f"  [red]⚠ {schedule.consecutive_failures} consecutive failure(s)[/red]"
        )
        if schedule.last_error:
            console.print(f"  [dim]Last error: {schedule.last_error}[/dim]")


def run_schedule_command(
    *,
    user_id: str,
    label: str,
    workflow_id: UUID | None = None,
    sync_target: str | None = None,
    daily: bool = False,
    weekly: str | None = None,
    at: str | None = None,
    tz: str | None = None,
    enable: bool = False,
    disable: bool = False,
    remove: bool = False,
) -> None:
    """Resolve the requested schedule action and run it (one codepath, two edges).

    Exactly one mutating action is allowed per invocation; with none, the current
    schedule is printed. All validation lives here and in the application use
    cases, so ``workflow schedule`` and ``sync schedule`` stay thin wrappers that
    only differ in which target identity they pass.
    """
    from src.application.runner import execute_use_case
    from src.application.use_cases.schedules import (
        DeleteScheduleCommand,
        DeleteScheduleUseCase,
        GetScheduleCommand,
        GetScheduleUseCase,
        ToggleScheduleCommand,
        ToggleScheduleUseCase,
        UpsertScheduleCommand,
        UpsertScheduleUseCase,
    )

    def _run[T](
        factory: Callable[[UnitOfWorkProtocol], Awaitable[T]], *, error: str
    ) -> T:
        # handle_cli_error is `-> Never`, so on failure this does not return —
        # callers read the result unconditionally.
        try:
            return run_async(execute_use_case(factory, user_id=user_id))
        except Exception as e:
            handle_cli_error(e, error)

    if sum([remove, enable, disable, daily, weekly is not None]) > 1:
        raise typer.BadParameter(
            "choose only one of --daily/--weekly, --enable, --disable, --remove"
        )
    # --at / --tz only mean something alongside a cadence; alone they would
    # silently fall through to "show", so reject them explicitly.
    if (at is not None or tz is not None) and not (daily or weekly is not None):
        raise typer.BadParameter("--at / --tz require --daily or --weekly")

    if remove:
        _run(
            lambda uow: DeleteScheduleUseCase().execute(
                DeleteScheduleCommand(
                    user_id=user_id, workflow_id=workflow_id, sync_target=sync_target
                ),
                uow,
            ),
            error=f"Failed to remove schedule for {label}",
        )
        console.print(f"[green]✓ Removed schedule for {label}.[/green]")
        return

    if enable or disable:
        result = _run(
            lambda uow: ToggleScheduleUseCase().execute(
                ToggleScheduleCommand(
                    user_id=user_id,
                    enabled=enable,
                    workflow_id=workflow_id,
                    sync_target=sync_target,
                ),
                uow,
            ),
            error=f"Failed to toggle schedule for {label}",
        )
        verb = "enabled" if enable else "disabled"
        console.print(f"[green]✓ Schedule {verb} for {label}.[/green]")
        _render_schedule(result.schedule, label=label)
        return

    if daily or weekly is not None:
        if at is None:
            raise typer.BadParameter("--at HH:MM is required when setting a schedule")
        hour, minute = parse_time_of_day(at)
        day_of_week = parse_weekday(weekly) if weekly is not None else None
        timezone = validate_timezone_arg(tz) if tz else resolve_default_timezone()
        result = _run(
            lambda uow: UpsertScheduleUseCase().execute(
                UpsertScheduleCommand(
                    user_id=user_id,
                    workflow_id=workflow_id,
                    sync_target=sync_target,
                    hour=hour,
                    minute=minute,
                    day_of_week=day_of_week,
                    timezone=timezone,
                ),
                uow,
            ),
            error=f"Failed to schedule {label}",
        )
        action = "Scheduled" if result.created else "Updated schedule for"
        console.print(f"[green]✓ {action} {label}.[/green]")
        _render_schedule(result.schedule, label=label)
        return

    # No action — show the current schedule (empty state if none).
    result = _run(
        lambda uow: GetScheduleUseCase().execute(
            GetScheduleCommand(
                user_id=user_id, workflow_id=workflow_id, sync_target=sync_target
            ),
            uow,
        ),
        error=f"Failed to read schedule for {label}",
    )
    if result.schedule is None:
        console.print(f"[dim]No schedule set for {label}.[/dim]")
        console.print(
            "[dim]Set one with --daily --at HH:MM or --weekly <day> --at HH:MM.[/dim]"
        )
        return
    _render_schedule(result.schedule, label=label)
