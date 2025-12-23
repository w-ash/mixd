"""Synchronizes liked tracks between Spotify and Last.fm.

Imports liked tracks from Spotify user libraries and exports them to Last.fm as "loved" tracks.
Supports incremental syncing with checkpoints to resume interrupted operations.
"""

from __future__ import annotations

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


# -------------------------------------------------------------------------
# SHARED CHECKPOINT & LIKE MANAGERS
# -------------------------------------------------------------------------


class CheckpointManager:
    """Manages sync checkpoint operations (get, create, update)."""

    @staticmethod
    async def get_or_create(
        user_id: str,
        service: str,
        entity_type: Literal["likes", "plays"],
        uow: UnitOfWorkProtocol,
    ) -> SyncCheckpoint:
        """Get existing checkpoint or create new one."""
        checkpoint_repo = uow.get_checkpoint_repository()
        checkpoint = await checkpoint_repo.get_sync_checkpoint(
            user_id=user_id, service=service, entity_type=entity_type
        )
        return checkpoint or SyncCheckpoint(
            user_id=user_id, service=service, entity_type=entity_type
        )

    @staticmethod
    async def update(
        checkpoint: SyncCheckpoint,
        uow: UnitOfWorkProtocol,
        timestamp: datetime | None = None,
        cursor: str | None = None,
    ) -> SyncCheckpoint:
        """Update checkpoint with new timestamp/cursor."""
        updated = checkpoint.with_update(
            timestamp=timestamp or datetime.now(UTC), cursor=cursor
        )
        checkpoint_repo = uow.get_checkpoint_repository()
        return await checkpoint_repo.save_sync_checkpoint(updated)


class LikeManager:
    """Manages track like status across services."""

    @staticmethod
    async def save_likes(
        track_id: int,
        uow: UnitOfWorkProtocol,
        services: list[str] | None = None,
        timestamp: datetime | None = None,
        is_liked: bool = True,
    ) -> None:
        """Save track like status across multiple services."""
        services = services or ["narada"]
        now = timestamp or datetime.now(UTC)
        like_repo = uow.get_like_repository()

        for service in services:
            await like_repo.save_track_like(
                track_id=track_id,
                service=service,
                is_liked=is_liked,
                last_synced=now,
            )

    @staticmethod
    async def is_liked_in_all(
        track_id: int,
        services: list[str],
        uow: UnitOfWorkProtocol,
    ) -> bool:
        """Check if track is liked in all specified services."""
        like_repo = uow.get_like_repository()
        for service in services:
            likes = await like_repo.get_track_likes(
                track_id=track_id, services=[service]
            )
            if not any(like.is_liked for like in likes):
                return False
        return True


# -------------------------------------------------------------------------
# COMMAND CLASSES
# -------------------------------------------------------------------------


@define(frozen=True, slots=True)
class ImportSpotifyLikesCommand:
    """Parameters for importing Spotify liked tracks."""

    user_id: str
    limit: int | None = None
    max_imports: int | None = None


@define(frozen=True, slots=True)
class ExportLastFmLikesCommand:
    """Parameters for exporting liked tracks to Last.fm."""

    user_id: str
    batch_size: int | None = None
    max_exports: int | None = None
    override_date: datetime | None = None


@define(frozen=True, slots=True)
class GetSyncCheckpointStatusCommand:
    """Parameters for retrieving sync checkpoint status."""

    service: str
    entity_type: Literal["likes", "plays"]


# -------------------------------------------------------------------------
# USE CASES
# -------------------------------------------------------------------------


@define(slots=True)
class ImportSpotifyLikesUseCase:
    """Imports liked tracks from Spotify into the local database."""

    async def execute(
        self, command: ImportSpotifyLikesCommand, uow: UnitOfWorkProtocol
    ) -> OperationResult:
        """Import Spotify liked tracks with database transaction management."""
        async with uow:
            return await self._import(command, uow)

    async def _import(
        self, command: ImportSpotifyLikesCommand, uow: UnitOfWorkProtocol
    ) -> OperationResult:
        """Fetch and store Spotify liked tracks in batches."""
        batch_size = command.limit or settings.api.spotify_batch_size
        checkpoint = await CheckpointManager.get_or_create(
            command.user_id, "spotify", "likes", uow
        )

        imported = 0
        already_synced = 0
        batches = 0
        cursor = None

        spotify_connector = self._get_connector(uow, "spotify")

        while True:
            if command.max_imports and imported >= command.max_imports:
                logger.info(f"Reached max imports: {command.max_imports}")
                break

            tracks, cursor = await spotify_connector.get_liked_tracks(
                limit=batch_size, cursor=cursor
            )

            if not tracks:
                logger.info("No more tracks to import")
                break

            batch_time = datetime.now(UTC)
            successful_ids = []
            new_in_batch = 0

            for connector_track in tracks:
                try:
                    repo = uow.get_connector_repository()
                    existing = await repo.find_track_by_connector(
                        connector="spotify",
                        connector_id=connector_track.connector_track_identifier,
                    )

                    if existing and existing.id:
                        if await LikeManager.is_liked_in_all(
                            existing.id, ["spotify", "narada"], uow
                        ):
                            already_synced += 1
                            continue
                        successful_ids.append(existing.id)
                        continue

                    ingested = await repo.ingest_external_tracks_bulk(
                        "spotify", [connector_track]
                    )
                    if ingested and ingested[0].id:
                        successful_ids.append(ingested[0].id)
                        new_in_batch += 1

                except Exception:
                    logger.exception(f"Error importing {connector_track.title}")

            # Save likes for successful tracks
            for track_id in successful_ids:
                try:
                    await LikeManager.save_likes(
                        track_id, uow, ["spotify", "narada"], batch_time
                    )
                    imported += 1
                except Exception:
                    logger.exception(f"Error saving likes for track {track_id}")

            batches += 1

            # Early termination if mostly duplicates
            if new_in_batch == 0 and already_synced > len(tracks) * 0.8:
                logger.info("Reached previously synced tracks, stopping")
                break

            # Update checkpoint periodically
            if batches % 10 == 0 or not cursor:
                await CheckpointManager.update(
                    checkpoint, uow, batch_time, cursor
                )

            if not cursor:
                logger.info("Completed import of all Spotify likes")
                break

        logger.info(f"Import complete: {imported} imported, {already_synced} synced")

        result = OperationResult(operation_name="Spotify Likes Import")
        total = imported + already_synced

        # Add summary metrics with display order
        result.summary_metrics.add("imported", imported, "Likes Imported", significance=1)
        result.summary_metrics.add(
            "already_liked", already_synced, "Already Liked ✅", significance=2
        )
        result.summary_metrics.add("candidates", total, "Candidates", significance=3)

        # Calculate and add success rate
        if total > 0:
            success_rate = (imported / total) * 100
            result.summary_metrics.add(
                "success_rate", success_rate, "Success Rate", format="percent", significance=4
            )

        return result

    @staticmethod
    def _get_connector(uow: UnitOfWorkProtocol, service: str) -> Any:
        """Get service connector from UoW."""
        provider = uow.get_service_connector_provider()
        return provider.get_connector(service)


@define(slots=True)
class ExportLastFmLikesUseCase:
    """Exports locally liked tracks to Last.fm as "loved" tracks."""

    async def execute(
        self, command: ExportLastFmLikesCommand, uow: UnitOfWorkProtocol
    ) -> OperationResult:
        """Export liked tracks to Last.fm with database transaction management."""
        async with uow:
            return await self._export(command, uow)

    async def _export(
        self, command: ExportLastFmLikesCommand, uow: UnitOfWorkProtocol
    ) -> OperationResult:
        """Find unsynced likes and export them to Last.fm."""
        batch_size = command.batch_size or settings.api.lastfm_batch_size
        checkpoint = await CheckpointManager.get_or_create(
            command.user_id, "lastfm", "likes", uow
        )

        # Determine timestamp for filtering
        filter_time = command.override_date or checkpoint.last_timestamp
        if filter_time:
            logger.info(f"Incremental export since {filter_time}")

        # Get unsynced likes
        like_repo = uow.get_like_repository()
        unsynced = await like_repo.get_unsynced_likes(
            source_service="narada",
            target_service="lastfm",
            is_liked=True,
            since_timestamp=filter_time,
        )

        total_narada = len(
            await like_repo.get_all_liked_tracks(service="narada", is_liked=True)
        )
        already_loved = total_narada - len(unsynced)

        logger.info(
            f"Export: {total_narada} total, {already_loved} already loved "
            f"({already_loved / total_narada * 100:.1f}%), {len(unsynced)} candidates"
        )

        exported = 0
        filtered = 0
        errors = 0
        lastfm = self._get_connector(uow, "lastfm")

        for i in range(0, len(unsynced), batch_size):
            if command.max_exports and exported >= command.max_exports:
                logger.info(f"Reached max exports: {command.max_exports}")
                break

            batch = unsynced[i : i + batch_size]
            batch_time = datetime.now(UTC)

            # Load tracks for batch
            tracks_to_export = []
            for like in batch:
                if command.max_exports and exported >= command.max_exports:
                    break

                try:
                    track_repo = uow.get_track_repository()
                    tracks_dict = await track_repo.find_tracks_by_ids([like.track_id])

                    if (track := tracks_dict.get(like.track_id)) and track.artists:
                        tracks_to_export.append(track)
                except Exception:
                    logger.exception(f"Error loading track {like.track_id}")
                    errors += 1

            if not tracks_to_export:
                continue

            # Process batch
            results = await self._process_batch(tracks_to_export, lastfm, uow)

            for result in results:
                match result["status"]:
                    case "exported":
                        exported += 1
                    case "skipped":
                        filtered += 1
                    case _:
                        errors += 1

            await CheckpointManager.update(checkpoint, uow, batch_time)

        logger.info(
            f"Export complete: {exported} exported, {filtered} skipped, {errors} errors"
        )

        result = OperationResult(operation_name="Last.fm Likes Export")
        total_candidates = len(unsynced)
        attempted = exported + filtered + errors

        # Add summary metrics with display order
        result.summary_metrics.add("exported", exported, "Exported", significance=1)
        if filtered > 0:
            result.summary_metrics.add(
                "filtered", filtered, "Filtered (Not Found)", significance=2
            )
        if already_loved > 0:
            result.summary_metrics.add(
                "already_loved", already_loved, "Already Loved ✅", significance=3
            )
        if errors > 0:
            result.summary_metrics.add("errors", errors, "Errors", significance=4)
        result.summary_metrics.add("candidates", total_candidates, "Candidates", significance=5)

        # Calculate and add success rate
        if attempted > 0:
            success_rate = (exported / attempted) * 100
            result.summary_metrics.add(
                "success_rate", success_rate, "Success Rate", format="percent", significance=6
            )

        return result

    async def _process_batch(
        self, tracks: list[Track], connector: Any, uow: UnitOfWorkProtocol
    ) -> list[dict]:
        """Process track batch through Last.fm API."""
        results = []
        for track in tracks:
            try:
                result = await self._love_track(track, connector, uow)
                results.append(result)
            except Exception as e:
                logger.exception(f"Error processing track {track.id}")
                results.append({
                    "track_id": track.id,
                    "status": "error",
                    "error": str(e),
                })
        return results

    async def _love_track(
        self, track: Track, connector: Any, uow: UnitOfWorkProtocol
    ) -> dict:
        """Love track on Last.fm and record result."""
        if not track.artists:
            return {
                "track_id": track.id,
                "status": "error",
                "error": "No artists found",
            }

        try:
            success = await connector.love_track(
                artist=track.artists[0].name, title=track.title
            )

            if success:
                if track.id:
                    await LikeManager.save_likes(track.id, uow, ["lastfm"])
                return {"track_id": track.id, "status": "exported"}
            else:
                return {
                    "track_id": track.id,
                    "status": "skipped",
                    "reason": "API returned False",
                }
        except Exception as e:
            return {
                "track_id": track.id,
                "status": "error",
                "error": str(e),
            }

    @staticmethod
    def _get_connector(uow: UnitOfWorkProtocol, service: str) -> Any:
        """Get service connector from UoW."""
        provider = uow.get_service_connector_provider()
        return provider.get_connector(service)


@define(slots=True)
class GetSyncCheckpointStatusUseCase:
    """Retrieves sync checkpoint status for UI display."""

    async def execute(
        self, command: GetSyncCheckpointStatusCommand, uow: UnitOfWorkProtocol
    ) -> SyncCheckpointStatus:
        """Get checkpoint status for a service and entity type."""
        async with uow:
            repo = uow.get_checkpoint_repository()
            checkpoint = await repo.get_sync_checkpoint(
                user_id="default",
                service=command.service,
                entity_type=command.entity_type,
            )

            return SyncCheckpointStatus(
                service=command.service,
                entity_type=command.entity_type,
                last_sync_timestamp=checkpoint.last_timestamp if checkpoint else None,
                has_previous_sync=checkpoint is not None
                and checkpoint.last_timestamp is not None,
            )


# -------------------------------------------------------------------------
# APPLICATION LAYER INTERFACES
# -------------------------------------------------------------------------


async def run_spotify_likes_import(
    user_id: str,
    limit: int | None = None,
    max_imports: int | None = None,
) -> OperationResult:
    """Import Spotify liked tracks into local database."""
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
    """Export locally liked tracks to Last.fm as loved tracks."""
    from src.infrastructure.persistence.database.db_connection import get_session
    from src.infrastructure.persistence.repositories.factories import get_unit_of_work

    async with get_session() as session:
        uow = get_unit_of_work(session)
        command = ExportLastFmLikesCommand(
            user_id=user_id,
            batch_size=batch_size,
            max_exports=max_exports,
            override_date=override_date,
        )
        use_case = ExportLastFmLikesUseCase()
        return await use_case.execute(command, uow)


async def get_sync_checkpoint_status(
    service: str,
    entity_type: Literal["likes", "plays"],
) -> SyncCheckpointStatus:
    """Get sync checkpoint status for UI display."""
    from src.infrastructure.persistence.database.db_connection import get_session
    from src.infrastructure.persistence.repositories.factories import get_unit_of_work

    async with get_session() as session:
        uow = get_unit_of_work(session)
        command = GetSyncCheckpointStatusCommand(
            service=service, entity_type=entity_type
        )
        use_case = GetSyncCheckpointStatusUseCase()
        return await use_case.execute(command, uow)
