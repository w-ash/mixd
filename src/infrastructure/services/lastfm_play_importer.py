"""Last.fm play history import service following clean architecture patterns.

Imports play data from Last.fm API using the BasePlayImporter template method pattern
for consistency with other music service imports like Spotify. Uses unified daily chunking
approach with smart checkpoint-bounded incremental imports and automatic track resolution.

Features:
- Smart daily chunking with auto-scaling for power users (200+ tracks/day)
- Checkpoint-bounded incremental imports respecting user's original import window
- Unified code path supporting both explicit date ranges and incremental imports
- Comprehensive track resolution with Last.fm metadata and Spotify discovery
- Resilient operation with automatic retry and error handling
"""

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.application.utilities.results import ImportResultData
from src.config import get_logger
from src.domain.entities import OperationResult, PlayRecord, SyncCheckpoint, TrackPlay
from src.domain.repositories.interfaces import (
    CheckpointRepositoryProtocol,
    ConnectorRepositoryProtocol,
    PlaysRepositoryProtocol,
    TrackRepositoryProtocol,
)
from src.infrastructure.connectors.lastfm import LastFMConnector
from src.infrastructure.services.base_play_importer import BasePlayImporter
from src.infrastructure.services.lastfm_track_resolution_service import (
    LastfmTrackResolutionService,
)

logger = get_logger(__name__)


class LastfmPlayImporter(BasePlayImporter):
    """Imports Last.fm play data using template method pattern for consistency.

    Follows the same clean architecture pattern as SpotifyImportService, extending
    BasePlayImporter to provide consistent workflow, error handling, and progress tracking.
    Uses LastfmTrackResolutionService for track identity resolution and connector mapping.
    """

    def __init__(
        self,
        plays_repository: PlaysRepositoryProtocol,
        checkpoint_repository: CheckpointRepositoryProtocol,
        connector_repository: ConnectorRepositoryProtocol,
        track_repository: TrackRepositoryProtocol,
        lastfm_connector: LastFMConnector | None = None,
        track_resolution_service: LastfmTrackResolutionService | None = None,
    ) -> None:
        """Initialize Last.fm import service with required repositories."""
        super().__init__(plays_repository)
        self.operation_name = "Last.fm Import"
        self.lastfm_connector = lastfm_connector or LastFMConnector()
        self.checkpoint_repository = checkpoint_repository
        self.connector_repository = connector_repository
        self.track_repository = track_repository
        self.track_resolution_service = (
            track_resolution_service or LastfmTrackResolutionService()
        )

    async def import_plays(
        self,
        import_batch_id: str | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
        uow: Any | None = None,
        **kwargs,
    ) -> OperationResult:
        """Import Last.fm plays with unified checkpoint-bounded approach.

        Two patterns:
        1. Explicit range: Provide from_date/to_date to establish or expand import window
        2. Incremental: No dates to import from last checkpoint to now (respects boundaries)

        Supported kwargs:
            from_date: Start date for import (UTC) - establishes/expands window
            to_date: End date for import (UTC) - defaults to now
            username: Last.fm username (defaults to configured username)
            limit: For backward compatibility (ignored in unified approach)

        Args:
            import_batch_id: Optional batch ID for tracking related imports
            progress_callback: Optional callback for progress updates
            uow: UnitOfWork instance for database operations (required)
            **kwargs: Last.fm-specific parameters (from_date, to_date, username)

        Returns:
            OperationResult with import statistics and track resolution metrics
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
        uow: Any | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        username: str | None = None,
        **kwargs,
    ) -> list[PlayRecord]:
        """Unified import using checkpoint-bounded date ranges.

        Two patterns:
        1. Explicit range: from_date/to_date provided (establishes/expands boundaries)
        2. Incremental: no dates (checkpoint-bounded, last run to now)
        """
        # Unified checkpoint resolution
        checkpoint = await self._resolve_checkpoint(username=username, uow=uow)

        # Smart date range determination
        effective_from, effective_to = self._determine_date_range(
            requested_from=from_date, requested_to=to_date, checkpoint=checkpoint
        )

        logger.info(f"📡 Unified import: {effective_from} to {effective_to}")

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
        uow: Any | None = None,
        require_username: bool = False,
    ) -> SyncCheckpoint | None:
        """Unified checkpoint resolution for all import operations.

        Eliminates duplicate checkpoint loading logic across methods.
        Returns None if checkpoint cannot be loaded or doesn't exist.

        Args:
            username: Last.fm username to use for checkpoint lookup
            uow: UnitOfWork instance for database operations
            require_username: If True, raises ValueError when username unavailable
        """
        if not uow or not self.checkpoint_repository:
            return None

        try:
            resolved_username = username or self.lastfm_connector.lastfm_username
            if not resolved_username:
                if require_username:
                    raise ValueError("Username is required for checkpoint operations")
                logger.debug("No username available for checkpoint operations")
                return None

            checkpoint = await self.checkpoint_repository.get_sync_checkpoint(
                user_id=resolved_username, service="lastfm", entity_type="plays"
            )

            logger.debug(
                f"Checkpoint resolution: found={checkpoint is not None}, user={resolved_username}"
            )
            if checkpoint:
                logger.debug(
                    f"Checkpoint details: last_timestamp={checkpoint.last_timestamp}, cursor={checkpoint.cursor}"
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

        Handles both explicit ranges and checkpoint-bounded incremental imports.
        """
        now = datetime.now(UTC)
        default_start = datetime(2005, 1, 1, tzinfo=UTC)  # Last.fm launch year

        if requested_from or requested_to:
            # Explicit range (may establish or expand boundaries)
            start_boundary = None
            if checkpoint and checkpoint.cursor:
                # For now, just use the checkpoint date as boundary
                try:
                    checkpoint_date = datetime.fromisoformat(checkpoint.cursor)
                    start_boundary = checkpoint_date
                except (ValueError, TypeError):
                    pass  # Invalid cursor format

            start = requested_from or start_boundary or default_start
            end = requested_to or now

            logger.debug(f"Explicit range: from={start}, to={end}")
            return start, end
        else:
            if not checkpoint:
                raise ValueError(
                    "No checkpoint found. First run requires explicit --from-date parameter to establish import window."
                )

            # Always redo the checkpoint day to catch any new tracks that might have been played
            if checkpoint.last_timestamp is None:
                raise ValueError("Invalid checkpoint: missing timestamp")
            start = datetime.combine(checkpoint.last_timestamp.date(), time.min, UTC)
            end = now

            logger.debug(f"Incremental: from={start} (checkpoint day redo), to={end}")
            return start, end

    async def _fetch_date_range_strategy(
        self,
        username: str | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        checkpoint: SyncCheckpoint | None = None,
        uow: Any | None = None,
        **additional_options,
    ) -> list[PlayRecord]:
        """Download scrobbles using smart daily chunking with auto-scaling for power users.

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
            if uow and self.checkpoint_repository:
                await self._save_day_checkpoint(
                    username=username,
                    completed_date=current_date,
                    day_end=effective_end,
                    uow=uow,
                )

            # Move to next day (simple and reliable)
            prev_date = current_date
            current_date = current_date + timedelta(days=1)
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
        if len(day_records) < 200:
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
            if len(chunk_records) == 200 and chunk_hours > 1:
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
        uow: Any,  # noqa: ARG002
    ) -> None:
        """Save checkpoint after successfully processing a day.

        Args:
            username: Last.fm username (used as user_id)
            completed_date: The date that was just completed
            day_end: End timestamp of the completed day
            uow: UnitOfWork for database operations (reserved for future use)
        """
        try:
            # For now, simplify by just storing the completed date
            # TODO(https://github.com/narada/narada/issues/pagination-fix): Future enhancement could add boundary tracking to cursor JSON
            checkpoint = SyncCheckpoint(
                user_id=username,
                service="lastfm",
                entity_type="plays",
                last_timestamp=day_end,
                cursor=completed_date.isoformat(),  # Store date as ISO string for easy parsing
            )

            await self.checkpoint_repository.save_sync_checkpoint(checkpoint)
            logger.debug(f"Checkpoint saved: user={username}, date={completed_date}")

        except Exception as e:
            # Don't fail the import for checkpoint errors, just log them
            logger.warning(f"Failed to save checkpoint for day {completed_date}: {e}")

    async def _process_data(
        self,
        raw_data: list[PlayRecord],
        batch_id: str,
        import_timestamp: datetime,
        progress_callback: Callable[[int, int, str], None] | None = None,
        uow: Any | None = None,
        **_kwargs,
    ) -> list[TrackPlay]:
        """Convert Last.fm play records into TrackPlay objects with track resolution.

        Uses LastfmTrackResolutionService to ensure every play has a valid track_id,
        similar to how SpotifyPlayAdapter handles track resolution.
        """
        if not raw_data:
            return []

        if uow is None:
            raise ValueError("UnitOfWork is required for track resolution")

        if progress_callback:
            progress_callback(50, 100, "Resolving track identities...")

        # Resolve play records to canonical tracks using injected service
        (
            resolved_tracks,
            resolution_metrics,
        ) = await self.track_resolution_service.resolve_plays_to_canonical_tracks(
            play_records=raw_data, uow=uow
        )

        if progress_callback:
            progress_callback(
                70, 100, f"Creating {len(resolved_tracks)} play records..."
            )

        # Convert to TrackPlay objects
        track_plays = []
        for i, play_record in enumerate(raw_data):
            canonical_track = resolved_tracks[i] if i < len(resolved_tracks) else None
            if not canonical_track or not canonical_track.id:
                logger.warning(
                    f"Skipping play with unresolved track: {play_record.artist_name} - {play_record.track_name}"
                )
                continue

            track_play = TrackPlay(
                track_id=canonical_track.id,
                service="lastfm",
                played_at=play_record.played_at,
                ms_played=play_record.ms_played
                or 0,  # Default to 0 if duration unknown
                context={
                    **play_record.service_metadata,  # Include all service metadata
                    "album_name": play_record.album_name,
                    "track_name": play_record.track_name,
                    "artist_name": play_record.artist_name,
                    "resolution_method": "lastfm_track_resolution_service",
                    "architecture_version": "clean_architecture_consolidated",
                },
                import_timestamp=import_timestamp,
                import_source="lastfm_api",
                import_batch_id=batch_id,
            )
            track_plays.append(track_play)

        # Store resolution metrics for result creation
        self._last_resolution_metrics = resolution_metrics

        logger.info(
            f"Processed {len(track_plays)} Last.fm plays with track resolution",
            total_records=len(raw_data),
            resolved_plays=len(track_plays),
            new_tracks=resolution_metrics.get("new_tracks_count", 0),
            updated_tracks=resolution_metrics.get("updated_tracks_count", 0),
        )

        return track_plays

    async def _handle_checkpoints(
        self,
        raw_data: list[PlayRecord],  # noqa: ARG002
        uow: Any | None = None,  # noqa: ARG002
        **_kwargs,
    ) -> None:
        """Handle sync checkpoints for Last.fm imports.

        This is a no-op implementation since Last.fm checkpoint handling is managed
        directly in the unified daily chunking strategy (_fetch_date_range_strategy).

        The unified approach handles checkpoints during the import process:
        - Loads checkpoint at import start for resumption logic
        - Saves checkpoint after each completed day during chunking
        - Supports both explicit date ranges and incremental imports

        This method exists to satisfy the BasePlayImporter template but does
        no additional work since checkpoints are handled inline.
        """
        # Checkpoint handling is managed inline during unified daily chunking
        # No additional checkpoint work needed at this stage
        logger.debug("Checkpoint handling managed inline during unified daily chunking")

    def _enrich_import_data(
        self,
        base_data: "ImportResultData",
        raw_data: list[Any],
        track_plays: list[TrackPlay],
    ) -> "ImportResultData":
        """Enrich import data with Last.fm-specific track resolution statistics."""
        from src.application.utilities.results import ImportResultData

        # Get stored resolution metrics from track resolution service
        resolution_metrics = getattr(self, "_last_resolution_metrics", {})

        # Calculate filtered count (tracks that couldn't be resolved)
        resolved_count = sum(1 for play in track_plays if play.track_id is not None)
        filtered_count = len(raw_data) - len(
            track_plays
        )  # Raw records that were filtered out
        error_count = (
            len(raw_data) - resolved_count - filtered_count
        )  # Unresolved tracks

        # Create enriched data with Last.fm-specific statistics
        return ImportResultData(
            raw_data_count=base_data.raw_data_count,
            imported_count=base_data.imported_count,
            filtered_count=filtered_count,
            duplicate_count=base_data.duplicate_count,
            error_count=error_count,
            new_tracks_count=resolution_metrics.get("new_tracks_count", 0),
            updated_tracks_count=resolution_metrics.get("updated_tracks_count", 0),
            batch_id=base_data.batch_id,
            tracks=base_data.tracks,
        )
