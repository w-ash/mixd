"""Core track repository implementation for basic track operations."""

# pyright: reportAny=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
# Legitimate Unknown: SQLAlchemy ColumnElement types from ilike/in_/cast expressions

from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import String, cast, delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities import Track
from src.infrastructure.persistence.database.db_models import (
    DBPlaylistTrack,
    DBTrack,
    DBTrackLike,
    DBTrackMapping,
    DBTrackMetric,
    DBTrackPlay,
)
from src.infrastructure.persistence.repositories.base_repo import BaseRepository
from src.infrastructure.persistence.repositories.repo_decorator import db_operation
from src.infrastructure.persistence.repositories.track.mapper import TrackMapper

logger = get_logger(__name__)


class TrackRepository(BaseRepository[DBTrack, Track]):
    """Repository for core track operations."""

    # ID type lookup definitions
    _TRACK_ID_TYPES: ClassVar[dict[str, str]] = {
        "internal": "id",
        "spotify": "spotify_id",
        "isrc": "isrc",
        "musicbrainz": "mbid",
    }

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with session and mapper."""
        super().__init__(
            session=session,
            model_class=DBTrack,
            mapper=TrackMapper(),
        )

    # -------------------------------------------------------------------------
    # PUBLIC API METHODS
    # -------------------------------------------------------------------------

    @db_operation("count_all_tracks")
    async def count_all_tracks(self) -> int:
        """Count all tracks in the database."""
        stmt = self.count()
        result = await self.session.execute(stmt)
        return result.scalar_one()

    @db_operation("find_tracks_by_ids")
    async def find_tracks_by_ids(self, track_ids: list[int]) -> dict[int, Track]:
        """Find multiple tracks by their internal IDs in a single batch operation.

        Args:
            track_ids: List of internal track IDs to retrieve

        Returns:
            Dictionary mapping track IDs to Track objects
        """
        if not track_ids:
            return {}

        # Leverage the base repository's get_by_ids method
        tracks = await self.get_by_ids(track_ids)

        # Map results by ID for easier lookup
        return {track.id: track for track in tracks if track.id is not None}

    @db_operation("save_track")
    async def save_track(self, track: Track) -> Track:
        """Save track without connector mappings using native SQLAlchemy 2.0 features.

        This method follows SQLAlchemy 2.0 async best practices:
        1. Uses direct value mappings instead of complex object hierarchies
        2. Uses explicit eager loading to avoid lazy loading issues
        3. Leverages upsert's two-phase approach for safe async operations
        4. Avoids implicit IO in relationship traversal
        """
        if not track.title or not track.artists:
            raise ValueError("Track must have title and artists")

        # Handle update case with explicit eager loading
        if track.id:
            return await self.update(track.id, track)

        # Create direct column-to-value mappings for insert/update
        # This avoids the need to convert the entire Track object to a dict
        values = {
            "title": track.title,
            "artists": {"names": [artist.name for artist in track.artists]},
            "album": track.album,
            "duration_ms": track.duration_ms,
            "release_date": track.release_date,
            "isrc": track.isrc,
        }

        # Add connector IDs if available
        if "spotify" in track.connector_track_identifiers:
            values["spotify_id"] = track.connector_track_identifiers["spotify"]
        if "musicbrainz" in track.connector_track_identifiers:
            values["mbid"] = track.connector_track_identifiers["musicbrainz"]

        # Handle lookups by ISRC or Spotify ID - leverage the improved upsert with direct values
        # The upsert method has been updated to use a two-phase approach that avoids greenlet issues
        if track.isrc:
            return await self.upsert({"isrc": track.isrc}, values)
        elif "spotify" in track.connector_track_identifiers:
            return await self.upsert(
                {"spotify_id": track.connector_track_identifiers["spotify"]}, values
            )

        # Create new track with explicit eager loading for relationships
        db_track = DBTrack(**values)
        self.session.add(db_track)
        await self.session.flush()

        # Refresh with explicit eager loading of relationships to avoid lazy loading
        default_rels = self.mapper.get_default_relationships()
        if default_rels:
            # Extract string names from relationships using utility method
            rel_names = self._extract_relationship_names(default_rels)
            if rel_names:
                await self.session.refresh(db_track, attribute_names=rel_names)
            else:
                await self.session.refresh(db_track)
        else:
            await self.session.refresh(db_track)

        # Map back to domain model - the to_domain method has been updated to use AsyncAttrs safely
        result = await TrackMapper.to_domain_with_session(db_track, self.session)
        if result is None:
            raise ValueError(f"Failed to map track from database (id={db_track.id})")
        return result

    # -------------------------------------------------------------------------
    # LIBRARY LISTING
    # -------------------------------------------------------------------------

    # Sort field → SQLAlchemy column expression mapping
    _SORT_MAP: ClassVar[dict[str, tuple[str, str]]] = {
        "title_asc": ("title", "asc"),
        "title_desc": ("title", "desc"),
        "artist_asc": ("artists_text", "asc"),
        "artist_desc": ("artists_text", "desc"),
        "added_desc": ("created_at", "desc"),
        "added_asc": ("created_at", "asc"),
        "duration_asc": ("duration_ms", "asc"),
        "duration_desc": ("duration_ms", "desc"),
    }

    @db_operation("list_tracks")
    async def list_tracks(
        self,
        *,
        query: str | None = None,
        liked: bool | None = None,
        connector: str | None = None,
        sort_by: str = "title_asc",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Track], int, set[int]]:
        """List tracks with search, filters, sorting, and pagination.

        Returns (tracks, total_count, liked_track_ids) where total_count reflects
        the filtered result set before LIMIT/OFFSET are applied, and liked_track_ids
        contains IDs of tracks liked on any service (authoritative from track_likes table).
        """
        # Build base filter conditions
        conditions = []

        if query:
            pattern = f"%{query}%"
            # TODO: search against denormalized artists_text column for index-friendly search
            conditions.append(
                or_(
                    DBTrack.title.ilike(pattern),
                    DBTrack.album.ilike(pattern),
                    cast(DBTrack.artists, String).ilike(pattern),
                )
            )

        if liked is not None:
            liked_subq = (
                select(DBTrackLike.track_id)
                .where(DBTrackLike.is_liked == True)  # noqa: E712
                .distinct()
            )
            if liked:
                conditions.append(DBTrack.id.in_(liked_subq))
            else:
                conditions.append(~DBTrack.id.in_(liked_subq))

        if connector:
            connector_subq = (
                select(DBTrackMapping.track_id)
                .where(DBTrackMapping.connector_name == connector)
                .distinct()
            )
            conditions.append(DBTrack.id.in_(connector_subq))

        # Count total matching tracks (before pagination)
        count_stmt = select(func.count()).select_from(DBTrack)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar_one()

        if total == 0:
            return [], 0, set()

        # Build data query with sorting, pagination, and relationship loading
        data_stmt = self.select()
        if conditions:
            data_stmt = data_stmt.where(*conditions)

        # Apply sort
        sort_field, sort_dir = self._SORT_MAP.get(sort_by, ("title", "asc"))
        if sort_field == "artists_text":
            col = cast(DBTrack.artists, String)
        else:
            col = getattr(DBTrack, sort_field)
        data_stmt = data_stmt.order_by(col.desc() if sort_dir == "desc" else col.asc())

        data_stmt = data_stmt.offset(offset).limit(limit)

        # Eager-load relationships for mapper
        data_stmt = self.with_default_relationships(data_stmt)

        result = await self.session.execute(data_stmt)
        db_tracks = result.scalars().all()

        tracks = [await self.mapper.to_domain(db_track) for db_track in db_tracks]

        # Get authoritative liked status from track_likes table for returned tracks
        track_ids = [t.id for t in tracks if t.id is not None]
        liked_ids: set[int] = set()
        if track_ids:
            liked_stmt = (
                select(DBTrackLike.track_id)
                .where(
                    DBTrackLike.track_id.in_(track_ids), DBTrackLike.is_liked == True
                )  # noqa: E712
                .distinct()
            )
            liked_result = await self.session.execute(liked_stmt)
            liked_ids = set(liked_result.scalars().all())

        return tracks, total, liked_ids

    # -------------------------------------------------------------------------
    # TRACK MERGE OPERATIONS
    # -------------------------------------------------------------------------

    @db_operation("move_references_to_track")
    async def move_references_to_track(self, from_id: int, to_id: int) -> None:
        """Move all foreign key references from one track to another.

        Moves playlist tracks, plays, and likes (with conflict resolution for likes).
        """
        now = datetime.now(UTC)

        # Update playlist tracks
        await self.session.execute(
            update(DBPlaylistTrack)
            .where(DBPlaylistTrack.track_id == from_id)
            .values(track_id=to_id, updated_at=now)
        )

        # Update track plays
        await self.session.execute(
            update(DBTrackPlay)
            .where(DBTrackPlay.track_id == from_id)
            .values(track_id=to_id, updated_at=now)
        )

        # Merge track likes with conflict resolution
        await self._merge_track_likes(from_id, to_id, now)

        logger.debug(f"Moved all references: {from_id} → {to_id}")

    @db_operation("merge_mappings_to_track")
    async def merge_mappings_to_track(self, from_id: int, to_id: int) -> None:
        """Merge connector mappings from one track to another with conflict resolution."""
        now = datetime.now(UTC)

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
                loser.track_id = :from_id AND
                winner.track_id = :to_id
        """)

        result = await self.session.execute(
            conflict_query, {"from_id": from_id, "to_id": to_id}
        )
        conflicts = result.fetchall()

        same_external_id_conflicts = 0
        different_external_id_conflicts = 0

        for conflict in conflicts:
            loser_mapping_id = conflict.loser_mapping_id
            winner_mapping_id = conflict.winner_mapping_id
            loser_connector_track_id = conflict.loser_connector_track_id
            winner_connector_track_id = conflict.winner_connector_track_id

            if loser_connector_track_id == winner_connector_track_id:
                # Same external ID — keep only the better mapping
                same_external_id_conflicts += 1
                should_update_winner = False

                if conflict.loser_confidence > conflict.winner_confidence or (
                    conflict.loser_confidence == conflict.winner_confidence
                    and conflict.loser_created_at > conflict.winner_created_at
                ):
                    should_update_winner = True

                if should_update_winner:
                    await self.session.execute(
                        update(DBTrackMapping)
                        .where(DBTrackMapping.id == winner_mapping_id)
                        .values(
                            confidence=conflict.loser_confidence,
                            match_method=conflict.loser_match_method,
                            updated_at=now,
                        )
                    )

                # Always delete the loser's mapping (same external ID = true duplicate)
                await self.session.execute(
                    delete(DBTrackMapping).where(DBTrackMapping.id == loser_mapping_id)
                )
            else:
                # Different external IDs — keep both, ensure winner's is primary
                different_external_id_conflicts += 1

                await self.session.execute(
                    update(DBTrackMapping)
                    .where(DBTrackMapping.id == winner_mapping_id)
                    .values(is_primary=True, updated_at=now)
                )
                await self.session.execute(
                    update(DBTrackMapping)
                    .where(DBTrackMapping.id == loser_mapping_id)
                    .values(track_id=to_id, is_primary=False, updated_at=now)
                )

        # Move non-conflicting mappings from loser to winner
        if conflicts:
            conflict_mapping_ids = [c.loser_mapping_id for c in conflicts]
            await self.session.execute(
                update(DBTrackMapping)
                .where(
                    DBTrackMapping.track_id == from_id,
                    ~DBTrackMapping.id.in_(conflict_mapping_ids),
                )
                .values(track_id=to_id, updated_at=now)
            )
        else:
            await self.session.execute(
                update(DBTrackMapping)
                .where(DBTrackMapping.track_id == from_id)
                .values(track_id=to_id, updated_at=now)
            )

        logger.debug(
            f"Merged track mappings: {from_id} → {to_id} "
            + f"({same_external_id_conflicts} same external ID, "
            + f"{different_external_id_conflicts} different external ID conflicts)"
        )

    @db_operation("merge_metrics_to_track")
    async def merge_metrics_to_track(self, from_id: int, to_id: int) -> None:
        """Merge track metrics from one track to another with conflict resolution."""
        now = datetime.now(UTC)

        # Find metrics that would conflict (same connector_name + metric_type)
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
                loser.track_id = :from_id AND
                winner.track_id = :to_id
        """)

        result = await self.session.execute(
            conflict_query, {"from_id": from_id, "to_id": to_id}
        )
        conflicts = result.fetchall()

        # Handle conflicts: keep the most recent metric value
        for conflict in conflicts:
            loser_metric_id = conflict.loser_metric_id
            winner_metric_id = conflict.winner_metric_id
            loser_collected_at = conflict.loser_collected_at
            winner_collected_at = conflict.winner_collected_at

            if loser_collected_at > winner_collected_at:
                collected_at_value = loser_collected_at
                if isinstance(loser_collected_at, str):
                    collected_at_value = datetime.fromisoformat(loser_collected_at)

                await self.session.execute(
                    update(DBTrackMetric)
                    .where(DBTrackMetric.id == winner_metric_id)
                    .values(
                        value=conflict.loser_value,
                        collected_at=collected_at_value,
                        updated_at=now,
                    )
                )

            # Hard delete the loser's conflicting metric
            await self.session.execute(
                delete(DBTrackMetric).where(DBTrackMetric.id == loser_metric_id)
            )

        # Move non-conflicting metrics from loser to winner
        if conflicts:
            conflict_metric_ids = [c.loser_metric_id for c in conflicts]
            await self.session.execute(
                update(DBTrackMetric)
                .where(
                    DBTrackMetric.track_id == from_id,
                    ~DBTrackMetric.id.in_(conflict_metric_ids),
                )
                .values(track_id=to_id, updated_at=now)
            )
        else:
            await self.session.execute(
                update(DBTrackMetric)
                .where(DBTrackMetric.track_id == from_id)
                .values(track_id=to_id, updated_at=now)
            )

        logger.debug(
            f"Merged track metrics: {from_id} → {to_id} ({len(conflicts)} conflicts resolved)"
        )

    @db_operation("hard_delete_track")
    async def hard_delete_track(self, track_id: int) -> None:
        """Permanently delete a track record from the database."""
        await self.session.execute(delete(DBTrack).where(DBTrack.id == track_id))
        logger.debug(f"Hard deleted track: {track_id}")

    # -------------------------------------------------------------------------
    # PRIVATE HELPERS
    # -------------------------------------------------------------------------

    async def _merge_track_likes(self, from_id: int, to_id: int, now: datetime) -> None:
        """Merge track likes with conflict resolution for duplicate (track_id, service)."""
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
                loser.track_id = :from_id AND
                winner.track_id = :to_id
        """)

        result = await self.session.execute(
            conflict_query, {"from_id": from_id, "to_id": to_id}
        )
        conflicts = result.fetchall()

        for conflict in conflicts:
            loser_like_id = conflict.loser_like_id
            winner_like_id = conflict.winner_like_id
            loser_last_synced = conflict.loser_last_synced
            winner_last_synced = conflict.winner_last_synced

            should_update_winner = False
            if loser_last_synced and winner_last_synced:
                if loser_last_synced > winner_last_synced:
                    should_update_winner = True
            elif loser_last_synced and not winner_last_synced:
                should_update_winner = True

            if should_update_winner:
                update_values: dict[str, object] = {
                    "is_liked": conflict.loser_is_liked,
                    "updated_at": now,
                }

                if conflict.loser_liked_at is not None:
                    if isinstance(conflict.loser_liked_at, str):
                        update_values["liked_at"] = datetime.fromisoformat(
                            conflict.loser_liked_at
                        )
                    else:
                        update_values["liked_at"] = conflict.loser_liked_at

                if loser_last_synced is not None:
                    if isinstance(loser_last_synced, str):
                        update_values["last_synced"] = datetime.fromisoformat(
                            loser_last_synced
                        )
                    else:
                        update_values["last_synced"] = loser_last_synced

                await self.session.execute(
                    update(DBTrackLike)
                    .where(DBTrackLike.id == winner_like_id)
                    .values(**update_values)
                )

            # Hard delete the loser's conflicting like
            await self.session.execute(
                delete(DBTrackLike).where(DBTrackLike.id == loser_like_id)
            )

        # Move non-conflicting likes from loser to winner
        if conflicts:
            conflict_like_ids = [c.loser_like_id for c in conflicts]
            await self.session.execute(
                update(DBTrackLike)
                .where(
                    DBTrackLike.track_id == from_id,
                    ~DBTrackLike.id.in_(conflict_like_ids),
                )
                .values(track_id=to_id, updated_at=now)
            )
        else:
            await self.session.execute(
                update(DBTrackLike)
                .where(DBTrackLike.track_id == from_id)
                .values(track_id=to_id, updated_at=now)
            )

        logger.debug(
            f"Merged track likes: {from_id} → {to_id} ({len(conflicts)} conflicts resolved)"
        )
