"""Synchronizes liked tracks between Spotify and Last.fm.

Imports liked tracks from Spotify user libraries and exports them to Last.fm as "loved" tracks.
Supports incremental syncing with checkpoints to resume interrupted operations and avoid
re-processing previously synced tracks. Handles batch processing for API rate limits
and provides detailed progress reporting.
"""

from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any, Literal

from attrs import define

from src.config import get_logger, settings
from src.domain.entities import (
    OperationResult,
    SyncCheckpoint,
    SyncCheckpointStatus,
    Track,
)
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


# Command classes for use case operations
@define(frozen=True, slots=True)
class ImportSpotifyLikesCommand:
    """Parameters for importing liked tracks from a Spotify user's library.

    Args:
        user_id: Spotify user identifier for the import operation
        limit: Maximum tracks to fetch per API request (defaults to config value)
        max_imports: Total limit on tracks to import (unlimited if None)
    """

    user_id: str
    limit: int | None = None
    max_imports: int | None = None


@define(frozen=True, slots=True)
class ExportLastFmLikesCommand:
    """Parameters for exporting liked tracks to Last.fm as "loved" tracks.

    Args:
        user_id: Last.fm user identifier for the export operation
        batch_size: Number of tracks to process per batch (defaults to config value)
        max_exports: Total limit on tracks to export (unlimited if None)
        override_date: Override checkpoint date - export tracks since this date
    """

    user_id: str
    batch_size: int | None = None
    max_exports: int | None = None
    override_date: datetime | None = None


@define(frozen=True, slots=True)
class GetSyncCheckpointStatusCommand:
    """Parameters for retrieving sync checkpoint status information.

    Args:
        service: Music service name (spotify, lastfm, etc.)
        entity_type: Type of data being synced (likes, plays)
    """

    service: str
    entity_type: Literal["likes", "plays"]


# Use case implementations
@define(slots=True)
class ImportSpotifyLikesUseCase:
    """Imports liked tracks from Spotify user libraries into the local database.

    Fetches tracks from Spotify's liked songs API, stores new tracks in the database,
    and marks them as liked for both Spotify and Narada services. Uses pagination
    to handle large libraries and checkpoints for resumable operations.
    """

    async def execute(
        self, command: ImportSpotifyLikesCommand, uow: UnitOfWorkProtocol
    ) -> OperationResult:
        """Imports Spotify liked tracks with database transaction management.

        Args:
            command: Import parameters including user ID and limits
            uow: Database transaction and repository access

        Returns:
            Results including count of imported tracks and already synced tracks
        """
        async with uow:
            return await self._import_spotify_likes_internal(
                command.user_id, uow, command.limit, command.max_imports
            )

    async def _import_spotify_likes_internal(
        self,
        user_id: str,
        uow: UnitOfWorkProtocol,
        limit: int | None = None,
        max_imports: int | None = None,
    ) -> OperationResult:
        """Fetches and stores Spotify liked tracks in batches with pagination.

        Processes tracks from Spotify API, checks for existing tracks to avoid duplicates,
        ingests new tracks with metadata, and marks all tracks as liked. Stops early
        when encountering mostly previously synced tracks for efficiency.

        Args:
            user_id: Spotify user identifier
            uow: Database transaction and repository access
            limit: Maximum tracks per API batch
            max_imports: Total import limit

        Returns:
            Import results with counts of processed tracks
        """
        # Get optimal batch size from config
        api_batch_size = limit or settings.api.spotify_batch_size

        # Create checkpoint for tracking
        checkpoint = await self._get_or_create_checkpoint(
            user_id, "spotify", "likes", uow
        )

        # Track stats for reporting
        imported_count = 0
        tracks_found_in_db = 0
        batches_processed = 0
        cursor = None

        # Process in batches with pagination
        while True:
            # Exit if we've reached the maximum import count
            if max_imports is not None and imported_count >= max_imports:
                logger.info(f"Reached maximum import count: {max_imports}")
                break

            # Fetch connector tracks from Spotify
            spotify_connector = self._get_spotify_connector(uow)
            (
                connector_tracks,
                next_cursor,
            ) = await spotify_connector.get_liked_tracks(
                limit=api_batch_size,
                cursor=cursor,
            )

            if not connector_tracks:
                logger.info("No more tracks to import from Spotify")
                break

            # Process tracks in this batch
            batch_timestamp = datetime.now(UTC)
            successful_tracks = []
            new_tracks_in_batch = 0

            for connector_track in connector_tracks:
                try:
                    # Check if this track already exists and is liked
                    connector_repo = uow.get_connector_repository()
                    existing_track = await connector_repo.find_track_by_connector(
                        connector="spotify",
                        connector_id=connector_track.connector_track_id,
                    )

                    if existing_track and existing_track.id is not None:
                        # Check if it's already liked in both services
                        if await self._is_track_already_liked(
                            existing_track.id, ["spotify", "narada"], uow
                        ):
                            tracks_found_in_db += 1
                            logger.debug(
                                f"Track already synced: {connector_track.title}"
                            )
                            continue
                        else:
                            # Track exists but not properly liked, process it
                            successful_tracks.append(existing_track.id)
                            continue

                    # Track doesn't exist, ingest it
                    db_track = await connector_repo.ingest_external_track(
                        connector="spotify",
                        connector_id=connector_track.connector_track_id,
                        metadata=connector_track.raw_metadata,
                        title=connector_track.title,
                        artists=[a.name for a in connector_track.artists],
                        album=connector_track.album,
                        duration_ms=connector_track.duration_ms,
                        release_date=connector_track.release_date,
                        isrc=connector_track.isrc,
                    )

                    if db_track and db_track.id is not None:
                        successful_tracks.append(db_track.id)
                        new_tracks_in_batch += 1
                    else:
                        logger.warning(
                            f"Could not ingest track: {connector_track.title}"
                        )

                except Exception as e:
                    logger.exception(
                        f"Error importing track {connector_track.title}: {e}"
                    )

            # Save likes for all successful tracks
            for track_id in successful_tracks:
                try:
                    await self._save_like_to_services(
                        track_id=track_id,
                        timestamp=batch_timestamp,
                        services=["spotify", "narada"],
                        uow=uow,
                    )
                    imported_count += 1
                except Exception as e:
                    logger.exception(f"Error saving likes for track {track_id}: {e}")

            batches_processed += 1

            # Early termination logic for incremental efficiency
            if (
                new_tracks_in_batch == 0
                and tracks_found_in_db > len(connector_tracks) * 0.8
            ):
                logger.info(
                    "Reached previously synced tracks, stopping incremental sync"
                )
                break

            # Update checkpoint periodically
            if batches_processed % 10 == 0 or not next_cursor:
                await self._update_checkpoint(
                    checkpoint=checkpoint,
                    timestamp=batch_timestamp,
                    cursor=next_cursor,
                    uow=uow,
                )

            # Break if no more pagination
            if not next_cursor:
                logger.info("Completed import of all Spotify likes")
                break

            cursor = next_cursor

        logger.info(
            f"Spotify likes import completed: {imported_count} imported, "
            f"{tracks_found_in_db} already synced"
        )

        return OperationResult(
            operation_name="Spotify Likes Import",
            imported_count=imported_count,
            already_liked=tracks_found_in_db,
            candidates=imported_count + tracks_found_in_db,
        )

    def _get_spotify_connector(self, uow: UnitOfWorkProtocol) -> Any:
        """Retrieves Spotify API connector for fetching liked tracks.

        Args:
            uow: Database transaction and repository access

        Returns:
            Spotify connector with get_liked_tracks method
        """
        service_connector_provider = uow.get_service_connector_provider()
        return service_connector_provider.get_connector("spotify")

    async def _get_or_create_checkpoint(
        self,
        user_id: str,
        service: str,
        entity_type: Literal["likes", "plays"],
        uow: UnitOfWorkProtocol,
    ) -> SyncCheckpoint:
        """Retrieves existing sync checkpoint or creates new one for resumable operations.

        Args:
            user_id: User identifier for the sync operation
            service: Music service name (spotify, lastfm, etc.)
            entity_type: Type of data being synced (likes or plays)
            uow: Database transaction and repository access

        Returns:
            Checkpoint tracking sync progress and pagination state
        """
        checkpoint_repo = uow.get_checkpoint_repository()
        checkpoint = await checkpoint_repo.get_sync_checkpoint(
            user_id=user_id,
            service=service,
            entity_type=entity_type,
        )

        if not checkpoint:
            checkpoint = SyncCheckpoint(
                user_id=user_id,
                service=service,
                entity_type=entity_type,
            )

        return checkpoint

    async def _update_checkpoint(
        self,
        checkpoint: SyncCheckpoint,
        timestamp: datetime | None = None,
        cursor: str | None = None,
        uow: UnitOfWorkProtocol | None = None,
    ) -> SyncCheckpoint:
        """Updates sync checkpoint with new progress timestamp and pagination cursor.

        Args:
            checkpoint: Existing checkpoint to update
            timestamp: New sync timestamp (defaults to current time)
            cursor: API pagination cursor for next request
            uow: Database transaction and repository access

        Returns:
            Updated checkpoint saved to database
        """
        updated = checkpoint.with_update(
            timestamp=timestamp or datetime.now(UTC),
            cursor=cursor,
        )

        if uow is None:
            raise ValueError("UnitOfWork is required for updating checkpoint")
        checkpoint_repo = uow.get_checkpoint_repository()
        return await checkpoint_repo.save_sync_checkpoint(updated)

    async def _save_like_to_services(
        self,
        track_id: int,
        timestamp: datetime | None = None,
        is_liked: bool = True,
        services: list[str] | None = None,
        uow: UnitOfWorkProtocol | None = None,
    ) -> None:
        """Records track like status across multiple music services.

        Args:
            track_id: Database ID of the track to mark as liked
            timestamp: When the like was recorded (defaults to current time)
            is_liked: Whether track is liked (True) or unliked (False)
            services: List of service names to update (defaults to ["narada"])
            uow: Database transaction and repository access
        """
        services = services or ["narada"]
        now = timestamp or datetime.now(UTC)

        if uow is None:
            raise ValueError("UnitOfWork is required for saving likes")
        like_repo = uow.get_like_repository()

        for service in services:
            await like_repo.save_track_like(
                track_id=track_id,
                service=service,
                is_liked=is_liked,
                last_synced=now,
            )

    async def _is_track_already_liked(
        self,
        track_id: int,
        services: list[str],
        uow: UnitOfWorkProtocol,
    ) -> bool:
        """Checks if track is already marked as liked in all specified services.

        Args:
            track_id: Database ID of the track to check
            services: List of service names to check
            uow: Database transaction and repository access

        Returns:
            True if track is liked in all services, False otherwise
        """
        like_repo = uow.get_like_repository()
        for service in services:
            likes = await like_repo.get_track_likes(
                track_id=track_id,
                services=[service],
            )
            if not any(like.is_liked for like in likes):
                return False
        return True


@define(slots=True)
class ExportLastFmLikesUseCase:
    """Exports locally liked tracks to Last.fm as "loved" tracks.

    Finds tracks liked in the local database but not yet marked as loved on Last.fm,
    then uses Last.fm API to love them. Supports incremental syncing to only process
    tracks liked since the last export operation.
    """

    async def execute(
        self, command: ExportLastFmLikesCommand, uow: UnitOfWorkProtocol
    ) -> OperationResult:
        """Exports liked tracks to Last.fm with database transaction management.

        Args:
            command: Export parameters including user ID and limits
            uow: Database transaction and repository access

        Returns:
            Results including count of exported tracks and skipped tracks
        """
        async with uow:
            return await self._export_likes_to_lastfm_internal(
                command.user_id, uow, command.batch_size, command.max_exports, command.override_date
            )

    async def _export_likes_to_lastfm_internal(
        self,
        user_id: str,
        uow: UnitOfWorkProtocol,
        batch_size: int | None = None,
        max_exports: int | None = None,
        override_date: datetime | None = None,
    ) -> OperationResult:
        """Finds unsynced liked tracks and exports them to Last.fm in batches.

        Identifies tracks liked locally but not on Last.fm, then calls Last.fm API
        to love each track. Processes in batches to respect API rate limits and
        updates sync checkpoints for resumable operations.

        Args:
            user_id: Last.fm user identifier
            uow: Database transaction and repository access
            batch_size: Number of tracks per API batch
            max_exports: Total export limit

        Returns:
            Export results with counts of processed tracks
        """
        # Use Last.fm specific batch size from config
        api_batch_size = batch_size or settings.api.lastfm_batch_size

        # Create checkpoint for tracking
        checkpoint = await self._get_or_create_checkpoint(
            user_id, "lastfm", "likes", uow
        )
        
        # Determine which timestamp to use for filtering
        if override_date:
            last_sync_time = override_date
            logger.info(f"Using override date for export: {override_date}")
        else:
            last_sync_time = checkpoint.last_timestamp
            if last_sync_time:
                logger.info(f"Performing incremental export since {last_sync_time}")

        # Get likes that need exporting
        if last_sync_time:
            liked_tracks = await self._get_unsynced_likes(
                source_service="narada",
                target_service="lastfm",
                is_liked=True,
                since_timestamp=last_sync_time,
                uow=uow,
            )
        else:
            logger.info("Performing full export - no checkpoint or override date specified")
            liked_tracks = await self._get_unsynced_likes(
                source_service="narada",
                target_service="lastfm",
                is_liked=True,
                uow=uow,
            )

        # Calculate metrics
        like_repo = uow.get_like_repository()
        total_liked_in_narada = len(
            await like_repo.get_all_liked_tracks(service="narada", is_liked=True)
        )
        candidates = len(liked_tracks)
        already_loved = total_liked_in_narada - candidates

        logger.info(
            f"Export analysis: {total_liked_in_narada} total liked tracks, "
            f"{already_loved} already loved on Last.fm ({already_loved / total_liked_in_narada * 100:.1f}%), "
            f"{candidates} candidates for export"
        )

        exported_count = 0
        filtered_count = 0
        error_count = 0

        # Process tracks in batches using unified batch processor
        for i in range(0, len(liked_tracks), api_batch_size):
            if max_exports is not None and exported_count >= max_exports:
                logger.info(f"Reached maximum export count: {max_exports}")
                break

            batch = liked_tracks[i : i + api_batch_size]
            batch_timestamp = datetime.now(UTC)
            batch_num = i // api_batch_size + 1
            
            logger.debug(
                f"Processing batch {batch_num}: {len(batch)} track likes "
                f"(tracks {i + 1}-{min(i + len(batch), len(liked_tracks))} of {len(liked_tracks)})"
            )

            # Build tracks to match
            tracks_to_match = []
            tracks_loaded = 0
            tracks_missing = 0
            tracks_no_artists = 0
            
            for track_like in batch:
                if max_exports is not None and exported_count >= max_exports:
                    break

                try:
                    track_repo = uow.get_track_repository()
                    tracks_dict = await track_repo.find_tracks_by_ids([
                        track_like.track_id
                    ])
                    track = tracks_dict.get(track_like.track_id)
                    
                    if not track:
                        tracks_missing += 1
                        logger.debug(f"Track {track_like.track_id} not found in database")
                        continue
                        
                    tracks_loaded += 1
                    
                    if not track.artists:
                        tracks_no_artists += 1
                        logger.debug(
                            f"Track {track_like.track_id} skipped - no artists found "
                            f"(title: '{track.title}', album: '{track.album}')"
                        )
                        continue
                        
                    logger.debug(
                        f"Track {track_like.track_id} ready for export - "
                        f"'{track.artists[0].name} - {track.title}' "
                        f"({len(track.artists)} artists)"
                    )
                    tracks_to_match.append(track)
                    
                except Exception as e:
                    logger.exception(
                        f"Error preparing track {track_like.track_id}: {e}"
                    )
                    error_count += 1

            logger.info(
                f"Batch {batch_num} preparation: {len(tracks_to_match)} tracks ready for export, "
                f"{tracks_loaded} loaded, {tracks_missing} missing from DB, "
                f"{tracks_no_artists} without artists"
            )

            if not tracks_to_match:
                logger.warning(
                    f"Batch {batch_num} skipped - no tracks ready for export "
                    f"({tracks_loaded} loaded, {tracks_missing} missing, {tracks_no_artists} no artists)"
                )
                continue

            # Process batch with unified processor
            logger.debug(f"Batch {batch_num}: Sending {len(tracks_to_match)} tracks to Last.fm API")
            lastfm_connector = self._get_lastfm_connector(uow)
            batch_results = await self._process_batch_with_unified_processor(
                tracks=tracks_to_match,
                connector=lastfm_connector,
                processor_func=self._love_track_on_lastfm,
                uow=uow,
            )

            # Update counters based on results
            batch_exported = 0
            batch_skipped = 0
            batch_errors = 0
            
            for result in batch_results:
                if result["status"] == "exported":
                    exported_count += 1
                    batch_exported += 1
                    logger.debug(f"Track {result['track_id']} successfully exported to Last.fm")
                elif result["status"] == "skipped":
                    filtered_count += 1
                    batch_skipped += 1
                    reason = result.get("reason", "unknown reason")
                    logger.debug(f"Track {result['track_id']} skipped: {reason}")
                else:
                    error_count += 1
                    batch_errors += 1
                    error_msg = result.get("error", "unknown error")
                    logger.warning(f"Track {result['track_id']} failed: {error_msg}")
                    
            logger.info(
                f"Batch {batch_num} results: {batch_exported} exported, "
                f"{batch_skipped} skipped, {batch_errors} errors"
            )

            # Update checkpoint
            await self._update_checkpoint(
                checkpoint=checkpoint,
                timestamp=batch_timestamp,
                uow=uow,
            )

        logger.info(
            f"Last.fm loves export completed: {exported_count} exported, "
            f"{filtered_count} skipped out of {candidates} candidates"
        )

        return OperationResult(
            operation_name="Last.fm Likes Export",
            exported_count=exported_count,
            filtered_count=filtered_count,
            error_count=error_count,
            already_liked=already_loved,
            candidates=candidates,
        )

    def _get_lastfm_connector(self, uow: UnitOfWorkProtocol) -> Any:
        """Retrieves Last.fm API connector for loving tracks.

        Args:
            uow: Database transaction and repository access

        Returns:
            Last.fm connector with love_track method
        """
        service_connector_provider = uow.get_service_connector_provider()
        return service_connector_provider.get_connector("lastfm")

    async def _get_or_create_checkpoint(
        self,
        user_id: str,
        service: str,
        entity_type: Literal["likes", "plays"],
        uow: UnitOfWorkProtocol,
    ) -> SyncCheckpoint:
        """Retrieves existing sync checkpoint or creates new one for resumable operations.

        Args:
            user_id: User identifier for the sync operation
            service: Music service name (spotify, lastfm, etc.)
            entity_type: Type of data being synced (likes or plays)
            uow: Database transaction and repository access

        Returns:
            Checkpoint tracking sync progress and pagination state
        """
        checkpoint_repo = uow.get_checkpoint_repository()
        checkpoint = await checkpoint_repo.get_sync_checkpoint(
            user_id=user_id,
            service=service,
            entity_type=entity_type,
        )

        if not checkpoint:
            checkpoint = SyncCheckpoint(
                user_id=user_id,
                service=service,
                entity_type=entity_type,
            )

        return checkpoint

    async def _update_checkpoint(
        self,
        checkpoint: SyncCheckpoint,
        timestamp: datetime | None = None,
        cursor: str | None = None,
        uow: UnitOfWorkProtocol | None = None,
    ) -> SyncCheckpoint:
        """Updates sync checkpoint with new progress timestamp and pagination cursor.

        Args:
            checkpoint: Existing checkpoint to update
            timestamp: New sync timestamp (defaults to current time)
            cursor: API pagination cursor for next request
            uow: Database transaction and repository access

        Returns:
            Updated checkpoint saved to database
        """
        updated = checkpoint.with_update(
            timestamp=timestamp or datetime.now(UTC),
            cursor=cursor,
        )

        if uow is None:
            raise ValueError("UnitOfWork is required for updating checkpoint")
        checkpoint_repo = uow.get_checkpoint_repository()
        return await checkpoint_repo.save_sync_checkpoint(updated)

    async def _get_unsynced_likes(
        self,
        source_service: str,
        target_service: str,
        is_liked: bool = True,
        since_timestamp: datetime | None = None,
        uow: UnitOfWorkProtocol | None = None,
    ) -> list[Any]:
        """Finds tracks liked in source service but not target service.

        Args:
            source_service: Service to get liked tracks from (e.g., "narada")
            target_service: Service to check for missing likes (e.g., "lastfm")
            is_liked: Whether to find liked (True) or unliked (False) tracks
            since_timestamp: Only include tracks liked since this time
            uow: Database transaction and repository access

        Returns:
            List of track like records that need syncing
        """
        if uow is None:
            raise ValueError("UnitOfWork is required for getting unsynced likes")
        like_repo = uow.get_like_repository()
        return await like_repo.get_unsynced_likes(
            source_service=source_service,
            target_service=target_service,
            is_liked=is_liked,
            since_timestamp=since_timestamp,
        )

    async def _process_batch_with_unified_processor(
        self,
        tracks: list[Track],
        connector: Any,
        processor_func: Callable[
            [Track, Any, UnitOfWorkProtocol], Coroutine[Any, Any, dict]
        ],
        uow: UnitOfWorkProtocol,
    ) -> list[dict]:
        """Processes tracks in a batch using a specified processing function.

        Args:
            tracks: List of tracks to process
            connector: Music service API connector
            processor_func: Function to apply to each track
            uow: Database transaction and repository access

        Returns:
            List of results for each processed track with status information
        """
        results = []

        for track in tracks:
            try:
                result = await processor_func(track, connector, uow)
                results.append(result)
            except Exception as e:
                logger.exception(f"Error processing track {track.id}: {e}")
                results.append({
                    "track_id": track.id,
                    "status": "error",
                    "error": str(e),
                })

        return results

    async def _love_track_on_lastfm(
        self, track: Track, connector: Any, uow: UnitOfWorkProtocol
    ) -> dict:
        """Calls Last.fm API to love a track and records the result.

        Args:
            track: Track to love on Last.fm
            connector: Last.fm API connector with love_track method
            uow: Database transaction and repository access

        Returns:
            Dict with track_id, status ("exported", "skipped", "error"), and optional error message
        """
        if not track.artists:
            logger.debug(f"Track {track.id} rejected - no artists found")
            return {
                "track_id": track.id,
                "status": "error",
                "error": "No artists found",
            }

        artist_name = track.artists[0].name
        track_title = track.title
        
        logger.debug(f"Attempting to love track {track.id}: '{artist_name} - {track_title}'")

        try:
            success = await connector.love_track(
                artist=artist_name,
                title=track_title,
            )
            
            logger.debug(f"Last.fm API response for track {track.id}: success={success}")

            if success:
                # Save the like status
                if track.id is not None:
                    await self._save_like_to_services(
                        track_id=track.id,
                        services=["lastfm"],
                        uow=uow,
                    )
                logger.debug(f"Track {track.id} successfully loved and saved to database")
                return {
                    "track_id": track.id,
                    "status": "exported",
                }
            else:
                logger.warning(f"Track {track.id} love API call returned False: '{artist_name} - {track_title}'")
                return {
                    "track_id": track.id,
                    "status": "skipped",
                    "reason": "API call returned False",
                }
        except Exception as e:
            logger.error(f"Exception loving track {track.id} '{artist_name} - {track_title}': {e}")
            return {
                "track_id": track.id,
                "status": "error",
                "error": str(e),
            }

    async def _save_like_to_services(
        self,
        track_id: int,
        timestamp: datetime | None = None,
        is_liked: bool = True,
        services: list[str] | None = None,
        uow: UnitOfWorkProtocol | None = None,
    ) -> None:
        """Records track like status across multiple music services.

        Args:
            track_id: Database ID of the track to mark as liked
            timestamp: When the like was recorded (defaults to current time)
            is_liked: Whether track is liked (True) or unliked (False)
            services: List of service names to update (defaults to ["narada"])
            uow: Database transaction and repository access
        """
        services = services or ["narada"]
        now = timestamp or datetime.now(UTC)

        if uow is None:
            raise ValueError("UnitOfWork is required for saving likes")
        like_repo = uow.get_like_repository()

        for service in services:
            await like_repo.save_track_like(
                track_id=track_id,
                service=service,
                is_liked=is_liked,
                last_synced=now,
            )


@define(slots=True)
class GetSyncCheckpointStatusUseCase:
    """Retrieves sync checkpoint status information for UI display."""

    async def execute(
        self, command: GetSyncCheckpointStatusCommand, uow: UnitOfWorkProtocol
    ) -> SyncCheckpointStatus:
        """Gets checkpoint status for a service and entity type.

        Args:
            command: Parameters specifying which checkpoint to check
            uow: Database transaction and repository access

        Returns:
            Checkpoint status information for UI display
        """
        async with uow:
            checkpoint_repo = uow.get_checkpoint_repository()
            
            # We don't need a user_id for status display since we're using "default"
            checkpoint = await checkpoint_repo.get_sync_checkpoint(
                user_id="default",
                service=command.service,
                entity_type=command.entity_type,
            )

            return SyncCheckpointStatus(
                service=command.service,
                entity_type=command.entity_type,
                last_sync_timestamp=checkpoint.last_timestamp if checkpoint else None,
                has_previous_sync=checkpoint is not None and checkpoint.last_timestamp is not None,
            )


# Application layer interfaces for CLI integration
async def run_spotify_likes_import(
    user_id: str,
    limit: int | None = None,
    max_imports: int | None = None,
) -> OperationResult:
    """Imports Spotify liked tracks into the local database.

    Creates database session and executes Spotify import use case with the provided
    parameters. Handles session management and dependency injection automatically.

    Args:
        user_id: Spotify user ID for the import operation
        limit: Maximum number of likes to import per batch
        max_imports: Maximum total number of likes to import

    Returns:
        OperationResult with import statistics and status
    """
    from src.infrastructure.persistence.database.db_connection import get_session
    from src.infrastructure.persistence.repositories.factories import get_unit_of_work

    async with get_session() as session:
        uow = get_unit_of_work(session)
        command = ImportSpotifyLikesCommand(
            user_id=user_id, limit=limit, max_imports=max_imports
        )
        use_case = ImportSpotifyLikesUseCase()
        return await use_case.execute(command, uow)


async def run_lastfm_likes_export(
    user_id: str,
    batch_size: int | None = None,
    max_exports: int | None = None,
    override_date: datetime | None = None,
) -> OperationResult:
    """Exports locally liked tracks to Last.fm as loved tracks.

    Creates database session and executes Last.fm export use case with the provided
    parameters. Handles session management and dependency injection automatically.

    Args:
        user_id: Last.fm user ID for the export operation
        batch_size: Number of tracks to process per batch
        max_exports: Maximum total number of tracks to export
        override_date: Override checkpoint date - export tracks since this date

    Returns:
        OperationResult with export statistics and status
    """
    from src.infrastructure.persistence.database.db_connection import get_session
    from src.infrastructure.persistence.repositories.factories import get_unit_of_work

    async with get_session() as session:
        uow = get_unit_of_work(session)
        command = ExportLastFmLikesCommand(
            user_id=user_id, batch_size=batch_size, max_exports=max_exports, override_date=override_date
        )
        use_case = ExportLastFmLikesUseCase()
        return await use_case.execute(command, uow)


async def get_sync_checkpoint_status(
    service: str,
    entity_type: Literal["likes", "plays"],
) -> SyncCheckpointStatus:
    """Get sync checkpoint status for UI display.

    Creates database session and executes checkpoint status use case with the provided
    parameters. Handles session management and dependency injection automatically.

    Args:
        service: Music service name (spotify, lastfm, etc.)
        entity_type: Type of data being synced (likes, plays)

    Returns:
        SyncCheckpointStatus with checkpoint information for UI display
    """
    from src.infrastructure.persistence.database.db_connection import get_session
    from src.infrastructure.persistence.repositories.factories import get_unit_of_work

    async with get_session() as session:
        uow = get_unit_of_work(session)
        command = GetSyncCheckpointStatusCommand(
            service=service, entity_type=entity_type
        )
        use_case = GetSyncCheckpointStatusUseCase()
        return await use_case.execute(command, uow)
