"""Consolidated like service for all like-related operations.

Provides unified interface for importing, exporting, and synchronizing
track likes between different music services. Handles progress reporting
and checkpoint management for incremental sync operations.
"""

from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any, Literal

from attrs import define

from src.config import get_config, get_logger
from src.domain.entities import OperationResult, SyncCheckpoint, Track
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


# Command classes for Clean Architecture use case pattern
@define(frozen=True, slots=True)
class ImportSpotifyLikesCommand:
    """Command for importing liked tracks from Spotify."""

    user_id: str
    limit: int | None = None
    max_imports: int | None = None


@define(frozen=True, slots=True)
class ExportLastFmLikesCommand:
    """Command for exporting liked tracks to Last.fm."""

    user_id: str
    batch_size: int | None = None
    max_exports: int | None = None


# Service connector protocols are now defined in domain layer interfaces


# LikeImportResult and LikeExportResult classes removed
# All functionality is now available in the unified OperationResult class


# Clean Architecture Use Cases - Replacing LikeService with proper use case pattern
@define(slots=True)
class ImportSpotifyLikesUseCase:
    """Use case for importing liked tracks from Spotify.

    Follows Clean Architecture pattern with UnitOfWork parameter injection.
    No constructor dependencies - pure domain layer compliance.
    """

    async def execute(
        self, command: ImportSpotifyLikesCommand, uow: UnitOfWorkProtocol
    ) -> OperationResult:
        """Execute Spotify likes import with explicit transaction control."""
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
        """Internal implementation of Spotify likes import."""
        # Get optimal batch size from config
        api_batch_size = limit or get_config("SPOTIFY_API_BATCH_SIZE", 50) or 50

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
        """Get Spotify connector from UnitOfWork.

        Returns:
            Spotify connector instance (expected to have get_liked_tracks method)
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
        """Get existing checkpoint or create a new one."""
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
        """Update the checkpoint with new timestamp and cursor."""
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
        """Save like status to multiple services at once."""
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
        """Check if track is already liked in all specified services."""
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
    """Use case for exporting liked tracks to Last.fm.

    Follows Clean Architecture pattern with UnitOfWork parameter injection.
    No constructor dependencies - pure domain layer compliance.
    """

    async def execute(
        self, command: ExportLastFmLikesCommand, uow: UnitOfWorkProtocol
    ) -> OperationResult:
        """Execute Last.fm likes export with explicit transaction control."""
        async with uow:
            return await self._export_likes_to_lastfm_internal(
                command.user_id, uow, command.batch_size, command.max_exports
            )

    async def _export_likes_to_lastfm_internal(
        self,
        user_id: str,
        uow: UnitOfWorkProtocol,
        batch_size: int | None = None,
        max_exports: int | None = None,
    ) -> OperationResult:
        """Internal implementation of Last.fm likes export."""
        # Use Last.fm specific batch size from config
        api_batch_size = batch_size or get_config("LASTFM_API_BATCH_SIZE", 20) or 20

        # Create checkpoint for tracking
        checkpoint = await self._get_or_create_checkpoint(
            user_id, "lastfm", "likes", uow
        )
        last_sync_time = checkpoint.last_timestamp

        # Get likes that need exporting
        if last_sync_time:
            logger.info(f"Performing incremental export since {last_sync_time}")
            liked_tracks = await self._get_unsynced_likes(
                source_service="narada",
                target_service="lastfm",
                is_liked=True,
                since_timestamp=last_sync_time,
                uow=uow,
            )
        else:
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
        skipped_count = 0
        error_count = 0

        # Process tracks in batches using unified batch processor
        for i in range(0, len(liked_tracks), api_batch_size):
            if max_exports is not None and exported_count >= max_exports:
                logger.info(f"Reached maximum export count: {max_exports}")
                break

            batch = liked_tracks[i : i + api_batch_size]
            batch_timestamp = datetime.now(UTC)

            # Build tracks to match
            tracks_to_match = []
            for track_like in batch:
                if max_exports is not None and exported_count >= max_exports:
                    break

                try:
                    track_repo = uow.get_track_repository()
                    tracks_dict = await track_repo.find_tracks_by_ids([
                        track_like.track_id
                    ])
                    track = tracks_dict.get(track_like.track_id)
                    if track and track.artists:
                        tracks_to_match.append(track)
                except Exception as e:
                    logger.exception(
                        f"Error preparing track {track_like.track_id}: {e}"
                    )
                    error_count += 1

            if not tracks_to_match:
                continue

            # Process batch with unified processor
            lastfm_connector = self._get_lastfm_connector(uow)
            batch_results = await self._process_batch_with_unified_processor(
                tracks=tracks_to_match,
                connector=lastfm_connector,
                processor_func=self._love_track_on_lastfm,
                uow=uow,
            )

            # Update counters based on results
            for result in batch_results:
                if result["status"] == "exported":
                    exported_count += 1
                elif result["status"] == "skipped":
                    skipped_count += 1
                else:
                    error_count += 1

            # Update checkpoint
            await self._update_checkpoint(
                checkpoint=checkpoint,
                timestamp=batch_timestamp,
                uow=uow,
            )

        logger.info(
            f"Last.fm loves export completed: {exported_count} exported, "
            f"{skipped_count} skipped out of {candidates} candidates"
        )

        return OperationResult(
            operation_name="Last.fm Likes Export",
            exported_count=exported_count,
            skipped_count=skipped_count,
            error_count=error_count,
            already_liked=already_loved,
            candidates=candidates,
        )

    def _get_lastfm_connector(self, uow: UnitOfWorkProtocol) -> Any:
        """Get Last.fm connector from UnitOfWork.

        Returns:
            Last.fm connector instance (expected to have love_track method)
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
        """Get existing checkpoint or create a new one."""
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
        """Update the checkpoint with new timestamp and cursor."""
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
        """Get tracks that need like status syncing."""
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
        """Unified batch processor that replaces duplicate processing patterns."""
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
        """Process a single track for Last.fm loving.

        Args:
            track: Track to love on Last.fm
            connector: Music service connector (expected to have love_track method)
            uow: Unit of work for transaction management
        """
        if not track.artists:
            return {
                "track_id": track.id,
                "status": "error",
                "error": "No artists found",
            }

        artist_name = track.artists[0].name
        track_title = track.title

        try:
            success = await connector.love_track(
                artist_name=artist_name,
                track_title=track_title,
            )

            if success:
                # Save the like status
                if track.id is not None:
                    await self._save_like_to_services(
                        track_id=track.id,
                        services=["lastfm"],
                        uow=uow,
                    )
                return {
                    "track_id": track.id,
                    "status": "exported",
                }
            else:
                return {
                    "track_id": track.id,
                    "status": "skipped",
                    "reason": "API call failed",
                }
        except Exception as e:
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
        """Save like status to multiple services at once."""
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


# Application layer interfaces for CLI integration
async def run_spotify_likes_import(
    user_id: str,
    limit: int | None = None,
    max_imports: int | None = None,
) -> OperationResult:
    """Application layer interface for Spotify likes import.

    Provides a clean boundary between CLI and use case layers by handling
    command object creation, use case instantiation, and session management.
    This follows Clean Architecture patterns for interface layers.

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
) -> OperationResult:
    """Application layer interface for Last.fm likes export.

    Provides a clean boundary between CLI and use case layers by handling
    command object creation, use case instantiation, and session management.
    This follows Clean Architecture patterns for interface layers.

    Args:
        user_id: Last.fm user ID for the export operation
        batch_size: Number of tracks to process per batch
        max_exports: Maximum total number of tracks to export

    Returns:
        OperationResult with export statistics and status
    """
    from src.infrastructure.persistence.database.db_connection import get_session
    from src.infrastructure.persistence.repositories.factories import get_unit_of_work

    async with get_session() as session:
        uow = get_unit_of_work(session)
        command = ExportLastFmLikesCommand(
            user_id=user_id, batch_size=batch_size, max_exports=max_exports
        )
        use_case = ExportLastFmLikesUseCase()
        return await use_case.execute(command, uow)
