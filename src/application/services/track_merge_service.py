"""Track merge service for handling duplicate canonical tracks.

Simple service that moves foreign key references and soft-deletes duplicate tracks
to prevent orphaned records.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from attrs import define
from sqlalchemy import update

from src.config import get_logger
from src.domain.entities import Track

if TYPE_CHECKING:
    from src.domain.repositories.interfaces import UnitOfWorkProtocol

logger = get_logger(__name__)


@define
class TrackMergeService:
    """Merge duplicate canonical tracks by moving all references to winner track."""

    async def merge_tracks(self, winner_id: int, loser_id: int, uow: "UnitOfWorkProtocol") -> Track:
        """Move all foreign key references from loser to winner, then soft-delete loser.
        
        Args:
            winner_id: Track ID that will keep all the references.
            loser_id: Track ID that will be soft-deleted.
            uow: Unit of work for transaction management.
            
        Returns:
            Winner track after merge.
            
        Raises:
            ValueError: If tracks are the same or don't exist.
        """
        if winner_id == loser_id:
            raise ValueError("Cannot merge track with itself")
            
        logger.info(f"Merging tracks: {loser_id} → {winner_id}")
        
        # Validate tracks exist
        track_repo = uow.get_track_repository()
        winner_track = await track_repo.get_by_id(winner_id)
        await track_repo.get_by_id(loser_id)  # Just verify it exists
        
        # Move all foreign key references
        await self._move_all_references(loser_id, winner_id, uow)
        
        # Soft delete the loser track
        await self._soft_delete_track(loser_id, uow)
        
        logger.info(f"Successfully merged tracks: {loser_id} → {winner_id}")
        return winner_track

    async def _move_all_references(self, loser_id: int, winner_id: int, uow: "UnitOfWorkProtocol") -> None:
        """Move all foreign key references from loser to winner track."""
        session = uow.get_session()
        now = datetime.now(UTC)
        
        from src.infrastructure.persistence.database.db_models import (
            DBPlaylistTrack,
            DBTrackMapping,
            DBTrackPlay,
        )
        
        # Update playlist tracks
        await session.execute(
            update(DBPlaylistTrack)
            .where(
                DBPlaylistTrack.track_id == loser_id,
                DBPlaylistTrack.is_deleted == False,  # noqa: E712
            )
            .values(track_id=winner_id, updated_at=now)
        )
        
        # Merge track likes with conflict resolution
        await self._merge_track_likes(loser_id, winner_id, session, now)
        
        # Update track plays
        await session.execute(
            update(DBTrackPlay)
            .where(
                DBTrackPlay.track_id == loser_id,
                DBTrackPlay.is_deleted == False,  # noqa: E712
            )
            .values(track_id=winner_id, updated_at=now)
        )
        
        # Merge track mappings with conflict resolution
        await self._merge_track_mappings(loser_id, winner_id, session, now)
        
        # Merge track metrics with conflict resolution
        await self._merge_track_metrics(loser_id, winner_id, session, now)
        
        logger.debug(f"Moved all references: {loser_id} → {winner_id}")

    async def _merge_track_metrics(self, loser_id: int, winner_id: int, session, now: datetime) -> None:
        """Merge track metrics with conflict resolution for duplicate (connector_name, metric_type)."""
        from sqlalchemy import text, update

        from src.infrastructure.persistence.database.db_models import DBTrackMetric
        
        # Find metrics that would conflict (same connector_name + metric_type for both tracks)
        conflict_query = text("""
            SELECT 
                loser.id as loser_metric_id,
                winner.id as winner_metric_id,
                loser.connector_name,
                loser.metric_type,
                loser.value as loser_value,
                loser.collected_at as loser_collected_at,
                winner.value as winner_value,
                winner.collected_at as winner_collected_at
            FROM track_metrics loser
            JOIN track_metrics winner ON (
                loser.connector_name = winner.connector_name AND
                loser.metric_type = winner.metric_type
            )
            WHERE 
                loser.track_id = :loser_id AND 
                winner.track_id = :winner_id AND
                loser.is_deleted = false AND 
                winner.is_deleted = false
        """)
        
        result = await session.execute(conflict_query, {"loser_id": loser_id, "winner_id": winner_id})
        conflicts = result.fetchall()
        
        # Handle conflicts: keep the most recent metric value
        for conflict in conflicts:
            loser_metric_id = conflict.loser_metric_id
            winner_metric_id = conflict.winner_metric_id
            loser_collected_at = conflict.loser_collected_at
            winner_collected_at = conflict.winner_collected_at
            
            if loser_collected_at > winner_collected_at:
                # Loser has more recent data - update winner with loser's value
                # Convert string datetime to datetime object if needed
                collected_at_value = loser_collected_at
                if isinstance(loser_collected_at, str):
                    from datetime import datetime as dt
                    collected_at_value = dt.fromisoformat(loser_collected_at.replace('Z', '+00:00'))
                
                await session.execute(
                    update(DBTrackMetric)
                    .where(DBTrackMetric.id == winner_metric_id)
                    .values(
                        value=conflict.loser_value,
                        collected_at=collected_at_value,
                        updated_at=now
                    )
                )
                logger.debug(f"Updated winner metric {winner_metric_id} with loser's more recent value")
            
            # Soft delete the loser's conflicting metric (whether we used its value or not)
            await session.execute(
                update(DBTrackMetric)
                .where(DBTrackMetric.id == loser_metric_id)
                .values(is_deleted=True, deleted_at=now, updated_at=now)
            )
            logger.debug(f"Soft deleted conflicting loser metric {loser_metric_id}")
        
        # Move non-conflicting metrics from loser to winner
        if conflicts:
            # Only move metrics that don't conflict
            conflict_metric_ids = [c.loser_metric_id for c in conflicts]
            await session.execute(
                update(DBTrackMetric)
                .where(
                    DBTrackMetric.track_id == loser_id,
                    DBTrackMetric.is_deleted == False,  # noqa: E712
                    ~DBTrackMetric.id.in_(conflict_metric_ids)
                )
                .values(track_id=winner_id, updated_at=now)
            )
        else:
            # No conflicts - move all metrics
            await session.execute(
                update(DBTrackMetric)
                .where(
                    DBTrackMetric.track_id == loser_id,
                    DBTrackMetric.is_deleted == False,  # noqa: E712
                )
                .values(track_id=winner_id, updated_at=now)
            )
        
        logger.debug(f"Merged track metrics: {loser_id} → {winner_id} ({len(conflicts)} conflicts resolved)")

    async def _merge_track_mappings(self, loser_id: int, winner_id: int, session, now: datetime) -> None:
        """Merge track mappings with conflict resolution.
        
        Handles two cases:
        1. Same connector + different external IDs: Keep both, winner's becomes primary
        2. Same connector + same external ID: Keep only the better mapping
        """
        from sqlalchemy import text, update
        
        from src.infrastructure.persistence.database.db_models import DBTrackMapping
        
        # Find mappings that would conflict by connector name
        conflict_query = text("""
            SELECT 
                loser.id as loser_mapping_id,
                winner.id as winner_mapping_id,
                loser.connector_name,
                loser.connector_track_id as loser_connector_track_id,
                winner.connector_track_id as winner_connector_track_id,
                loser.confidence as loser_confidence,
                loser.match_method as loser_match_method,
                loser.created_at as loser_created_at,
                loser.is_primary as loser_is_primary,
                winner.confidence as winner_confidence,
                winner.match_method as winner_match_method,
                winner.created_at as winner_created_at,
                winner.is_primary as winner_is_primary
            FROM track_mappings loser
            JOIN track_mappings winner ON loser.connector_name = winner.connector_name
            WHERE 
                loser.track_id = :loser_id AND 
                winner.track_id = :winner_id AND
                loser.is_deleted = false AND 
                winner.is_deleted = false
        """)
        
        result = await session.execute(conflict_query, {"loser_id": loser_id, "winner_id": winner_id})
        conflicts = result.fetchall()
        
        same_external_id_conflicts = 0
        different_external_id_conflicts = 0
        
        for conflict in conflicts:
            loser_mapping_id = conflict.loser_mapping_id
            winner_mapping_id = conflict.winner_mapping_id
            loser_connector_track_id = conflict.loser_connector_track_id
            winner_connector_track_id = conflict.winner_connector_track_id
            
            if loser_connector_track_id == winner_connector_track_id:
                # Same external ID - keep only the better mapping
                same_external_id_conflicts += 1
                should_update_winner = False
                
                # Use confidence as primary criteria
                if conflict.loser_confidence > conflict.winner_confidence:
                    should_update_winner = True
                elif conflict.loser_confidence == conflict.winner_confidence:
                    # If confidence is equal, prefer the newer mapping
                    if conflict.loser_created_at > conflict.winner_created_at:
                        should_update_winner = True
                
                if should_update_winner:
                    # Update winner with loser's better mapping data
                    await session.execute(
                        update(DBTrackMapping)
                        .where(DBTrackMapping.id == winner_mapping_id)
                        .values(
                            confidence=conflict.loser_confidence,
                            match_method=conflict.loser_match_method,
                            updated_at=now
                        )
                    )
                    logger.debug(f"Updated winner mapping {winner_mapping_id} with loser's better data")
                
                # Always delete the loser's mapping (same external ID = true duplicate)
                await session.execute(
                    update(DBTrackMapping)
                    .where(DBTrackMapping.id == loser_mapping_id)
                    .values(is_deleted=True, deleted_at=now, updated_at=now)
                )
                logger.debug(f"Deleted duplicate loser mapping {loser_mapping_id} (same external ID)")
                
            else:
                # Different external IDs - keep both, ensure winner's is primary
                different_external_id_conflicts += 1
                
                # Ensure winner's mapping is primary for this connector
                await session.execute(
                    update(DBTrackMapping)
                    .where(DBTrackMapping.id == winner_mapping_id)
                    .values(is_primary=True, updated_at=now)
                )
                
                # Move loser's mapping to winner track but make it non-primary
                await session.execute(
                    update(DBTrackMapping)
                    .where(DBTrackMapping.id == loser_mapping_id)
                    .values(track_id=winner_id, is_primary=False, updated_at=now)
                )
                logger.debug(f"Moved loser mapping {loser_mapping_id} to winner as secondary (different external ID)")
        
        # Move non-conflicting mappings from loser to winner
        if conflicts:
            conflict_mapping_ids = [c.loser_mapping_id for c in conflicts]
            await session.execute(
                update(DBTrackMapping)
                .where(
                    DBTrackMapping.track_id == loser_id,
                    DBTrackMapping.is_deleted == False,  # noqa: E712
                    ~DBTrackMapping.id.in_(conflict_mapping_ids)
                )
                .values(track_id=winner_id, updated_at=now)
            )
        else:
            # No conflicts - move all mappings
            await session.execute(
                update(DBTrackMapping)
                .where(
                    DBTrackMapping.track_id == loser_id,
                    DBTrackMapping.is_deleted == False,  # noqa: E712
                )
                .values(track_id=winner_id, updated_at=now)
            )
        
        logger.debug(
            f"Merged track mappings: {loser_id} → {winner_id} "
            f"({same_external_id_conflicts} same external ID conflicts, "
            f"{different_external_id_conflicts} different external ID conflicts resolved)"
        )

    async def _merge_track_likes(self, loser_id: int, winner_id: int, session, now: datetime) -> None:
        """Merge track likes with conflict resolution for duplicate (track_id, service)."""
        from sqlalchemy import text, update

        from src.infrastructure.persistence.database.db_models import DBTrackLike
        
        # Find likes that would conflict (same service for both tracks)
        conflict_query = text("""
            SELECT 
                loser.id as loser_like_id,
                winner.id as winner_like_id,
                loser.service,
                loser.is_liked as loser_is_liked,
                loser.liked_at as loser_liked_at,
                winner.is_liked as winner_is_liked,
                winner.liked_at as winner_liked_at,
                loser.last_synced as loser_last_synced,
                winner.last_synced as winner_last_synced
            FROM track_likes loser
            JOIN track_likes winner ON loser.service = winner.service
            WHERE 
                loser.track_id = :loser_id AND 
                winner.track_id = :winner_id AND
                loser.is_deleted = false AND 
                winner.is_deleted = false
        """)
        
        result = await session.execute(conflict_query, {"loser_id": loser_id, "winner_id": winner_id})
        conflicts = result.fetchall()
        
        # Handle conflicts: keep the most recent like state
        for conflict in conflicts:
            loser_like_id = conflict.loser_like_id
            winner_like_id = conflict.winner_like_id
            loser_last_synced = conflict.loser_last_synced
            winner_last_synced = conflict.winner_last_synced
            
            # Use the most recently synced like state
            should_update_winner = False
            
            if loser_last_synced and winner_last_synced:
                if loser_last_synced > winner_last_synced:
                    should_update_winner = True
            elif loser_last_synced and not winner_last_synced:
                should_update_winner = True
            
            if should_update_winner:
                # Update winner with loser's state (handle None values properly)
                update_values = {
                    "is_liked": conflict.loser_is_liked,
                    "updated_at": now
                }
                
                # Only set non-None datetime values, convert strings to datetime objects
                if conflict.loser_liked_at is not None:
                    # Convert string datetime to datetime object if needed
                    if isinstance(conflict.loser_liked_at, str):
                        from datetime import datetime as dt
                        update_values["liked_at"] = dt.fromisoformat(conflict.loser_liked_at.replace('Z', '+00:00'))
                    else:
                        update_values["liked_at"] = conflict.loser_liked_at
                        
                if loser_last_synced is not None:
                    # Convert string datetime to datetime object if needed
                    if isinstance(loser_last_synced, str):
                        from datetime import datetime as dt
                        update_values["last_synced"] = dt.fromisoformat(loser_last_synced.replace('Z', '+00:00'))
                    else:
                        update_values["last_synced"] = loser_last_synced
                
                await session.execute(
                    update(DBTrackLike)
                    .where(DBTrackLike.id == winner_like_id)
                    .values(**update_values)
                )
                logger.debug(f"Updated winner like {winner_like_id} with loser's more recent state")
            
            # Soft delete the loser's conflicting like (whether we used its state or not)
            await session.execute(
                update(DBTrackLike)
                .where(DBTrackLike.id == loser_like_id)
                .values(is_deleted=True, deleted_at=now, updated_at=now)
            )
            logger.debug(f"Soft deleted conflicting loser like {loser_like_id}")
        
        # Move non-conflicting likes from loser to winner
        if conflicts:
            # Only move likes that don't conflict
            conflict_like_ids = [c.loser_like_id for c in conflicts]
            await session.execute(
                update(DBTrackLike)
                .where(
                    DBTrackLike.track_id == loser_id,
                    DBTrackLike.is_deleted == False,  # noqa: E712
                    ~DBTrackLike.id.in_(conflict_like_ids)
                )
                .values(track_id=winner_id, updated_at=now)
            )
        else:
            # No conflicts - move all likes
            await session.execute(
                update(DBTrackLike)
                .where(
                    DBTrackLike.track_id == loser_id,
                    DBTrackLike.is_deleted == False,  # noqa: E712
                )
                .values(track_id=winner_id, updated_at=now)
            )
        
        logger.debug(f"Merged track likes: {loser_id} → {winner_id} ({len(conflicts)} conflicts resolved)")

    async def _soft_delete_track(self, track_id: int, uow: "UnitOfWorkProtocol") -> None:
        """Soft delete the loser track."""
        session = uow.get_session()
        now = datetime.now(UTC)
        
        from src.infrastructure.persistence.database.db_models import DBTrack
        
        await session.execute(
            update(DBTrack)
            .where(DBTrack.id == track_id)
            .values(is_deleted=True, deleted_at=now, updated_at=now)
        )
        
        logger.debug(f"Soft deleted track: {track_id}")