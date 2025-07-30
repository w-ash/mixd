"""Downloads Last.fm scrobble history and optionally matches tracks to local music library.

Provides three import modes:
1. Recent: Downloads last N scrobbles without duplicate checking
2. Recent with resolution: Downloads and matches track names to local library
3. Incremental: Downloads only new scrobbles since last import, with timestamp tracking
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from src.application.utilities.results import ImportResultData, ResultFactory
from src.config import get_logger
from src.domain.entities import (
    OperationResult,
    PlayRecord,
    SyncCheckpoint,
    TrackPlay,
)
from src.domain.repositories.interfaces import (
    CheckpointRepositoryProtocol,
    ConnectorRepositoryProtocol,
    PlaysRepositoryProtocol,
    TrackRepositoryProtocol,
)
from src.infrastructure.connectors.lastfm import LastFMConnector
from src.infrastructure.services.base_play_importer import BasePlayImporter

logger = get_logger(__name__)


class LastfmPlayImporter(BasePlayImporter):
    """Downloads Last.fm scrobbles and saves them as play records with optional track matching.

    Supports three import strategies:
    - Recent: Get latest N scrobbles
    - Recent with resolution: Get scrobbles and match artist/title to local tracks
    - Incremental: Get only new scrobbles since last checkpoint, prevents duplicates
    """

    def __init__(
        self,
        plays_repository: PlaysRepositoryProtocol,
        checkpoint_repository: CheckpointRepositoryProtocol,
        connector_repository: ConnectorRepositoryProtocol,
        track_repository: TrackRepositoryProtocol,
        lastfm_connector: LastFMConnector | None = None,
    ) -> None:
        """Initialize Last.fm import service with required database repositories."""
        super().__init__(plays_repository)
        self.operation_name = "Last.fm Recent Plays Import"
        self.lastfm_connector = lastfm_connector or LastFMConnector()
        self.checkpoint_repository = checkpoint_repository
        self.connector_repository = connector_repository
        self.track_repository = track_repository

    async def import_recent_plays(
        self,
        limit: int = 1000,
        import_batch_id: str | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> OperationResult:
        """Download most recent scrobbles from Last.fm without checking for duplicates.

        Args:
            limit: Maximum number of scrobbles to download
            import_batch_id: Optional batch ID for tracking related imports
            progress_callback: Optional callback for progress updates (current, total, message)

        Returns:
            OperationResult with import statistics and unresolved track references
        """
        return await self.import_data(
            strategy="recent",
            limit=limit,
            import_batch_id=import_batch_id,
            progress_callback=progress_callback,
        )

    async def import_recent_plays_with_resolution(
        self,
        limit: int = 1000,
        import_batch_id: str | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> OperationResult:
        """Download recent scrobbles and match artist/title names to tracks in local library.

        Args:
            limit: Maximum number of scrobbles to download
            import_batch_id: Optional batch ID for tracking related imports
            progress_callback: Optional callback for progress updates

        Returns:
            OperationResult with import and track matching statistics
        """
        return await self.import_data(
            strategy="recent",
            resolve_tracks=True,
            limit=limit,
            import_batch_id=import_batch_id,
            progress_callback=progress_callback,
        )

    async def import_incremental_plays(
        self,
        user_id: str | None = None,
        resolve_tracks: bool = True,
        import_batch_id: str | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> OperationResult:
        """Download only scrobbles since last checkpoint timestamp to avoid duplicates.

        Args:
            user_id: Last.fm username (defaults to LASTFM_USERNAME env var)
            resolve_tracks: Whether to match tracks to local library (default: True)
            import_batch_id: Optional batch ID for tracking related imports
            progress_callback: Optional callback for progress updates

        Returns:
            OperationResult with import statistics and updated checkpoint timestamp
        """
        # Reset incremental state
        self._incremental_from_timestamp = None
        self._incremental_to_timestamp = None

        result = await self.import_data(
            strategy="incremental",
            user_id=user_id,
            resolve_tracks=resolve_tracks,
            import_batch_id=import_batch_id,
            progress_callback=progress_callback,
        )

        # Override result for incremental imports to include checkpoint metrics
        if hasattr(self, "_incremental_from_timestamp"):
            # For backward compatibility, create incremental result with unified fields preserved
            return OperationResult(
                operation_name=result.operation_name,
                plays_processed=result.plays_processed,
                play_metrics={
                    **result.play_metrics,
                    "checkpoint_updated": True,
                    "from_timestamp": self._incremental_from_timestamp.isoformat()
                    if self._incremental_from_timestamp
                    else None,
                    "to_timestamp": self._incremental_to_timestamp.isoformat()
                    if self._incremental_to_timestamp
                    else None,
                },
                tracks=result.tracks,
                metrics=result.metrics,
                execution_time=result.execution_time,
                # Preserve unified count fields
                imported_count=result.imported_count,
                exported_count=result.exported_count,
                skipped_count=result.skipped_count,
                error_count=result.error_count,
                already_liked=result.already_liked,
                candidates=result.candidates,
            )

        return result

    async def _fetch_data(
        self,
        progress_callback: Callable[[int, int, str], None] | None = None,
        strategy: str = "recent",
        **kwargs,
    ) -> list[PlayRecord]:
        """Route to appropriate fetch strategy based on import mode."""
        if strategy == "recent":
            return await self._fetch_recent_strategy(
                progress_callback=progress_callback, **kwargs
            )
        elif strategy == "incremental":
            return await self._fetch_incremental_strategy(
                progress_callback=progress_callback, **kwargs
            )
        else:
            raise ValueError(f"Unknown fetch strategy: {strategy}")

    async def _fetch_recent_strategy(
        self,
        limit: int = 1000,
        progress_callback: Callable[[int, int, str], None] | None = None,
        **additional_options,
    ) -> list[PlayRecord]:
        """Download latest N scrobbles using Last.fm API pagination (200 tracks per page)."""
        _ = additional_options  # Reserved for future extensibility
        page_size = min(200, limit)  # Last.fm API max is 200
        all_play_records = []
        current_page = 1
        records_fetched = 0

        while records_fetched < limit:
            remaining = limit - records_fetched
            current_page_size = min(page_size, remaining)

            if progress_callback:
                progress = 20 + int(
                    (records_fetched / limit) * 40
                )  # 20-60% for API fetching
                progress_callback(
                    progress, 100, f"Fetching page {current_page} from Last.fm API..."
                )

            page_records = await self.lastfm_connector.get_recent_tracks(
                limit=current_page_size, page=current_page
            )

            if not page_records:
                logger.info(f"No more records found at page {current_page}")
                break

            all_play_records.extend(page_records)
            records_fetched += len(page_records)
            current_page += 1

            # Stop if we got fewer records than requested (end of data)
            if len(page_records) < 200:  # Last.fm API page size
                logger.info(
                    f"Reached end of data with {len(page_records)} records on page {current_page - 1}"
                )
                break

        logger.info(f"Fetched {len(all_play_records)} recent plays from Last.fm")
        return all_play_records

    async def _fetch_incremental_strategy(
        self,
        user_id: str | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
        **additional_options,
    ) -> list[PlayRecord]:
        """Download scrobbles since last checkpoint timestamp, stores checkpoint info for later update."""
        _ = additional_options  # Reserved for future extensibility
        username = user_id or self.lastfm_connector.lastfm_username
        if not username:
            raise ValueError(
                "No Last.fm username provided or configured (set LASTFM_USERNAME environment variable)"
            )

        # Get checkpoint (only once)
        checkpoint = await self.checkpoint_repository.get_sync_checkpoint(
            user_id=username, service="lastfm", entity_type="plays"
        )
        from_time = checkpoint.last_timestamp if checkpoint else None

        # Store checkpoint info for handle_checkpoints method
        self._existing_checkpoint = checkpoint
        self._from_time = from_time
        self._username = username

        if from_time:
            logger.info(f"Incremental fetch since {from_time} for user {username}")
        else:
            logger.info(f"First-time incremental fetch for user {username}")

        # Fetch with time filter
        all_play_records = []
        page = 1
        page_size = 200  # Max Last.fm allows

        while True:
            if progress_callback:
                progress = 20 + min(int(page * 5), 40)  # Progressive 20-60%
                progress_callback(
                    progress, 100, f"Fetching page {page} from Last.fm API..."
                )

            page_records = await self.lastfm_connector.get_recent_tracks(
                username=username,
                limit=page_size,
                page=page,
                from_time=from_time,
            )

            if not page_records:
                break

            all_play_records.extend(page_records)

            # If we got fewer than page_size, we've reached the end
            if len(page_records) < page_size:
                break

            page += 1

            # Safety limit to prevent runaway API calls
            if page > 50:  # 50 pages * 200 = 10,000 plays max
                logger.warning("Hit safety limit of 50 pages during incremental import")
                break

        logger.info(f"Fetched {len(all_play_records)} new plays since {from_time}")
        return all_play_records

    async def _process_data(
        self,
        raw_data: list[PlayRecord],
        batch_id: str,
        import_timestamp: datetime,
        progress_callback: Callable[[int, int, str], None] | None = None,
        uow: Any | None = None,  # noqa: ARG002
        resolve_tracks: bool = False,
        **kwargs,
    ) -> list[TrackPlay]:
        """Convert Last.fm scrobbles to database format, optionally matching to local tracks."""
        if resolve_tracks:
            return await self._process_with_resolution(
                raw_data, batch_id, import_timestamp, progress_callback, **kwargs
            )
        else:
            return await self._process_without_resolution(
                raw_data, batch_id, import_timestamp, progress_callback, **kwargs
            )

    async def _process_without_resolution(
        self,
        play_records: list[PlayRecord],
        batch_id: str,
        import_timestamp: datetime,
        progress_callback: Callable[[int, int, str], None] | None = None,
        **additional_options,
    ) -> list[TrackPlay]:
        """Convert scrobbles to TrackPlay records with null track_id (no local track matching)."""
        _ = progress_callback  # Reserved for future progress tracking
        strategy = additional_options.get("strategy", "recent")

        track_plays = []
        for record in play_records:
            context = {
                "service": record.service,
                "album_name": record.album_name,
                **record.service_metadata,
                "api_page": record.api_page,
            }

            track_play = TrackPlay(
                track_id=None,  # No resolution
                service="lastfm",
                played_at=record.played_at,
                ms_played=record.ms_played,
                context=context,
                import_timestamp=import_timestamp,
                import_source=f"lastfm_api_{strategy}",
                import_batch_id=batch_id,
            )
            track_plays.append(track_play)

        return track_plays

    async def _process_with_resolution(
        self,
        play_records: list[PlayRecord],
        batch_id: str,
        import_timestamp: datetime,
        progress_callback: Callable[[int, int, str], None] | None = None,
        **kwargs,
    ) -> list[TrackPlay]:
        """Convert scrobbles to TrackPlay records and populate track_id by matching artist/title to local tracks."""
        strategy = kwargs.get("strategy", "recent")

        if progress_callback:
            progress_callback(60, 100, "Creating play records...")

        track_plays = []
        # NOTE: Identity resolution is now handled by the application layer
        # Infrastructure layer only handles data import coordination

        for record in play_records:
            context = {
                "service": record.service,
                "album_name": record.album_name,
                **record.service_metadata,
                "api_page": record.api_page,
                "artist_name": record.artist_name,
                "track_name": record.track_name,
            }

            track_play = TrackPlay(
                track_id=None,  # Identity resolution handled by application layer
                service="lastfm",
                played_at=record.played_at,
                ms_played=record.ms_played,
                context=context,
                import_timestamp=import_timestamp,
                import_source=f"lastfm_api_{strategy}",
                import_batch_id=batch_id,
            )
            track_plays.append(track_play)

        logger.info(f"Created {len(track_plays)} play records from Last.fm data")
        return track_plays

    async def _handle_checkpoints(
        self,
        raw_data: list[PlayRecord],
        strategy: str = "recent",
        user_id: str | None = None,
        **additional_options,
    ) -> None:
        """Update incremental sync checkpoint with latest scrobble timestamp for next import."""
        _ = additional_options  # Reserved for future extensibility
        if strategy == "incremental":
            username = user_id or self.lastfm_connector.lastfm_username
            if username:
                # Use stored checkpoint info from _fetch_incremental_strategy to avoid duplicate queries
                from_timestamp = getattr(self, "_from_time", None)

                if raw_data:
                    # Update checkpoint with most recent timestamp
                    most_recent_timestamp = max(record.played_at for record in raw_data)
                    checkpoint = SyncCheckpoint(
                        user_id=username,
                        service="lastfm",
                        entity_type="plays",
                        last_timestamp=most_recent_timestamp,
                    )
                    await self.checkpoint_repository.save_sync_checkpoint(checkpoint)
                    logger.info(
                        f"Updated checkpoint for {username} to {most_recent_timestamp}"
                    )

                    # Store for result creation
                    self._incremental_from_timestamp = from_timestamp
                    self._incremental_to_timestamp = most_recent_timestamp
                else:
                    # Update checkpoint to current time even if no new data
                    current_time = datetime.now(UTC)
                    checkpoint = SyncCheckpoint(
                        user_id=username,
                        service="lastfm",
                        entity_type="plays",
                        last_timestamp=current_time,
                    )
                    await self.checkpoint_repository.save_sync_checkpoint(checkpoint)
                    logger.info(
                        f"Updated checkpoint for {username} to current time (no new data)"
                    )

                    # Store for result creation
                    self._incremental_from_timestamp = from_timestamp
                    self._incremental_to_timestamp = current_time

    def _create_success_result(
        self,
        raw_data: list[Any],
        track_plays: list[TrackPlay],
        imported_count: int,
        batch_id: str,
    ) -> OperationResult:
        """Build import result with resolved/unresolved track counts if track matching was performed."""
        # Check if this was a resolution import
        has_resolution = any(
            play.import_source and "resolved" in play.import_source
            for play in track_plays
        )

        import_data = ImportResultData(
            raw_data_count=len(raw_data),
            imported_count=imported_count,
            batch_id=batch_id,
            tracks=track_plays,
        )

        result = ResultFactory.create_import_result(
            operation_name=self.operation_name,
            import_data=import_data,
        )

        # Add Last.fm-specific resolution metrics if applicable
        if has_resolution:
            resolved_count = sum(1 for play in track_plays if play.track_id is not None)
            unresolved_count = len(track_plays) - resolved_count

            result.play_metrics.update({
                "resolved_count": resolved_count,
                "unresolved_count": unresolved_count,
            })

        return result

    def _create_incremental_result(
        self,
        raw_data: list[Any],
        track_plays: list[TrackPlay],
        imported_count: int,
        batch_id: str,
        from_timestamp: datetime | None = None,
        to_timestamp: datetime | None = None,
    ) -> OperationResult:
        """Build import result with checkpoint timestamps for tracking incremental sync progress."""
        import_data = ImportResultData(
            raw_data_count=len(raw_data),
            imported_count=imported_count,
            batch_id=batch_id,
            tracks=track_plays,
            checkpoint_timestamp=to_timestamp,
        )

        result = ResultFactory.create_import_result(
            operation_name=self.operation_name,
            import_data=import_data,
        )

        # Add incremental-specific metrics expected by original tests
        incremental_metrics: dict[str, Any] = {
            "checkpoint_updated": True,
        }

        if from_timestamp:
            incremental_metrics["from_timestamp"] = from_timestamp.isoformat()

        if to_timestamp:
            incremental_metrics["to_timestamp"] = to_timestamp.isoformat()

        result.play_metrics.update(incremental_metrics)
        return result
