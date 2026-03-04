"""Synchronizes liked tracks between Spotify and Last.fm.

Imports liked tracks from Spotify user libraries and exports them to Last.fm as "loved" tracks.
Supports incremental syncing with checkpoints to resume interrupted operations.
"""

from datetime import UTC, datetime
from typing import Literal

from attrs import define

from src.application.connector_protocols import LoveTrackConnector
from src.application.use_cases._shared.connector_resolver import (
    resolve_liked_track_connector,
    resolve_love_track_connector,
)
from src.application.utilities.batch_results import BatchItemResult, BatchItemStatus
from src.config import get_logger, settings
from src.config.constants import BusinessLimits
from src.domain.entities import (
    ConnectorTrack,
    OperationResult,
    SyncCheckpoint,
    SyncCheckpointStatus,
    Track,
)
from src.domain.entities.operations import UNSET, Unset
from src.domain.entities.progress import ProgressEmitter
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


# -------------------------------------------------------------------------
# SHARED CHECKPOINT & LIKE HELPERS
# -------------------------------------------------------------------------


async def get_or_create_checkpoint(
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


async def update_checkpoint(
    checkpoint: SyncCheckpoint,
    uow: UnitOfWorkProtocol,
    timestamp: datetime | None = None,
    cursor: str | Unset | None = UNSET,
) -> SyncCheckpoint:
    """Update checkpoint with new timestamp/cursor."""
    updated = checkpoint.with_update(
        timestamp=timestamp or datetime.now(UTC), cursor=cursor
    )
    checkpoint_repo = uow.get_checkpoint_repository()
    return await checkpoint_repo.save_sync_checkpoint(updated)


async def save_likes(
    track_id: int,
    uow: UnitOfWorkProtocol,
    services: list[str] | None = None,
    timestamp: datetime | None = None,
    is_liked: bool = True,
    liked_at: datetime | None = None,
) -> None:
    """Save track like status across multiple services.

    Args:
        track_id: Internal track ID.
        uow: Unit of work for transaction management.
        services: Services to save likes for (defaults to ["narada"]).
        timestamp: When this sync happened (used for last_synced).
        is_liked: Whether the track is liked.
        liked_at: When the user originally liked the track.
    """
    services = services or ["narada"]
    now = timestamp or datetime.now(UTC)
    like_repo = uow.get_like_repository()

    for service in services:
        _ = await like_repo.save_track_like(
            track_id=track_id,
            service=service,
            is_liked=is_liked,
            last_synced=now,
            liked_at=liked_at,
        )


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


# All known service/entity combinations for checkpoint queries.
CHECKPOINT_COMBINATIONS: tuple[tuple[str, Literal["likes", "plays"]], ...] = (
    ("spotify", "likes"),
    ("lastfm", "likes"),
    ("lastfm", "plays"),
    ("spotify", "plays"),
)


# -------------------------------------------------------------------------
# USE CASES
# -------------------------------------------------------------------------


@define(slots=True)
class ImportSpotifyLikesUseCase:
    """Imports liked tracks from Spotify into the local database."""

    async def execute(
        self,
        command: ImportSpotifyLikesCommand,
        uow: UnitOfWorkProtocol,
        progress_emitter: ProgressEmitter | None = None,
    ) -> OperationResult:
        """Import Spotify liked tracks with database transaction management."""
        from src.domain.entities.progress import NullProgressEmitter

        emitter = progress_emitter or NullProgressEmitter()
        async with uow:
            return await self._import(command, uow, emitter)

    async def _import(
        self,
        command: ImportSpotifyLikesCommand,
        uow: UnitOfWorkProtocol,
        emitter: ProgressEmitter,
    ) -> OperationResult:
        """Fetch and store Spotify liked tracks in batches."""
        from src.domain.entities.progress import tracked_operation

        async with tracked_operation(
            emitter, "Importing Spotify Likes"
        ) as operation_id:
            return await self._import_inner(command, uow, emitter, operation_id)

    async def _import_inner(
        self,
        command: ImportSpotifyLikesCommand,
        uow: UnitOfWorkProtocol,
        emitter: ProgressEmitter,
        operation_id: str,
    ) -> OperationResult:
        from src.domain.entities.progress import create_progress_event

        batch_size = command.limit or settings.api.spotify_batch_size
        checkpoint = await get_or_create_checkpoint(
            command.user_id, "spotify", "likes", uow
        )

        imported = 0
        already_synced = 0
        batches = 0
        cursor = None

        spotify_connector = resolve_liked_track_connector(uow)

        while True:
            if command.max_imports and imported >= command.max_imports:
                logger.info(f"Reached max imports: {command.max_imports}")
                break

            await emitter.emit_progress(
                create_progress_event(
                    operation_id,
                    current=imported + already_synced,
                    total=None,
                    message=f"Fetching batch {batches + 1}...",
                )
            )

            tracks, cursor = await spotify_connector.get_liked_tracks(
                limit=batch_size, cursor=cursor
            )

            if not tracks:
                logger.info("No more tracks to import")
                break

            batch_time = datetime.now(UTC)
            repo = uow.get_connector_repository()

            # 1. Bulk-find existing tracks (1 query instead of N)
            # Include linked_from alternate IDs so relinked tracks are found
            connections: list[tuple[str, str]] = [
                ("spotify", ct.connector_track_identifier) for ct in tracks
            ]
            for ct in tracks:
                alt = ct.raw_metadata.get("linked_from_id")
                if alt and alt != ct.connector_track_identifier:
                    connections.append(("spotify", alt))

            try:
                existing_map = await repo.find_tracks_by_connectors(connections)
            except Exception:
                logger.exception("Error bulk-finding tracks")
                existing_map = {}

            # 2. Partition into existing vs new
            new_tracks: list[ConnectorTrack] = []
            existing_ids: list[int] = []
            # Map connector_track_identifier → ConnectorTrack for liked_at extraction
            ct_by_id: dict[str, ConnectorTrack] = {
                ct.connector_track_identifier: ct for ct in tracks
            }

            for ct in tracks:
                key = ("spotify", ct.connector_track_identifier)
                existing_track = existing_map.get(key)
                # Fall back to linked_from alternate ID
                if not existing_track:
                    alt = ct.raw_metadata.get("linked_from_id")
                    if alt:
                        existing_track = existing_map.get(("spotify", alt))
                if existing_track and existing_track.id:
                    existing_ids.append(existing_track.id)
                else:
                    new_tracks.append(ct)

            # 3. Bulk-check like status for existing tracks (1 query)
            batch_already_synced = 0
            needs_likes: list[int] = []
            if existing_ids:
                like_repo = uow.get_like_repository()
                like_status = await like_repo.get_liked_status_batch(
                    existing_ids, ["spotify", "narada"]
                )
                for track_id in existing_ids:
                    statuses = like_status.get(track_id, {})
                    if all(statuses.get(s, False) for s in ("spotify", "narada")):
                        already_synced += 1
                        batch_already_synced += 1
                    else:
                        needs_likes.append(track_id)

            # 4. Bulk-ingest new tracks (1 query via ingest_external_tracks_bulk)
            new_in_batch = 0
            ingested: list[Track] = []
            if new_tracks:
                try:
                    ingested = await repo.ingest_external_tracks_bulk(
                        "spotify", new_tracks
                    )
                    for track in ingested:
                        if track.id:
                            needs_likes.append(track.id)
                            new_in_batch += 1
                except Exception:
                    logger.exception("Error bulk-ingesting tracks")

            # 5. Build track_id → liked_at mapping from ConnectorTrack metadata
            liked_at_map: dict[int, datetime | None] = {}
            # Existing tracks: reverse-lookup via existing_map
            for (_conn, ct_identifier), track in existing_map.items():
                if track.id and track.id in needs_likes:
                    liked_at_map[track.id] = _parse_liked_at(
                        ct_by_id.get(ct_identifier)
                    )
            # Newly ingested tracks: lookup via connector_track_identifiers
            for ingested_track in ingested:
                if ingested_track.id and ingested_track.id not in liked_at_map:
                    spotify_id = ingested_track.connector_track_identifiers.get(
                        "spotify"
                    )
                    if spotify_id:
                        liked_at_map[ingested_track.id] = _parse_liked_at(
                            ct_by_id.get(spotify_id)
                        )

            # 6. Bulk-save likes for all tracks that need them
            if needs_likes:
                like_repo = uow.get_like_repository()
                like_entries: list[
                    tuple[int, str, bool, datetime | None, datetime | None]
                ] = []
                for track_id in needs_likes:
                    track_liked_at = liked_at_map.get(track_id)
                    like_entries.extend(
                        (track_id, service, True, batch_time, track_liked_at)
                        for service in ("spotify", "narada")
                    )
                try:
                    await like_repo.save_track_likes_batch(like_entries)
                    imported += len(needs_likes)
                except Exception:
                    logger.exception("Error bulk-saving likes")

            batches += 1

            await emitter.emit_progress(
                create_progress_event(
                    operation_id,
                    current=imported + already_synced,
                    total=None,
                    message=f"Processed batch {batches}: {imported} imported, {already_synced} already synced",
                )
            )

            # Early termination if this batch is mostly duplicates
            if (
                new_in_batch == 0
                and batch_already_synced
                > len(tracks) * BusinessLimits.DUPLICATE_RATE_EARLY_STOP
            ):
                logger.info("Reached previously synced tracks, stopping")
                await emitter.emit_progress(
                    create_progress_event(
                        operation_id,
                        current=imported + already_synced,
                        total=None,
                        message="Detected high duplicate rate, finishing...",
                    )
                )
                break

            # Update checkpoint periodically
            if batches % 10 == 0:
                checkpoint = await update_checkpoint(checkpoint, uow, batch_time)

            if not cursor:
                logger.info("Completed import of all Spotify likes")
                break

        # Always stamp the checkpoint on exit — regardless of which exit path was taken.
        checkpoint = await update_checkpoint(checkpoint, uow, datetime.now(UTC))
        logger.info(f"Import complete: {imported} imported, {already_synced} synced")

        await uow.commit()  # commit checkpoint before "complete" SSE fires
        result = OperationResult(operation_name="Spotify Likes Import")
        total = imported + already_synced

        # Add summary metrics with display order
        result.summary_metrics.add(
            "imported", imported, "Likes Imported", significance=1
        )
        result.summary_metrics.add(
            "already_liked", already_synced, "Already Liked ✅", significance=2
        )
        result.summary_metrics.add("candidates", total, "Candidates", significance=3)

        # Calculate and add success rate
        if total > 0:
            success_rate = (imported / total) * 100
            result.summary_metrics.add(
                "success_rate",
                success_rate,
                "Success Rate",
                format="percent",
                significance=4,
            )

        return result


def _parse_liked_at(ct: ConnectorTrack | None) -> datetime | None:
    """Parse liked_at from a ConnectorTrack's raw_metadata.

    Spotify stores the original liked timestamp as an ISO 8601 string
    in raw_metadata["liked_at"].
    """
    if ct is None or not hasattr(ct, "raw_metadata"):
        return None
    liked_at_str = ct.raw_metadata.get("liked_at")
    if liked_at_str and isinstance(liked_at_str, str):
        try:
            return datetime.fromisoformat(liked_at_str)
        except ValueError:
            return None
    return None


@define(slots=True)
class ExportLastFmLikesUseCase:
    """Exports locally liked tracks to Last.fm as "loved" tracks."""

    async def execute(
        self,
        command: ExportLastFmLikesCommand,
        uow: UnitOfWorkProtocol,
        progress_emitter: ProgressEmitter | None = None,
    ) -> OperationResult:
        """Export liked tracks to Last.fm with database transaction management."""
        from src.domain.entities.progress import NullProgressEmitter

        emitter = progress_emitter or NullProgressEmitter()
        async with uow:
            return await self._export(command, uow, emitter)

    async def _export(
        self,
        command: ExportLastFmLikesCommand,
        uow: UnitOfWorkProtocol,
        emitter: ProgressEmitter,
    ) -> OperationResult:
        """Find unsynced likes and export them to Last.fm."""
        from src.domain.entities.progress import tracked_operation

        async with tracked_operation(
            emitter, "Exporting Likes to Last.fm"
        ) as operation_id:
            return await self._export_inner(command, uow, emitter, operation_id)

    async def _export_inner(
        self,
        command: ExportLastFmLikesCommand,
        uow: UnitOfWorkProtocol,
        emitter: ProgressEmitter,
        operation_id: str,
    ) -> OperationResult:
        from src.domain.entities.progress import create_progress_event

        batch_size = command.batch_size or settings.api.lastfm_batch_size
        checkpoint = await get_or_create_checkpoint(
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

        total_narada = await like_repo.count_liked_tracks(
            service="narada", is_liked=True
        )
        already_loved = total_narada - len(unsynced)
        total_to_export = len(unsynced)

        if total_narada > 0:
            logger.info(
                f"Export: {total_narada} total, {already_loved} already loved "
                + f"({already_loved / total_narada * 100:.1f}%), {total_to_export} candidates"
            )
        else:
            logger.info(
                f"Export: no liked tracks in narada, {total_to_export} candidates"
            )

        exported = 0
        filtered = 0
        errors = 0
        lastfm = resolve_love_track_connector(uow)

        for i in range(0, len(unsynced), batch_size):
            if command.max_exports and exported >= command.max_exports:
                logger.info(f"Reached max exports: {command.max_exports}")
                break

            batch = unsynced[i : i + batch_size]
            batch_time = datetime.now(UTC)

            await emitter.emit_progress(
                create_progress_event(
                    operation_id,
                    current=exported,
                    total=total_to_export or None,
                    message=f"Loving track {exported + 1} of {total_to_export}...",
                )
            )

            # Batch-fetch all tracks for this batch (single query instead of N)
            track_repo = uow.get_track_repository()
            batch_track_ids = [like.track_id for like in batch]
            try:
                tracks_dict = await track_repo.find_tracks_by_ids(batch_track_ids)
            except Exception:
                logger.exception("Error batch-loading tracks for export")
                errors += len(batch)
                continue

            # Filter to exportable tracks (must have artists)
            tracks_to_export: list[Track] = []
            for like in batch:
                if command.max_exports and exported >= command.max_exports:
                    break

                if (track := tracks_dict.get(like.track_id)) and track.artists:
                    tracks_to_export.append(track)

            if not tracks_to_export:
                continue

            # Process batch
            results = await self._process_batch(tracks_to_export, lastfm, uow)

            for result in results:
                match result.status:
                    case BatchItemStatus.EXPORTED:
                        exported += 1
                    case BatchItemStatus.SKIPPED:
                        filtered += 1
                    case _:
                        errors += 1

            checkpoint = await update_checkpoint(checkpoint, uow, batch_time)

        logger.info(
            f"Export complete: {exported} exported, {filtered} skipped, {errors} errors"
        )

        await uow.commit()  # commit checkpoint before "complete" SSE fires
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
        result.summary_metrics.add(
            "candidates", total_candidates, "Candidates", significance=5
        )

        # Calculate and add success rate
        if attempted > 0:
            success_rate = (exported / attempted) * 100
            result.summary_metrics.add(
                "success_rate",
                success_rate,
                "Success Rate",
                format="percent",
                significance=6,
            )

        return result

    async def _process_batch(
        self,
        tracks: list[Track],
        connector: LoveTrackConnector,
        uow: UnitOfWorkProtocol,
    ) -> list[BatchItemResult]:
        """Process track batch through Last.fm API."""
        results: list[BatchItemResult] = []
        for track in tracks:
            try:
                result = await self._love_track(track, connector, uow)
                results.append(result)
            except Exception as e:
                logger.exception(f"Error processing track {track.id}")
                results.append(
                    BatchItemResult(
                        status=BatchItemStatus.ERROR,
                        track_id=track.id,
                        error=str(e),
                    )
                )
        return results

    async def _love_track(
        self, track: Track, connector: LoveTrackConnector, uow: UnitOfWorkProtocol
    ) -> BatchItemResult:
        """Love track on Last.fm and record result."""
        if not track.artists:
            return BatchItemResult(
                status=BatchItemStatus.ERROR,
                track_id=track.id,
                error="No artists found",
            )

        try:
            success = await connector.love_track(
                artist=track.artists[0].name, title=track.title
            )

            if success:
                if track.id:
                    await save_likes(track.id, uow, ["lastfm"])
                return BatchItemResult(
                    status=BatchItemStatus.EXPORTED,
                    track_id=track.id,
                )
            else:
                return BatchItemResult(
                    status=BatchItemStatus.SKIPPED,
                    track_id=track.id,
                    metadata={"reason": "API returned False"},
                )
        except Exception as e:
            return BatchItemResult(
                status=BatchItemStatus.ERROR,
                track_id=track.id,
                error=str(e),
            )


@define(slots=True)
class GetSyncCheckpointStatusUseCase:
    """Retrieves sync checkpoint status for UI display."""

    async def execute(
        self, command: GetSyncCheckpointStatusCommand, uow: UnitOfWorkProtocol
    ) -> SyncCheckpointStatus:
        """Get checkpoint status for a service and entity type."""
        async with uow:
            return await self._get_status(command.service, command.entity_type, uow)

    async def execute_all(
        self,
        combinations: tuple[tuple[str, Literal["likes", "plays"]], ...],
        uow: UnitOfWorkProtocol,
    ) -> list[SyncCheckpointStatus]:
        """Get checkpoint statuses for all service/entity combinations in a single session."""
        async with uow:
            return [
                await self._get_status(service, entity_type, uow)
                for service, entity_type in combinations
            ]

    @staticmethod
    async def _get_status(
        service: str,
        entity_type: Literal["likes", "plays"],
        uow: UnitOfWorkProtocol,
    ) -> SyncCheckpointStatus:
        from src.config.constants import BusinessLimits

        repo = uow.get_checkpoint_repository()
        checkpoint = await repo.get_sync_checkpoint(
            user_id=BusinessLimits.DEFAULT_USER_ID,
            service=service,
            entity_type=entity_type,
        )

        return SyncCheckpointStatus(
            service=service,
            entity_type=entity_type,
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
    progress_emitter: ProgressEmitter | None = None,
) -> OperationResult:
    """Import Spotify liked tracks into local database."""
    from src.application.runner import execute_use_case

    command = ImportSpotifyLikesCommand(
        user_id=user_id, limit=limit, max_imports=max_imports
    )
    return await execute_use_case(
        lambda uow: ImportSpotifyLikesUseCase().execute(command, uow, progress_emitter)
    )


async def run_lastfm_likes_export(
    user_id: str,
    batch_size: int | None = None,
    max_exports: int | None = None,
    override_date: datetime | None = None,
    progress_emitter: ProgressEmitter | None = None,
) -> OperationResult:
    """Export locally liked tracks to Last.fm as loved tracks."""
    from src.application.runner import execute_use_case

    command = ExportLastFmLikesCommand(
        user_id=user_id,
        batch_size=batch_size,
        max_exports=max_exports,
        override_date=override_date,
    )
    return await execute_use_case(
        lambda uow: ExportLastFmLikesUseCase().execute(command, uow, progress_emitter)
    )


async def get_sync_checkpoint_status(
    service: str,
    entity_type: Literal["likes", "plays"],
) -> SyncCheckpointStatus:
    """Get sync checkpoint status for UI display."""
    from src.application.runner import execute_use_case

    command = GetSyncCheckpointStatusCommand(service=service, entity_type=entity_type)
    return await execute_use_case(
        lambda uow: GetSyncCheckpointStatusUseCase().execute(command, uow)
    )


async def get_all_checkpoint_statuses() -> list[SyncCheckpointStatus]:
    """Get checkpoint statuses for all known service/entity combinations in a single session."""
    from src.application.runner import execute_use_case

    return await execute_use_case(
        lambda uow: GetSyncCheckpointStatusUseCase().execute_all(
            CHECKPOINT_COMBINATIONS, uow
        )
    )
