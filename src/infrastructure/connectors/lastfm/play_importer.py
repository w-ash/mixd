"""Last.fm-specific play importer implementing connector-only ingestion.

Contains all Last.fm import logic: token-first account resolution, windowed
chunking, checkpoint management, and boundary-respecting date ranges.
"""

from datetime import UTC, date, datetime, time, timedelta
import math
from typing import Final, override

from attrs import evolve

from src.config import get_logger, settings
from src.config.constants import LastFMConstants
from src.domain.entities import (
    ConnectorTrackPlay,
    OperationResult,
    PlayRecord,
    SyncCheckpoint,
)
from src.domain.entities.progress import ProgressEmitter, create_progress_event
from src.domain.exceptions import LastfmAuthRequiredError
from src.domain.repositories.play import (
    LastfmImportParams,
    PlayImporterProtocol,
    PlayImportParams,
)
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.token_storage import (
    TokenStorage,
    get_token_storage,
)
from src.infrastructure.connectors.lastfm.client import LastFMPartialFetchError
from src.infrastructure.connectors.lastfm.connector import LastFMConnector
from src.infrastructure.services.base_play_importer import BasePlayImporter

logger = get_logger(__name__)

# How many calendar days one user.getRecentTracks fetch spans. Seven days is
# truncation headroom, not an API shape: the API takes arbitrary from/to
# bounds at 200/page, and one fetch is capped at FULL_HISTORY_LIMIT (10,000)
# records — a typical dense day is ≤200 scrobbles, so 7x200 leaves ~7x
# headroom before a window could truncate (and a window that hits the cap is
# re-fetched one day at a time). It lives HERE, not in config/constants: the
# window is this
# importer's fetch-strategy tunable — the crash-loss granularity of its
# write-then-checkpoint loop — not a property of the Last.fm API.
_IMPORT_WINDOW_DAYS: Final = 7

# The cursor records the last completed day AND the Last.fm account it belongs
# to, separated by "@" (never legal in a Last.fm username, so the split is
# unambiguous). The account half exists because the checkpoint row is keyed on
# the mixd user — see _checkpoint_matches_account.
_CURSOR_ACCOUNT_SEPARATOR = "@"


def _encode_cursor(completed_date: date, account: str) -> str:
    """Build a cursor from the completed day and the account it was fetched for."""
    return f"{completed_date.isoformat()}{_CURSOR_ACCOUNT_SEPARATOR}{account}"


def _decode_cursor(cursor: str) -> tuple[str, str | None]:
    """Split a cursor into (date text, account or None when unrecorded)."""
    day, separator, account = cursor.partition(_CURSOR_ACCOUNT_SEPARATOR)
    return day, account if separator else None


class LastfmPlayImporter(
    BasePlayImporter[ConnectorTrackPlay, LastfmImportParams], PlayImporterProtocol
):
    """Last.fm play importer with windowed chunking and checkpoint logic.

    Implements PlayImporterProtocol for use with the generic
    PlayImportOrchestrator. Ingests connector plays only; canonical resolution
    is the resolver's job (two-phase import).

    The pipeline's ``TRawData`` is ``ConnectorTrackPlay``, not ``PlayRecord``:
    the window loop converts each window's scrobbles the moment they land and
    drops the raw records, so a multi-year import never accumulates the whole
    span twice. :meth:`_process_data` is therefore a pass-through.

    The window loop also persists each window before checkpointing it, so the
    run-end save has nothing left to write — see
    ``persists_plays_during_fetch``.
    """

    # The window loop owns both the writes and the resume cursor, and keeps the
    # cursor behind the writes; the base class must not re-save the span.
    persists_plays_during_fetch = True

    operation_name: str
    lastfm_connector: LastFMConnector
    _token_storage: TokenStorage

    def __init__(
        self,
        lastfm_connector: LastFMConnector | None = None,
        token_storage: TokenStorage | None = None,
    ) -> None:
        """Initialize Last.fm play importer for connector-only ingestion.

        Args:
            lastfm_connector: Last.fm API connector (optional, created if None)
            token_storage: OAuth token store used to resolve the connected
                Last.fm account name per mixd user (optional, defaults to the
                DB-backed store)
        """
        self.operation_name = "Last.fm Connector Play Import"
        self.lastfm_connector = lastfm_connector or LastFMConnector()
        self._token_storage = token_storage or get_token_storage()

    @override
    async def import_plays(
        self,
        uow: UnitOfWorkProtocol,
        params: PlayImportParams,
        *,
        user_id: str,
        progress_emitter: ProgressEmitter | None = None,
    ) -> tuple[OperationResult, list[ConnectorTrackPlay]]:
        """Import Last.fm plays as connector_plays for later resolution.

        Args:
            uow: Unit of work for database operations
            params: Last.fm import selectors (username, date range, limit)
            user_id: The mixd user id, used for token-first account resolution
                and ledger tenancy
            progress_emitter: Optional progress emitter

        Returns:
            Tuple of (operation_result, connector_plays_list)
        """
        if not isinstance(params, LastfmImportParams):
            raise TypeError(
                f"LastfmPlayImporter requires LastfmImportParams, got {type(params).__name__}"
            )

        # Resolve the Last.fm account ONCE, token-first, and pass the concrete
        # name down so the per-window fetch + checkpoint never fall back to env for
        # a web user (the cross-tenant leak). Raises LastfmAuthRequiredError if
        # nothing resolves (web user with no connected account and no env).
        resolved_username = await self._resolve_username(params.username, user_id)

        logger.info(
            "Starting Last.fm connector play ingestion with unified approach",
            username=resolved_username,
            from_date=params.from_date,
            to_date=params.to_date,
            limit=params.limit,
        )

        # Checkpoint reset for full history imports
        if (
            params.limit
            and params.limit >= settings.import_settings.full_history_import_threshold
        ):
            await self._reset_checkpoint_for_full_history(
                user_id=user_id, account=resolved_username, uow=uow
            )

        result, connector_plays = await self.import_data(
            evolve(params, username=resolved_username),
            uow=uow,
            user_id=user_id,
            progress_emitter=progress_emitter,
        )

        logger.info(
            "Last.fm connector play ingestion complete",
            connector_plays_ingested=len(connector_plays),
            canonical_plays_created=0,  # Zero - we only do ingestion
        )

        return result, connector_plays

    async def _resolve_username(
        self, request_username: str | None, user_id: str
    ) -> str:
        """Resolve the Last.fm account to import, token-first.

        Precedence (2026-canonical user → env, the security crux):
        1. The connected account: the stored OAuth token's ``account_name`` for
           THIS mixd ``user_id``. A web user with a token can therefore NEVER read
           env — the cross-tenant leak this fix closes.
        2. An explicit request ``username`` (a CLI affordance; the web import route
           has no username field, so it is unreachable from the web).
        3. The ``LASTFM_USERNAME`` env fallback (CLI / local-dev only, where
           ``user_id`` is the ``DEFAULT_USER_ID`` sentinel and holds no token).

        Raises ``LastfmAuthRequiredError`` when nothing resolves (a web user with no
        connected Last.fm account and no env) — surfaced as a clean terminal error.
        """
        token = await self._token_storage.load_token("lastfm", user_id)
        if token is not None:
            account_name = token.get("account_name")
            if account_name:
                return account_name

        if request_username:
            return request_username

        env_username = self.lastfm_connector.lastfm_username
        if env_username:
            return env_username

        raise LastfmAuthRequiredError()

    @staticmethod
    def _require_resolved_username(params: LastfmImportParams) -> str:
        """Narrow the resolved username, failing loudly if resolution was skipped.

        ``import_plays`` always evolves the params with the resolved account
        before entering the pipeline; a None here means a caller bypassed
        ``_resolve_username`` — the exact path that used to silently fall back
        to env (the cross-tenant leak), now a hard error instead.
        """
        if params.username is None:
            raise ValueError(
                "username must be resolved before the import pipeline "
                "(see LastfmPlayImporter._resolve_username)"
            )
        return params.username

    @override
    async def _fetch_data(
        self,
        params: LastfmImportParams,
        *,
        uow: UnitOfWorkProtocol,
        user_id: str,
        batch_id: str,
        import_timestamp: datetime,
        progress_emitter: ProgressEmitter | None = None,
        operation_id: str | None = None,
    ) -> list[ConnectorTrackPlay]:
        """Unified import using checkpoint-bounded date ranges.

        1. Explicit range: from_date/to_date provided (establishes/expands boundaries)
        2. Incremental: no dates (checkpoint-bounded, last run to now)

        Returns ledger rows rather than raw scrobbles — conversion happens per
        window inside the chunking loop (see :meth:`_fetch_date_range_strategy`).
        """
        username = self._require_resolved_username(params)

        explicit_range = params.from_date is not None
        checkpoint = await self._resolve_checkpoint(
            user_id=user_id, account=username, uow=uow
        )

        effective_from, effective_to = self._determine_date_range(
            requested_from=params.from_date,
            requested_to=params.to_date,
            checkpoint=checkpoint,
        )

        logger.info(f"📡 Unified Last.fm import: {effective_from} to {effective_to}")

        # Single code path - always use windowed chunking
        return await self._fetch_date_range_strategy(
            from_date=effective_from,
            to_date=effective_to,
            user_id=user_id,
            username=username,
            batch_id=batch_id,
            import_timestamp=import_timestamp,
            checkpoint=checkpoint,  # Already resolved; avoids a redundant lookup
            progress_emitter=progress_emitter,
            uow=uow,
            explicit_range=explicit_range,
            operation_id=operation_id,
        )

    async def _resolve_checkpoint(
        self, *, user_id: str, account: str, uow: UnitOfWorkProtocol
    ) -> SyncCheckpoint | None:
        """Load the plays sync checkpoint, degrading to None on lookup failure.

        Keyed on the mixd ``user_id``, not the Last.fm account: ``sync_checkpoints``
        is FORCE-RLS'd on ``user_id`` (migrations 007 + 011), so any other key is
        invisible to reads and rejected on write.
        """
        try:
            checkpoint_repository = uow.get_checkpoint_repository()
            checkpoint = await checkpoint_repository.get_sync_checkpoint(
                user_id=user_id, service="lastfm", entity_type="plays"
            )
        except Exception as e:
            logger.warning(f"Checkpoint resolution failed: {e}")
            return None

        if checkpoint is not None and not self._checkpoint_matches_account(
            checkpoint, account
        ):
            return None

        logger.debug(
            f"Checkpoint resolution: found={checkpoint is not None}, "
            f"user={user_id}, account={account}"
        )
        return checkpoint

    @staticmethod
    def _checkpoint_matches_account(checkpoint: SyncCheckpoint, account: str) -> bool:
        """Whether the checkpoint's recorded account is the one being imported.

        The row is keyed on the mixd user, but the account behind it can change
        (reconnecting a different Last.fm account, or just editing
        ``LASTFM_USERNAME`` locally) — and nothing clears the checkpoint when it
        does. Resuming the previous account's position would silently skip all of
        the new account's history before that date, so a mismatch is treated as
        "no checkpoint": the run falls back to the default window and a
        full-history import can backfill the rest. Cursors with no account
        recorded predate this encoding and are accepted as-is.
        """
        if checkpoint.cursor is None:
            return True
        recorded = _decode_cursor(checkpoint.cursor)[1]
        if recorded is None or recorded.casefold() == account.casefold():
            return True

        logger.warning(
            "Last.fm checkpoint belongs to a different account — ignoring it and "
            "importing the default window; run a full-history import to backfill",
            checkpoint_account=recorded,
            requested_account=account,
        )
        return False

    def _determine_date_range(
        self,
        requested_from: datetime | None,
        requested_to: datetime | None,
        checkpoint: SyncCheckpoint | None,
    ) -> tuple[datetime, datetime]:
        """Smart boundary-respecting date range logic.

        Handles both explicit ranges and checkpoint-bounded incremental imports.
        """
        now = datetime.now(UTC)

        # Default to_date is always now
        to_date = requested_to or now

        # From date logic: explicit request vs checkpoint-based
        if requested_from:
            from_date = requested_from
        elif checkpoint and checkpoint.last_timestamp:
            # Incremental: start from last checkpoint
            # Ensure timezone consistency - convert naive to UTC if needed
            from_date = checkpoint.last_timestamp
            if from_date.tzinfo is None:
                from_date = from_date.replace(tzinfo=UTC)
        else:
            # No checkpoint and no explicit date: start from 30 days ago (reasonable default)
            from_date = now - timedelta(days=30)

        return from_date, to_date

    async def _fetch_date_range_strategy(
        self,
        *,
        from_date: datetime,
        to_date: datetime,
        user_id: str,
        username: str,
        batch_id: str,
        import_timestamp: datetime,
        uow: UnitOfWorkProtocol,
        checkpoint: SyncCheckpoint | None = None,
        progress_emitter: ProgressEmitter | None = None,
        explicit_range: bool = False,
        operation_id: str | None = None,
    ) -> list[ConnectorTrackPlay]:
        """Download scrobbles in ``_IMPORT_WINDOW_DAYS``-day windows.

        One ``user.getRecentTracks`` call (paginated at 200/page by the
        client) covers a whole window instead of a single day — the API takes
        arbitrary from/to bounds, so per-day calls were a mixd convention
        costing a full-history import thousands of round trips.

        Each window is converted to ledger rows before the next one is
        fetched, so only the accumulated ``ConnectorTrackPlay`` list survives
        the loop — a full-history import (up to 50,000 plays) never holds the
        raw scrobbles for the whole span on top of it. Each window is also
        persisted and its resume cursor committed together, in that order, so
        the cursor can never point past rows that were never written.

        Args:
            user_id: The mixd user the per-window checkpoints are keyed on, and
                the tenancy stamped onto each window's ledger rows.
            username: The resolved Last.fm account the plays are fetched from.
            batch_id: The import batch each window's rows are tagged with.
            import_timestamp: When the import started — shared by every row so
                the batch has one timestamp, not one per window of the loop.
            uow: Required, with no default, because this loop IS the persistence
                path: ``persists_plays_during_fetch`` is unconditionally True on
                this importer, so the base class skips the run-end save. A
                uow-less call would fetch the whole span, write nothing, and
                still report success — the type system forbids it rather than a
                runtime guard catching it afterwards.
            explicit_range: When True, the caller explicitly requested this date range.
                The checkpoint will NOT override the start date, allowing historical
                fetches even when the checkpoint is ahead of the requested range.
            operation_id: Optional operation ID for progress event emission.
        """
        logger.info(
            f"📡 Fetching tracks with {_IMPORT_WINDOW_DAYS}-day windows: "
            f"from_date={from_date}, to_date={to_date}, user={username}"
        )

        # Adjust start date based on checkpoint for incremental imports
        start_date = self._resolve_chunk_start(
            checkpoint, explicit_range=explicit_range, requested_start=from_date.date()
        )
        end_date = to_date.date()
        total_days = (end_date - start_date).days + 1
        total_windows = math.ceil(total_days / _IMPORT_WINDOW_DAYS)

        # If we're already caught up, return empty
        if start_date > end_date:
            checkpoint_cursor = checkpoint.cursor if checkpoint else "unknown"
            logger.info(
                f"📋 Already up to date: checkpoint date {checkpoint_cursor} is >= end_date {end_date}"
            )
            return []

        all_connector_plays: list[ConnectorTrackPlay] = []
        windows_processed = 0
        batch_commit = getattr(uow, "commit_batch", None)

        # Process each window chronologically (oldest → newest)
        window_start_date = start_date
        while window_start_date <= end_date:
            windows_processed += 1
            window_end_date = min(
                window_start_date + timedelta(days=_IMPORT_WINDOW_DAYS - 1), end_date
            )

            # Window boundaries in UTC, respecting the original time
            # boundaries on the first/last window if more restrictive —
            # exactly as the day boundaries used to clamp.
            window_start = datetime.combine(window_start_date, time.min, UTC)
            window_end = datetime.combine(window_end_date, time.max, UTC)
            effective_start = (
                max(window_start, from_date)
                if window_start_date == start_date
                else window_start
            )
            effective_end = (
                min(window_end, to_date) if window_end_date == end_date else window_end
            )

            window_records = await self._fetch_window_records(
                username=username,
                window_start=effective_start,
                window_end=effective_end,
            )

            self._warn_if_outside_bounds(window_records, effective_start, effective_end)

            # Convert now and let the window's raw records go: this is the
            # only place the two representations of a window coexist, and the
            # loop is already committing per window, so nothing downstream
            # needs them.
            window_plays = self._to_connector_plays(
                window_records,
                user_id=user_id,
                batch_id=batch_id,
                import_timestamp=import_timestamp,
            )
            all_connector_plays.extend(window_plays)

            if progress_emitter and operation_id:
                await progress_emitter.emit_progress(
                    create_progress_event(
                        operation_id=operation_id,
                        current=windows_processed,
                        total=total_windows,
                        message=(
                            f"Fetched {len(all_connector_plays)} plays "
                            f"({windows_processed}/{total_windows} windows)"
                        ),
                    )
                )

            # Invariant: the checkpoint never leads the persisted plays.
            #
            # The window's rows are written first and its cursor second, inside
            # one transaction that the commit below makes durable as a unit —
            # so a crash anywhere leaves the cursor at or behind the last
            # written window, and the resumed run re-fetches from there. That
            # is what "at most one window lost on crash" costs. Persisting the
            # whole span only at the end of the run (as this loop once did)
            # meant a crash at day 300 of 5,100 left a day-300 cursor with
            # ZERO rows written, and days 1-299 were silently skipped forever.
            # A partial window can never reach here: the client raises on a
            # failed page instead of returning a truncated span.
            #
            # Unconditional: ``uow`` is required precisely so this write can
            # never be skipped while the base class also skips the run-end save.
            _ = await self._persist_fetched_chunk(window_plays, uow, user_id=user_id)
            await self._save_window_checkpoint(
                user_id=user_id,
                account=username,
                completed_date=window_end_date,
                window_end=effective_end,
                uow=uow,
            )
            if batch_commit is not None:
                await batch_commit()

            window_start_date = window_end_date + timedelta(days=1)

        logger.info(
            f"📡 Windowed chunking complete: {len(all_connector_plays)} records "
            f"across {windows_processed} windows"
        )

        if checkpoint:
            logger.info(
                f"📋 Incremental import complete: processed {windows_processed} new windows since {checkpoint.cursor}"
            )
        else:
            logger.info(
                f"📋 Full import complete: processed {windows_processed} windows total"
            )

        return all_connector_plays

    @staticmethod
    def _to_connector_plays(
        window_records: list[PlayRecord],
        *,
        user_id: str,
        batch_id: str,
        import_timestamp: datetime,
    ) -> list[ConnectorTrackPlay]:
        """Build one window's ledger rows, tenancy stamped at construction."""
        return [
            ConnectorTrackPlay(
                service="lastfm",
                user_id=user_id,
                track_name=play_record.track_name,
                artist_name=play_record.artist_name,
                album_name=play_record.album_name,
                played_at=play_record.played_at,
                ms_played=play_record.ms_played,  # Will be None for Last.fm
                service_metadata=play_record.service_metadata or {},
                api_page=play_record.api_page,
                raw_data=play_record.raw_data or {},
                import_timestamp=import_timestamp,
                import_source="lastfm_api",
                import_batch_id=batch_id,
            )
            for play_record in window_records
        ]

    @staticmethod
    def _resolve_chunk_start(
        checkpoint: SyncCheckpoint | None,
        *,
        explicit_range: bool,
        requested_start: date,
    ) -> date:
        """Pick the chunking start date: checkpoint resume vs. requested start.

        Resuming always re-processes the checkpoint day (the last completed
        window's final day) to catch new plays.
        When ``explicit_range`` is True the caller explicitly requested this
        range, so the checkpoint never overrides it (historical fetches work
        even when the checkpoint is ahead).
        """
        if not (checkpoint and checkpoint.cursor and not explicit_range):
            return requested_start

        try:
            checkpoint_date = datetime.fromisoformat(
                _decode_cursor(checkpoint.cursor)[0]
            ).date()
        except (ValueError, TypeError) as e:
            logger.warning(
                f"Invalid checkpoint cursor '{checkpoint.cursor}': {e}, starting from beginning"
            )
            return requested_start

        logger.info(
            f"📋 Re-processing checkpoint day: {checkpoint_date} (always redo to catch new plays)"
        )
        # But don't go earlier than the requested from_date
        return max(checkpoint_date, requested_start)

    @staticmethod
    def _warn_if_outside_bounds(
        window_records: list[PlayRecord],
        effective_start: datetime,
        effective_end: datetime,
    ) -> None:
        """Warn when fetched timestamps violate the window's boundary contract."""
        if not window_records:
            return
        window_timestamps = [r.played_at for r in window_records]
        min_ts = min(window_timestamps)
        max_ts = max(window_timestamps)
        if min_ts < effective_start or max_ts > effective_end:
            logger.warning(
                f"Window {effective_start.date()}..{effective_end.date()}: timestamps "
                f"outside expected range! Expected {effective_start} to "
                f"{effective_end}, got {min_ts} to {max_ts}"
            )

    async def _fetch_window_records(
        self,
        username: str,
        window_start: datetime,
        window_end: datetime,
    ) -> list[PlayRecord]:
        """Fetch all plays for one window using pagination.

        Raises ``LastFMPartialFetchError`` (from the client) when a page fails
        mid-pagination — a partial window must fail the run, never checkpoint.
        A window that hits the fetch ceiling is re-fetched one day at a time;
        a single day that still hits it raises the same error.
        """
        logger.debug(f"Fetching window: start={window_start}, end={window_end}")
        records = await self.lastfm_connector.get_recent_tracks(
            username=username,
            limit=LastFMConstants.FULL_HISTORY_LIMIT,  # pagination handles it
            from_time=window_start,
            to_time=window_end,
        )
        if len(records) >= LastFMConstants.FULL_HISTORY_LIMIT:
            # A ceiling-size result is truncated (or indistinguishable from
            # truncated), and the API serves newest-first — the dropped records
            # are the window's OLDEST. Persisting this window would checkpoint
            # past them, and the forward-only cursor never revisits a completed
            # window, so they would be lost forever. Subdivide instead of warn:
            # re-fetch the same span through this same path at the degenerate
            # one-day window, so dense accounts stay importable. The
            # persist→checkpoint→commit invariant is untouched — every
            # subdivided fetch returns into the window's record list before the
            # window's single checkpoint advances. A single day that still
            # ceilings raises: 10,000 plays in one day is beyond any real
            # account, and an honest failure beats silently dropping its tail.
            if window_start.date() == window_end.date():
                raise LastFMPartialFetchError(
                    f"day {window_start.date()} hit the "
                    f"{LastFMConstants.FULL_HISTORY_LIMIT}-record fetch ceiling "
                    "even as a one-day window; refusing to persist a truncated "
                    "day"
                )
            return await self._refetch_window_by_day(
                username=username,
                window_start=window_start,
                window_end=window_end,
            )
        logger.info(
            f"Window {window_start.date()}..{window_end.date()}: {len(records)} plays"
        )
        return records

    async def _refetch_window_by_day(
        self,
        *,
        username: str,
        window_start: datetime,
        window_end: datetime,
    ) -> list[PlayRecord]:
        """Re-fetch a ceiling-hit window as one-day windows via the same path.

        Each day goes back through :meth:`_fetch_window_records` — the one-day
        window is the degenerate case of the windowed fetch, not a second
        implementation — so a day that itself ceilings raises there.
        """
        logger.warning(
            f"Window {window_start.date()}..{window_end.date()} hit the "
            f"{LastFMConstants.FULL_HISTORY_LIMIT}-record fetch ceiling — "
            f"re-fetching one day at a time",
            window_start=window_start.isoformat(),
            window_end=window_end.isoformat(),
        )
        records: list[PlayRecord] = []
        day = window_start.date()
        while day <= window_end.date():
            day_start = max(datetime.combine(day, time.min, UTC), window_start)
            day_end = min(datetime.combine(day, time.max, UTC), window_end)
            records.extend(
                await self._fetch_window_records(
                    username=username,
                    window_start=day_start,
                    window_end=day_end,
                )
            )
            day += timedelta(days=1)
        return records

    async def _save_window_checkpoint(
        self,
        *,
        user_id: str,
        account: str,
        completed_date: date,
        window_end: datetime,
        uow: UnitOfWorkProtocol,
    ) -> None:
        """Save checkpoint after successfully persisting a window.

        Args:
            user_id: The mixd user the checkpoint belongs to. ``sync_checkpoints``
                is FORCE-RLS'd on this column, so it is the only key a write can
                use — anything else is rejected by the ``user_isolation`` policy.
            account: The Last.fm account the window was fetched from, recorded in
                the cursor so a later account switch is detectable.
            completed_date: The window's last calendar day (the resume cursor).
            window_end: End timestamp of the completed window.
            uow: UnitOfWork for database operations with proper transaction context
        """
        try:
            checkpoint = SyncCheckpoint(
                user_id=user_id,
                service="lastfm",
                entity_type="plays",
                last_timestamp=window_end,
                cursor=_encode_cursor(completed_date, account),
            )

            checkpoint_repo = uow.get_checkpoint_repository()
            _ = await checkpoint_repo.save_sync_checkpoint(checkpoint)
            logger.debug(f"Checkpoint saved: user={user_id}, date={completed_date}")

        except Exception as e:
            # Still swallowed — a checkpoint is a resume hint, and losing it must
            # not discard a long import's already-fetched days. But logged at
            # ERROR with a stack trace and the exact key: the previous WARNING
            # with only the message text is what let a permanently-rejected write
            # (wrong RLS key) look like noise for months.
            logger.error(
                "Failed to save Last.fm play checkpoint — the next incremental "
                "import will restart from the default window instead of resuming",
                user_id=user_id,
                account=account,
                completed_date=completed_date.isoformat(),
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True,
            )

    @override
    async def _process_data(
        self,
        raw_data: list[ConnectorTrackPlay],
        *,
        user_id: str,
        batch_id: str,
        import_timestamp: datetime,
    ) -> list[ConnectorTrackPlay]:
        """Pass through: the window loop already built these rows as it fetched.

        Conversion moved into :meth:`_to_connector_plays`, called per fetch
        window, so the span-wide raw list this step used to consume no longer
        exists. Everything it would stamp here — tenancy, batch, timestamp — is
        set at construction there.
        """
        _ = user_id, batch_id, import_timestamp
        return raw_data

    async def _reset_checkpoint_for_full_history(
        self, *, user_id: str, account: str, uow: UnitOfWorkProtocol
    ) -> None:
        """Reset the Last.fm checkpoint so a full-history import starts clean.

        Args:
            user_id: The mixd user whose checkpoint is cleared (the RLS key).
            account: The resolved Last.fm account name, for the log line only.
            uow: Unit of work for database operations.
        """
        checkpoint = SyncCheckpoint(
            user_id=user_id,
            service="lastfm",
            entity_type="plays",
            last_timestamp=None,  # Forces full import
            cursor=None,  # Clears both the resume date and the recorded account
        )

        checkpoint_repo = uow.get_checkpoint_repository()
        _ = await checkpoint_repo.save_sync_checkpoint(checkpoint)

        logger.info(
            f"Reset Last.fm checkpoint for full history import: "
            f"user={user_id}, account={account}"
        )

    @override
    async def _handle_checkpoints(
        self,
        raw_data: list[ConnectorTrackPlay],
        params: LastfmImportParams,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> None:
        """No-op: Last.fm checkpoints are saved per window in _save_window_checkpoint."""
