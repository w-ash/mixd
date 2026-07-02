"""Last.fm-specific play importer implementing connector-only ingestion.

Contains all Last.fm import logic: token-first account resolution, daily
chunking, checkpoint management, and boundary-respecting date ranges.
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import override

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
from src.infrastructure.connectors.lastfm.connector import LastFMConnector
from src.infrastructure.services.base_play_importer import BasePlayImporter

logger = get_logger(__name__)


class LastfmPlayImporter(
    BasePlayImporter[PlayRecord, LastfmImportParams], PlayImporterProtocol
):
    """Last.fm play importer with daily chunking and checkpoint logic.

    Implements PlayImporterProtocol for use with the generic
    PlayImportOrchestrator. Ingests connector plays only; canonical resolution
    is the resolver's job (two-phase import).
    """

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
        user_id: str | None = None,
        progress_emitter: ProgressEmitter | None = None,
    ) -> tuple[OperationResult, list[ConnectorTrackPlay]]:
        """Import Last.fm plays as connector_plays for later resolution.

        Args:
            uow: Unit of work for database operations
            params: Last.fm import selectors (username, date range, limit)
            user_id: The mixd user id (web path) for token-first account
                resolution; None for CLI/local-dev
            progress_emitter: Optional progress emitter

        Returns:
            Tuple of (operation_result, connector_plays_list)
        """
        if not isinstance(params, LastfmImportParams):
            raise TypeError(
                f"LastfmPlayImporter requires LastfmImportParams, got {type(params).__name__}"
            )

        # Resolve the Last.fm account ONCE, token-first, and pass the concrete
        # name down so the per-day fetch + checkpoint never fall back to env for
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
            await self._reset_checkpoint_for_full_history(resolved_username, uow)

        result, connector_plays = await self.import_data(
            evolve(params, username=resolved_username),
            uow=uow,
            progress_emitter=progress_emitter,
        )

        logger.info(
            "Last.fm connector play ingestion complete",
            connector_plays_ingested=len(connector_plays),
            canonical_plays_created=0,  # Zero - we only do ingestion
        )

        return result, connector_plays

    async def _resolve_username(
        self, request_username: str | None, user_id: str | None
    ) -> str:
        """Resolve the Last.fm account to import, token-first.

        Precedence (2026-canonical user → env, the security crux):
        1. The connected account: the stored OAuth token's ``account_name`` for
           THIS mixd ``user_id``. A web user with a token can therefore NEVER read
           env — the cross-tenant leak this fix closes.
        2. An explicit request ``username`` (a CLI affordance; the web import route
           has no username field, so it is unreachable from the web).
        3. The ``LASTFM_USERNAME`` env fallback (CLI / local-dev only).

        Raises ``LastfmAuthRequiredError`` when nothing resolves (a web user with no
        connected Last.fm account and no env) — surfaced as a clean terminal error.
        """
        if user_id is not None:
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
        progress_emitter: ProgressEmitter | None = None,
        operation_id: str | None = None,
    ) -> list[PlayRecord]:
        """Unified import using checkpoint-bounded date ranges.

        1. Explicit range: from_date/to_date provided (establishes/expands boundaries)
        2. Incremental: no dates (checkpoint-bounded, last run to now)
        """
        username = self._require_resolved_username(params)

        explicit_range = params.from_date is not None
        checkpoint = await self._resolve_checkpoint(username, uow)

        effective_from, effective_to = self._determine_date_range(
            requested_from=params.from_date,
            requested_to=params.to_date,
            checkpoint=checkpoint,
        )

        logger.info(f"📡 Unified Last.fm import: {effective_from} to {effective_to}")

        # Single code path - always use daily chunking
        return await self._fetch_date_range_strategy(
            from_date=effective_from,
            to_date=effective_to,
            username=username,
            checkpoint=checkpoint,  # Already resolved; avoids a redundant lookup
            progress_emitter=progress_emitter,
            uow=uow,
            explicit_range=explicit_range,
            operation_id=operation_id,
        )

    async def _resolve_checkpoint(
        self, username: str, uow: UnitOfWorkProtocol
    ) -> SyncCheckpoint | None:
        """Load the plays sync checkpoint, degrading to None on lookup failure."""
        try:
            checkpoint_repository = uow.get_checkpoint_repository()
            checkpoint = await checkpoint_repository.get_sync_checkpoint(
                user_id=username, service="lastfm", entity_type="plays"
            )
        except Exception as e:
            logger.warning(f"Checkpoint resolution failed: {e}")
            return None

        logger.debug(
            f"Checkpoint resolution: found={checkpoint is not None}, user={username}"
        )
        return checkpoint

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
        from_date: datetime,
        to_date: datetime,
        username: str,
        checkpoint: SyncCheckpoint | None = None,
        progress_emitter: ProgressEmitter | None = None,
        uow: UnitOfWorkProtocol | None = None,
        explicit_range: bool = False,
        operation_id: str | None = None,
    ) -> list[PlayRecord]:
        """Download scrobbles using smart daily chunking.

        Most users listen to <200 tracks/day, so daily chunks are optimal;
        the Last.fm client paginates within a day when needed.

        Args:
            explicit_range: When True, the caller explicitly requested this date range.
                The checkpoint will NOT override the start date, allowing historical
                fetches even when the checkpoint is ahead of the requested range.
            operation_id: Optional operation ID for progress event emission.
        """
        logger.info(
            f"📡 Fetching tracks with daily chunking: from_date={from_date}, to_date={to_date}, user={username}"
        )

        # Adjust start date based on checkpoint for incremental imports
        start_date = self._resolve_chunk_start(
            checkpoint, explicit_range=explicit_range, requested_start=from_date.date()
        )
        end_date = to_date.date()
        total_days = (end_date - start_date).days + 1

        # If we're already caught up, return empty
        if start_date > end_date:
            checkpoint_cursor = checkpoint.cursor if checkpoint else "unknown"
            logger.info(
                f"📋 Already up to date: checkpoint date {checkpoint_cursor} is >= end_date {end_date}"
            )
            return []

        all_play_records: list[PlayRecord] = []
        days_processed = 0
        batch_commit = getattr(uow, "commit_batch", None) if uow else None

        # Process each day chronologically (oldest → newest)
        current_date = start_date
        while current_date <= end_date:
            days_processed += 1

            # Define day boundaries in UTC, respecting the original time
            # boundaries on the first/last day if more restrictive
            day_start = datetime.combine(current_date, time.min, UTC)
            day_end = datetime.combine(current_date, time.max, UTC)
            effective_start = (
                max(day_start, from_date) if current_date == start_date else day_start
            )
            effective_end = (
                min(day_end, to_date) if current_date == end_date else day_end
            )

            day_records = await self._fetch_day_records(
                username=username,
                day_start=effective_start,
                day_end=effective_end,
                current_date=current_date,
            )

            all_play_records.extend(day_records)

            if progress_emitter and operation_id:
                await progress_emitter.emit_progress(
                    create_progress_event(
                        operation_id=operation_id,
                        current=days_processed,
                        total=total_days,
                        message=f"Fetched {len(all_play_records)} plays ({days_processed}/{total_days} days)",
                    )
                )

            self._warn_if_outside_bounds(
                day_records, current_date, effective_start, effective_end
            )

            # Save checkpoint and commit batch after each day so data
            # survives machine restarts (at most one day lost on crash)
            if uow:
                await self._save_day_checkpoint(
                    username=username,
                    completed_date=current_date,
                    day_end=effective_end,
                    uow=uow,
                )
                if batch_commit is not None:
                    await batch_commit()

            current_date += timedelta(days=1)

        logger.info(
            f"📡 Daily chunking complete: {len(all_play_records)} records across {days_processed} days"
        )

        if checkpoint:
            logger.info(
                f"📋 Incremental import complete: processed {days_processed} new days since {checkpoint.cursor}"
            )
        else:
            logger.info(
                f"📋 Full import complete: processed {days_processed} days total"
            )

        return all_play_records

    @staticmethod
    def _resolve_chunk_start(
        checkpoint: SyncCheckpoint | None,
        *,
        explicit_range: bool,
        requested_start: date,
    ) -> date:
        """Pick the chunking start date: checkpoint resume vs. requested start.

        Resuming always re-processes the checkpoint day to catch new plays.
        When ``explicit_range`` is True the caller explicitly requested this
        range, so the checkpoint never overrides it (historical fetches work
        even when the checkpoint is ahead).
        """
        if not (checkpoint and checkpoint.cursor and not explicit_range):
            return requested_start

        try:
            checkpoint_date = datetime.fromisoformat(checkpoint.cursor).date()
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
        day_records: list[PlayRecord],
        current_date: date,
        effective_start: datetime,
        effective_end: datetime,
    ) -> None:
        """Warn when fetched timestamps violate the day's boundary contract."""
        if not day_records:
            return
        day_timestamps = [r.played_at for r in day_records]
        min_ts = min(day_timestamps)
        max_ts = max(day_timestamps)
        if min_ts < effective_start or max_ts > effective_end:
            logger.warning(
                f"Day {current_date}: timestamps outside expected range! "
                + f"Expected {effective_start} to {effective_end}, got {min_ts} to {max_ts}"
            )

    async def _fetch_day_records(
        self,
        username: str,
        day_start: datetime,
        day_end: datetime,
        current_date: date,
    ) -> list[PlayRecord]:
        """Fetch all plays for a single calendar day using pagination."""
        logger.debug(f"Fetching day {current_date}: start={day_start}, end={day_end}")
        records = await self.lastfm_connector.get_recent_tracks(
            username=username,
            limit=LastFMConstants.FULL_HISTORY_LIMIT,  # pagination handles it
            from_time=day_start,
            to_time=day_end,
        )
        logger.info(f"Day {current_date}: {len(records)} plays")
        return records

    async def _save_day_checkpoint(
        self,
        username: str,
        completed_date: date,
        day_end: datetime,
        uow: UnitOfWorkProtocol,
    ) -> None:
        """Save checkpoint after successfully processing a day.

        Args:
            username: Last.fm username (used as user_id)
            completed_date: The date that was just completed
            day_end: End timestamp of the completed day
            uow: UnitOfWork for database operations with proper transaction context
        """
        try:
            checkpoint = SyncCheckpoint(
                user_id=username,
                service="lastfm",
                entity_type="plays",
                last_timestamp=day_end,
                cursor=completed_date.isoformat(),  # ISO string for easy parsing
            )

            checkpoint_repo = uow.get_checkpoint_repository()
            _ = await checkpoint_repo.save_sync_checkpoint(checkpoint)
            logger.debug(f"Checkpoint saved: user={username}, date={completed_date}")

        except Exception as e:
            # Don't fail the import for checkpoint errors, just log them
            logger.warning(f"Failed to save checkpoint for day {completed_date}: {e}")

    @override
    async def _process_data(
        self,
        raw_data: list[PlayRecord],
        *,
        batch_id: str,
        import_timestamp: datetime,
    ) -> list[ConnectorTrackPlay]:
        """Convert PlayRecord objects to ConnectorTrackPlay objects."""
        _ = import_timestamp  # Last.fm stamps each play at conversion time
        return [
            ConnectorTrackPlay(
                service="lastfm",
                track_name=play_record.track_name,
                artist_name=play_record.artist_name,
                album_name=play_record.album_name,
                played_at=play_record.played_at,
                ms_played=play_record.ms_played,  # Will be None for Last.fm
                service_metadata=play_record.service_metadata or {},
                api_page=play_record.api_page,
                raw_data=play_record.raw_data or {},
                import_timestamp=datetime.now(UTC),
                import_source="lastfm_api",
                import_batch_id=batch_id,
            )
            for play_record in raw_data
        ]

    async def _reset_checkpoint_for_full_history(
        self, username: str, uow: UnitOfWorkProtocol
    ) -> None:
        """Reset the Last.fm checkpoint so a full-history import starts clean.

        Args:
            username: The resolved Last.fm account name.
            uow: Unit of work for database operations.
        """
        checkpoint = SyncCheckpoint(
            user_id=username,
            service="lastfm",
            entity_type="plays",
            last_timestamp=None,  # Forces full import
        )

        checkpoint_repo = uow.get_checkpoint_repository()
        _ = await checkpoint_repo.save_sync_checkpoint(checkpoint)

        logger.info(
            f"Reset Last.fm checkpoint for full history import: user={username}"
        )

    @override
    async def _handle_checkpoints(
        self,
        raw_data: list[PlayRecord],
        params: LastfmImportParams,
        uow: UnitOfWorkProtocol,
    ) -> None:
        """No-op: Last.fm checkpoints are saved per day in _save_day_checkpoint."""
