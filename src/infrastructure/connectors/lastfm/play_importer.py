"""Last.fm-specific play importer implementing connector-only ingestion pattern.

MIGRATED from src/infrastructure/services/lastfm_play_importer.py to keep ALL Last.fm
logic contained within the lastfm connector directory. Contains sophisticated daily
chunking, checkpoint management, and boundary-respecting import logic.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from src.application.services.play_import_orchestrator import PlayImporterProtocol
from src.config import get_logger, settings
from src.config.constants import LastFMConstants
from src.domain.entities import (
    ConnectorTrackPlay,
    OperationResult,
    PlayRecord,
    SyncCheckpoint,
)
from src.domain.repositories.interfaces import UnitOfWorkProtocol
from src.infrastructure.connectors.lastfm.connector import LastFMConnector
from src.infrastructure.services.base_play_importer import (
    BasePlayImporter,
    LastFMImportParams,
)

logger = get_logger(__name__)


class LastfmPlayImporter(BasePlayImporter, PlayImporterProtocol):
    """Last.fm-specific play importer with sophisticated chunking and checkpoint logic.

    MIGRATED from services directory to maintain clean architecture boundaries.
    Implements PlayImporterProtocol for use with generic PlayImportOrchestrator.
    Contains ALL Last.fm-specific logic: daily chunking, checkpoint management, etc.
    """

    def __init__(
        self,
        lastfm_connector: LastFMConnector | None = None,
    ) -> None:
        """Initialize Last.fm play importer for connector-only ingestion pattern.

        Args:
            lastfm_connector: Last.fm API connector (optional, will create if None)
        """
        # Initialize base class with None since we only do connector ingestion
        super().__init__(None)  # type: ignore[arg-type]
        self.operation_name = "Last.fm Connector Play Import"
        self.lastfm_connector = lastfm_connector or LastFMConnector()

    async def import_plays(
        self, uow: UnitOfWorkProtocol, **params: Any
    ) -> tuple[OperationResult, list[ConnectorTrackPlay]]:
        """Import Last.fm plays as connector_plays for later resolution.

        Implements PlayImporterProtocol interface. Uses sophisticated chunking logic.

        Args:
            uow: Unit of work for database operations
            **params: Last.fm parameters (username, from_date, to_date, limit, etc.)

        Returns:
            Tuple of (operation_result, connector_plays_list)
        """
        # Extract common and Last.fm-specific parameters using typed approach
        common_params, lastfm_params = self._extract_common_params(**params)
        typed_params: LastFMImportParams = {**common_params, **lastfm_params}  # type: ignore[misc]

        logger.info(
            "Starting Last.fm connector play ingestion with unified approach",
            username=typed_params.get("username"),
            from_date=typed_params.get("from_date"),
            to_date=typed_params.get("to_date"),
            limit=typed_params.get("limit"),
        )

        # Handle checkpoint reset for full history imports (moved from application layer)
        limit = typed_params.get("limit")
        if (
            limit and limit >= settings.import_settings.full_history_import_threshold
        ):  # Full history import detection
            await self._reset_checkpoint_for_full_history(
                typed_params.get("username"), uow
            )

        # Use migrated sophisticated import logic with typed parameters
        result = await self._import_plays_unified(
            import_batch_id=typed_params.get("import_batch_id"),
            progress_callback=typed_params.get("progress_callback"),
            uow=typed_params.get("uow"),
            from_date=typed_params.get("from_date"),
            to_date=typed_params.get("to_date"),
            username=typed_params.get("username"),
            # Pass any remaining parameters that aren't in the typed interface
            **{
                k: v
                for k, v in lastfm_params.items()
                if k not in {"username", "from_date", "to_date", "limit"}
            },
        )

        # Get the connector plays using base class method
        connector_plays = self._get_stored_connector_plays()

        logger.info(
            "Last.fm connector play ingestion complete",
            connector_plays_ingested=len(connector_plays),
            canonical_plays_created=0,  # Zero - we only do ingestion
        )

        return result, connector_plays

    # === MIGRATED SOPHISTICATED LOGIC FROM ORIGINAL IMPORTER ===

    async def _import_plays_unified(
        self,
        import_batch_id: str | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
        uow: UnitOfWorkProtocol | None = None,
        **kwargs,
    ) -> OperationResult:
        """Import Last.fm plays with unified checkpoint-bounded approach.

        MIGRATED from original LastfmPlayImporter. Sophisticated logic for:
        - Explicit range: Provide from_date/to_date to establish or expand import window
        - Incremental: No dates to import from last checkpoint to now (respects boundaries)
        """
        # Extract Last.fm-specific parameters from kwargs
        from_date = kwargs.get("from_date")
        to_date = kwargs.get("to_date")
        username = kwargs.get("username")

        # Unified approach - always use date range strategy with smart boundaries
        return await self.import_data(
            import_batch_id=import_batch_id,
            progress_callback=progress_callback,
            uow=uow,
            from_date=from_date,
            to_date=to_date,
            username=username,
            **{
                k: v
                for k, v in kwargs.items()
                if k not in {"from_date", "to_date", "username"}
            },
        )

    async def _fetch_data(
        self,
        progress_callback: Callable[[int, int, str], None] | None = None,
        uow: UnitOfWorkProtocol | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        username: str | None = None,
        **kwargs,
    ) -> list[PlayRecord]:
        """Unified import using checkpoint-bounded date ranges.

        MIGRATED sophisticated logic from original importer:
        1. Explicit range: from_date/to_date provided (establishes/expands boundaries)
        2. Incremental: no dates (checkpoint-bounded, last run to now)
        """
        # Unified checkpoint resolution
        checkpoint = await self._resolve_checkpoint(username=username, uow=uow)

        # Smart date range determination
        effective_from, effective_to = self._determine_date_range(
            requested_from=from_date, requested_to=to_date, checkpoint=checkpoint
        )

        logger.info(f"📡 Unified Last.fm import: {effective_from} to {effective_to}")

        # Single code path - always use daily chunking (superior strategy)
        return await self._fetch_date_range_strategy(
            from_date=effective_from,
            to_date=effective_to,
            username=username,
            checkpoint=checkpoint,  # Pass resolved checkpoint to avoid redundant lookup
            progress_callback=progress_callback,
            uow=uow,
            **kwargs,
        )

    async def _resolve_checkpoint(
        self,
        username: str | None = None,
        uow: UnitOfWorkProtocol | None = None,
        require_username: bool = False,
    ) -> SyncCheckpoint | None:
        """Unified checkpoint resolution for all import operations.

        MIGRATED: Eliminates duplicate checkpoint loading logic across methods.
        """

        def _raise_username_required_error() -> None:
            raise ValueError("Username is required for checkpoint operations")

        if not uow:
            return None

        try:
            resolved_username = username or self.lastfm_connector.lastfm_username
            if not resolved_username:
                if require_username:
                    _raise_username_required_error()
                logger.debug("No username available for checkpoint operations")
                return None

            # Get checkpoint repository from UnitOfWork to ensure same transaction context
            checkpoint_repository = uow.get_checkpoint_repository()
            checkpoint = await checkpoint_repository.get_sync_checkpoint(
                user_id=resolved_username, service="lastfm", entity_type="plays"
            )

            logger.debug(
                f"Checkpoint resolution: found={checkpoint is not None}, user={resolved_username}"
            )
            return checkpoint
        except Exception as e:
            logger.warning(f"Checkpoint resolution failed: {e}")
            if require_username:
                raise
            return None

    def _determine_date_range(
        self,
        requested_from: datetime | None,
        requested_to: datetime | None,
        checkpoint: SyncCheckpoint | None,
    ) -> tuple[datetime, datetime]:
        """Smart boundary-respecting date range logic.

        MIGRATED: Handles both explicit ranges and checkpoint-bounded incremental imports.
        """
        now = datetime.now(UTC)

        # Default to_date is always now
        to_date = requested_to or now

        # From date logic: explicit request vs checkpoint-based
        if requested_from:
            from_date = requested_from
        elif checkpoint and checkpoint.last_timestamp:
            # Incremental: start from last checkpoint
            from_date = checkpoint.last_timestamp
        else:
            # No checkpoint and no explicit date: start from 30 days ago (reasonable default)
            from_date = now - timedelta(days=30)

        return from_date, to_date

    async def _fetch_date_range_strategy(
        self,
        from_date: datetime,
        to_date: datetime,
        username: str | None = None,
        checkpoint: SyncCheckpoint | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
        uow: UnitOfWorkProtocol | None = None,
        **additional_options,
    ) -> list[PlayRecord]:
        """Download scrobbles using smart daily chunking with auto-scaling for power users.

        MIGRATED sophisticated chunking logic from original importer.
        Most users listen to <200 tracks/day, so we optimize for daily chunks.
        Only sub-chunk when a day returns exactly 200 tracks (power user case).
        """
        _ = additional_options  # Reserved for future extensibility
        username = username or self.lastfm_connector.lastfm_username
        if not username:
            raise ValueError(
                "No Last.fm username provided or configured (set LASTFM_USERNAME environment variable)"
            )

        logger.info(
            f"📡 Fetching tracks with daily chunking: from_date={from_date}, to_date={to_date}, user={username}"
        )
        logger.debug(
            f"Daily chunking debug: from_date type={type(from_date)}, to_date type={type(to_date)}"
        )

        if not from_date or not to_date:
            raise ValueError(
                "Both from_date and to_date are required for daily chunking strategy"
            )

        if not uow:
            logger.warning("No UnitOfWork provided - checkpoint functionality disabled")

        # Checkpoint parameter contains the already-resolved checkpoint from _fetch_data

        # Adjust start date based on checkpoint for incremental imports
        original_start_date = from_date.date()
        original_end_date = to_date.date()

        if checkpoint and checkpoint.cursor:
            # Resume from checkpoint - always re-process checkpoint day to catch new plays
            try:
                checkpoint_date = datetime.fromisoformat(checkpoint.cursor).date()

                # Always redo the checkpoint day to catch any new tracks that might have been played
                resume_date = checkpoint_date
                logger.info(
                    f"📋 Re-processing checkpoint day: {checkpoint_date} (always redo to catch new plays)"
                )

                # But don't go earlier than the requested from_date
                start_date = max(resume_date, original_start_date)
                logger.debug(
                    f"Checkpoint resume: checkpoint_date={checkpoint_date}, resume_date={resume_date}, final_start={start_date}"
                )
            except (ValueError, TypeError) as e:
                logger.warning(
                    f"Invalid checkpoint cursor '{checkpoint.cursor}': {e}, starting from beginning"
                )
                start_date = original_start_date
        else:
            start_date = original_start_date
            logger.debug(
                f"No checkpoint found, starting from requested date: {start_date}"
            )

        end_date = original_end_date
        total_days = (end_date - start_date).days + 1

        logger.debug(
            f"Daily chunking: start_date={start_date}, end_date={end_date}, total_days={total_days}"
        )
        logger.debug(
            f"Date calculation: original range={original_start_date} to {original_end_date}, effective range={start_date} to {end_date}"
        )

        # If we're already caught up, return empty
        if start_date > end_date:
            checkpoint_cursor = checkpoint.cursor if checkpoint else "unknown"
            logger.info(
                f"📋 Already up to date: checkpoint date {checkpoint_cursor} is >= end_date {end_date}"
            )
            return []

        all_play_records = []
        days_processed = 0

        # Process each day chronologically (oldest → newest)
        current_date = start_date
        while current_date <= end_date:
            days_processed += 1

            if progress_callback:
                progress = (
                    int((days_processed / total_days) * 50) + 40
                )  # 40-90% range for API fetching
                date_str = current_date.strftime("%Y-%m-%d")
                progress_callback(
                    progress,
                    100,
                    f"Fetching {date_str}... ({days_processed}/{total_days} days)",
                )

            # Define day boundaries in UTC
            day_start = datetime.combine(current_date, time.min, UTC)
            day_end = datetime.combine(current_date, time.max, UTC)

            logger.debug(
                f"Day {current_date}: raw boundaries day_start={day_start}, day_end={day_end}"
            )

            # Respect the original time boundaries if they're more restrictive
            effective_start = (
                max(day_start, from_date) if current_date == start_date else day_start
            )
            effective_end = (
                min(day_end, to_date) if current_date == end_date else day_end
            )

            logger.debug(
                f"Day {current_date}: effective boundaries start={effective_start}, end={effective_end}"
            )
            logger.debug(
                f"Day {current_date}: is_first_day={current_date == start_date}, is_last_day={current_date == end_date}"
            )

            day_records = await self._fetch_day_with_auto_scaling(
                username=username,
                day_start=effective_start,
                day_end=effective_end,
                current_date=current_date,
            )

            all_play_records.extend(day_records)
            logger.debug(
                f"Day {current_date}: fetched {len(day_records)} records, total so far: {len(all_play_records)}"
            )

            # Validate day records timestamps are within expected range
            if day_records:
                day_timestamps = [r.played_at for r in day_records]
                min_ts = min(day_timestamps)
                max_ts = max(day_timestamps)
                logger.debug(
                    f"Day {current_date}: timestamp range {min_ts} to {max_ts}"
                )

                # Check if timestamps are actually within the day boundaries
                if min_ts < effective_start or max_ts > effective_end:
                    logger.warning(
                        f"Day {current_date}: timestamps outside expected range! "
                        f"Expected {effective_start} to {effective_end}, got {min_ts} to {max_ts}"
                    )

            # Save checkpoint after successful day completion
            if uow:
                await self._save_day_checkpoint(
                    username=username,
                    completed_date=current_date,
                    day_end=effective_end,
                    uow=uow,
                )

            # Move to next day (simple and reliable)
            prev_date = current_date
            current_date += timedelta(days=1)
            logger.debug(f"Daily iteration: moved from {prev_date} to {current_date}")

        logger.info(
            f"📡 Daily chunking complete: {len(all_play_records)} records across {days_processed} days"
        )

        # Log checkpoint status
        if checkpoint:
            logger.info(
                f"📋 Incremental import complete: processed {days_processed} new days since {checkpoint.cursor}"
            )
        else:
            logger.info(
                f"📋 Full import complete: processed {days_processed} days total"
            )

        return all_play_records

    async def _fetch_day_with_auto_scaling(
        self,
        username: str,
        day_start: datetime,
        day_end: datetime,
        current_date: date,
    ) -> list[PlayRecord]:
        """Fetch a single day with automatic sub-chunking for power users.

        Strategy:
        1. Try to fetch the full day (200 track limit)
        2. If we get exactly 200 tracks, the user might have more - sub-chunk the day
        3. Otherwise, the day is complete
        """
        logger.debug(f"Fetching day {current_date}: start={day_start}, end={day_end}")
        # First attempt: fetch the full day
        logger.debug(f"Day {current_date}: making API call with limit=200")
        day_records = await self.lastfm_connector.get_recent_tracks(
            username=username,
            limit=200,
            from_time=day_start,
            to_time=day_end,
        )

        logger.debug(f"Day {current_date}: API returned {len(day_records)} records")

        # Normal case: day complete in one request
        if len(day_records) < LastFMConstants.RECENT_TRACKS_MAX_LIMIT:
            logger.debug(
                f"Day {current_date}: complete in single request ({len(day_records)} < 200)"
            )
            return day_records

        # Power user case: need sub-chunking
        logger.info(f"Day {current_date} has 200+ plays, using sub-chunking...")
        logger.debug(
            f"Day {current_date}: got exactly 200 records, triggering sub-chunking"
        )
        return await self._sub_chunk_day(
            username=username,
            day_start=day_start,
            day_end=day_end,
            current_date=current_date,
        )

    async def _sub_chunk_day(
        self,
        username: str,
        day_start: datetime,
        day_end: datetime,
        current_date: date,
    ) -> list[PlayRecord]:
        """Sub-chunk a day into smaller time windows for power users.

        Uses progressively smaller windows: 6h -> 4h -> 1h -> sliding windows
        """
        logger.debug(
            f"Sub-chunking day {current_date}: start={day_start}, end={day_end}"
        )

        all_day_records = []

        # Try 6-hour chunks first
        chunk_hours = 6
        current_time = day_end  # Work backwards
        chunk_count = 0

        logger.debug(
            f"Day {current_date}: starting sub-chunking with {chunk_hours}h chunks, working backwards from {current_time}"
        )

        while current_time > day_start:
            chunk_count += 1
            chunk_start = max(day_start, current_time - timedelta(hours=chunk_hours))

            logger.debug(
                f"Day {current_date}: sub-chunk {chunk_count} ({chunk_hours}h): {chunk_start} to {current_time}"
            )

            chunk_records = await self.lastfm_connector.get_recent_tracks(
                username=username,
                limit=200,
                from_time=chunk_start,
                to_time=current_time,
            )

            logger.debug(
                f"Day {current_date}: sub-chunk {chunk_count} returned {len(chunk_records)} records"
            )
            all_day_records.extend(chunk_records)

            # If we got exactly 200 again, we might need smaller chunks
            if (
                len(chunk_records) == LastFMConstants.RECENT_TRACKS_MAX_LIMIT
                and chunk_hours > 1
            ):
                new_chunk_hours = max(1, chunk_hours // 2)
                logger.info(
                    f"Day {current_date}: {chunk_hours}h chunk full, reducing to {new_chunk_hours}h chunks"
                )
                logger.debug(
                    f"Day {current_date}: chunk size reduction {chunk_hours}h -> {new_chunk_hours}h"
                )
                chunk_hours = new_chunk_hours

            prev_time = current_time
            current_time = chunk_start
            logger.debug(
                f"Day {current_date}: sub-chunk iteration: moved from {prev_time} to {current_time}"
            )

        logger.info(
            f"Day {current_date}: sub-chunked into {len(all_day_records)} total records using {chunk_count} chunks"
        )
        logger.debug(
            f"Day {current_date}: sub-chunking complete, final chunk size was {chunk_hours}h"
        )

        # Validate no duplicate timestamps in sub-chunked results
        if all_day_records:
            timestamps = [r.played_at for r in all_day_records]
            unique_timestamps = set(timestamps)
            if len(timestamps) != len(unique_timestamps):
                logger.warning(
                    f"Day {current_date}: found {len(timestamps) - len(unique_timestamps)} duplicate timestamps in sub-chunked results"
                )

        return all_day_records

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
            # Create checkpoint using UnitOfWork transaction context
            checkpoint = SyncCheckpoint(
                user_id=username,
                service="lastfm",
                entity_type="plays",
                last_timestamp=day_end,
                cursor=completed_date.isoformat(),  # Store date as ISO string for easy parsing
            )

            # Use UnitOfWork's checkpoint repository to ensure proper transaction handling
            checkpoint_repo = uow.get_checkpoint_repository()
            await checkpoint_repo.save_sync_checkpoint(checkpoint)
            logger.debug(f"Checkpoint saved: user={username}, date={completed_date}")

        except Exception as e:
            # Don't fail the import for checkpoint errors, just log them
            logger.warning(f"Failed to save checkpoint for day {completed_date}: {e}")

    async def _process_data(
        self,
        raw_data: list[Any],
        batch_id: str,
        import_timestamp: datetime,  # noqa: ARG002 - Required by base class interface
        progress_callback: Callable[[int, int, str], None] | None = None,  # noqa: ARG002 - Required by base class
        uow: UnitOfWorkProtocol | None = None,  # noqa: ARG002 - Required by base class
        **kwargs,  # noqa: ARG002 - Required by base class
    ) -> list[ConnectorTrackPlay]:
        """Convert PlayRecord objects to ConnectorTrackPlay objects.

        Overrides base class to return connector plays instead of canonical plays.
        """
        # raw_data should be list[PlayRecord] in this case
        play_records = raw_data
        connector_plays = []

        for play_record in play_records:
            connector_play = ConnectorTrackPlay(
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
                import_batch_id=batch_id,  # Use the provided batch_id
            )
            connector_plays.append(connector_play)

        return connector_plays

    async def _save_data(
        self, data: list[Any], uow: UnitOfWorkProtocol | None = None
    ) -> tuple[int, int]:
        """Save connector plays using base class method for DRY compliance."""
        if data and not uow:
            raise RuntimeError("UnitOfWork required for Last.fm connector play storage")

        # Use base class method to eliminate duplication
        return await self._save_connector_plays_via_uow(data, uow) if uow else (0, 0)

    async def _reset_checkpoint_for_full_history(
        self, username: str | None, uow: UnitOfWorkProtocol
    ) -> None:
        """Reset Last.fm checkpoint for full history imports.

        Moved from application layer to maintain clean architecture boundaries.

        Args:
            username: Last.fm username (will resolve from connector if None)
            uow: Unit of work for database operations
        """
        # Resolve username from connector if not provided
        resolved_username = username or self.lastfm_connector.lastfm_username
        if not resolved_username:
            logger.warning("Cannot reset checkpoint: no Last.fm username available")
            return

        # Create a new checkpoint with no timestamp (forces full import)
        checkpoint = SyncCheckpoint(
            user_id=resolved_username,
            service="lastfm",
            entity_type="plays",
            last_timestamp=None,
        )

        # Use transaction manager's checkpoint repository
        checkpoint_repo = uow.get_checkpoint_repository()
        await checkpoint_repo.save_sync_checkpoint(checkpoint)

        logger.info(
            f"Reset Last.fm checkpoint for full history import: user={resolved_username}"
        )

    async def _handle_checkpoints(
        self, raw_data: list[Any], uow: UnitOfWorkProtocol | None = None, **kwargs
    ) -> None:
        """Update sync checkpoints to track import progress for incremental syncs.

        Last.fm-specific implementation that handles checkpoint updates after daily processing.
        The actual checkpoint saving is done during daily chunking in _save_day_checkpoint.
        """
        # For Last.fm, checkpoints are handled during the daily chunking process
        # in _save_day_checkpoint method, so this is a no-op
