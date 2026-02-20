"""Track mappers for converting between domain and database models."""

from collections.abc import Awaitable, Callable
from typing import Any, override

from attrs import define
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities import Artist, Track, ensure_utc
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBTrack,
    DBTrackMapping,
)
from src.infrastructure.persistence.repositories.base_repo import BaseModelMapper

logger = get_logger(__name__)

# Callback type: promotes a connector mapping to primary status
# Args: (track_id, connector_name, connector_track_db_id) -> success
PromotePrimaryMappingFn = Callable[[int, str, int], Awaitable[bool]]


def _make_promote_primary_fn(session: AsyncSession) -> PromotePrimaryMappingFn:
    """Create a callback that promotes a connector mapping to primary via SQL.

    This factory keeps write logic out of TrackMapper's transformation methods.
    The returned closure executes two UPDATE statements (demote all → promote one),
    matching TrackConnectorRepository.set_primary_mapping.

    Args:
        session: Active async session for executing UPDATEs.

    Returns:
        Async callback: (track_id, connector_name, connector_track_db_id) -> success.
    """

    async def _promote(
        track_id: int, connector_name: str, connector_track_db_id: int
    ) -> bool:
        from sqlalchemy import update

        try:
            # Step 1: Demote all mappings for this track+connector to non-primary
            await session.execute(
                update(DBTrackMapping)
                .where(
                    DBTrackMapping.track_id == track_id,
                    DBTrackMapping.connector_name == connector_name,
                )
                .values(is_primary=False)
            )

            # Step 2: Promote the specified mapping to primary
            result = await session.execute(
                update(DBTrackMapping)
                .where(
                    DBTrackMapping.track_id == track_id,
                    DBTrackMapping.connector_track_id == connector_track_db_id,
                )
                .values(is_primary=True)
            )
        except Exception as e:
            logger.error(
                f"Promote primary mapping failed: track_id={track_id}, "
                f"connector={connector_name}, connector_track_db_id={connector_track_db_id}: {e}"
            )
            return False
        else:
            return result.rowcount > 0  # type: ignore[union-attr]

    return _promote


@define(frozen=True, slots=True)
class TrackMapper(BaseModelMapper[DBTrack, Track]):
    """Bidirectional mapper between DB and domain models for Track."""

    @override
    @staticmethod
    async def to_domain(db_model: DBTrack) -> Track:
        """Convert database track to domain model."""
        return await TrackMapper._to_domain_with_session(db_model)

    @staticmethod
    async def to_domain_with_session(
        db_model: DBTrack, session: AsyncSession | None = None
    ) -> Track:
        """Convert database track to domain model with session for auto-healing."""
        promote_primary_fn = _make_promote_primary_fn(session) if session is not None else None
        return await TrackMapper._to_domain_with_session(
            db_model, promote_primary_fn=promote_primary_fn
        )

    @staticmethod
    async def _to_domain_with_session(
        db_model: DBTrack,
        connector_filter: set[str] | None = None,
        promote_primary_fn: PromotePrimaryMappingFn | None = None,
    ) -> Track:
        """Convert database track to domain model.

        Args:
            db_model: Database track entity to convert.
            connector_filter: Optional set of connector names to include.
            promote_primary_fn: Optional callback to promote a fallback connector
                mapping to primary status. Signature:
                (track_id, connector_name, connector_track_db_id) -> success.
                When provided and a connector has no primary mapping, the mapper
                delegates the write to the caller via this callback.
        """
        if not db_model:
            return None

        # Use only eager-loaded relationships to avoid greenlet issues
        # Check if relationships are loaded to prevent lazy loading that causes MissingGreenlet
        from sqlalchemy import inspect
        from sqlalchemy.orm.base import NEVER_SET

        state = inspect(db_model)

        # Safely access mappings - only if already loaded
        active_mappings = []
        mappings_attr = state.attrs.get("mappings")
        if mappings_attr and mappings_attr.loaded_value is not NEVER_SET:
            mappings = mappings_attr.loaded_value or []
            active_mappings = mappings

        # Safely access likes - only if already loaded
        active_likes = []
        likes_attr = state.attrs.get("likes")
        if likes_attr and likes_attr.loaded_value is not NEVER_SET:
            likes = likes_attr.loaded_value or []
            active_likes = likes

        # Build connector IDs and metadata
        connector_track_identifiers = {}
        connector_metadata = {}

        # Add internal ID first
        if db_model.id:
            connector_track_identifiers["db"] = str(db_model.id)

        # Add direct IDs from the track model
        if db_model.spotify_id:
            connector_track_identifiers["spotify"] = db_model.spotify_id
        if db_model.mbid:
            connector_track_identifiers["musicbrainz"] = db_model.mbid

        # Process connector track mappings with primary awareness
        # First pass: collect all primary mappings
        for mapping in active_mappings:
            if mapping.is_primary:
                conn_track = await TrackMapper._get_connector_track(mapping)
                if conn_track:
                    connector_name = conn_track.connector_name
                    # Skip connectors not in filter (if filter is specified)
                    if connector_filter and connector_name not in connector_filter:
                        continue
                    connector_track_identifiers[connector_name] = (
                        conn_track.connector_track_identifier
                    )
                    connector_metadata[connector_name] = conn_track.raw_metadata or {}

        # Second pass: fill in any missing connectors with non-primary mappings (fallback)
        # Also auto-heal by promoting the best non-primary to primary
        connectors_needing_primary = set()
        fallback_mappings: dict[str, DBTrackMapping] = {}

        for mapping in active_mappings:
            if not mapping.is_primary:
                conn_track = await TrackMapper._get_connector_track(mapping)
                if conn_track:
                    connector_name = conn_track.connector_name
                    # Skip connectors not in filter (if filter is specified)
                    if connector_filter and connector_name not in connector_filter:
                        continue
                    # Only use non-primary if no primary exists for this connector
                    if connector_name not in connector_track_identifiers:
                        connector_track_identifiers[connector_name] = (
                            conn_track.connector_track_identifier
                        )
                        connector_metadata[connector_name] = (
                            conn_track.raw_metadata or {}
                        )

                        # Track for primary promotion
                        connectors_needing_primary.add(connector_name)
                        fallback_mappings[connector_name] = mapping

        # Promote fallback mappings to primary via caller-provided callback
        if connectors_needing_primary and hasattr(db_model, "id") and promote_primary_fn:
            await TrackMapper._promote_fallback_to_primary(
                db_model.id, fallback_mappings, promote_primary_fn
            )
        elif connectors_needing_primary:
            logger.warning(
                f"Track {db_model.id} has no primary connector mapping(s): "
                f"connectors={list(connectors_needing_primary)} — "
                f"pass a session to to_domain_with_session() to enable auto-promotion"
            )

        # Process likes into connector metadata
        for like in active_likes:
            service = like.service
            # Skip services not in filter (if filter is specified)
            if connector_filter and service not in connector_filter:
                continue
            if service not in connector_metadata:
                connector_metadata[service] = {}

            connector_metadata[service]["is_liked"] = like.is_liked
            if like.liked_at:
                connector_metadata[service]["liked_at"] = like.liked_at.isoformat()

        return Track(
            id=db_model.id,
            title=db_model.title,
            artists=[Artist(name=name) for name in db_model.artists["names"]],
            album=db_model.album,
            duration_ms=db_model.duration_ms,
            release_date=ensure_utc(db_model.release_date),
            isrc=db_model.isrc,
            connector_track_identifiers=connector_track_identifiers,
            connector_metadata=connector_metadata,
        )

    @staticmethod
    async def _promote_fallback_to_primary(
        track_id: int,
        fallback_mappings: dict[str, DBTrackMapping],
        promote_primary_fn: PromotePrimaryMappingFn,
    ) -> None:
        """Promote fallback connector mappings to primary status.

        For each connector that had no primary mapping, delegates the actual
        DB write to the caller-provided callback. This keeps the mapper
        read-only while enabling opportunistic self-correction.

        Args:
            track_id: The track whose mappings need promotion.
            fallback_mappings: Connector name → the fallback mapping currently in use.
            promote_primary_fn: Callback that performs the DB write:
                (track_id, connector_name, connector_track_db_id) -> success.
        """
        try:
            promoted_count = 0

            for connector_name, mapping in fallback_mappings.items():
                conn_track = await TrackMapper._get_connector_track(mapping)
                if conn_track and conn_track.id:
                    success = await promote_primary_fn(track_id, connector_name, conn_track.id)
                    if success:
                        promoted_count += 1
                        logger.info(
                            f"Promoted connector mapping to primary: track_id={track_id}, "
                            f"connector={connector_name}, "
                            f"connector_track_db_id={conn_track.id}, "
                            f"external_id={conn_track.connector_track_identifier}"
                        )
                    else:
                        logger.warning(
                            f"Failed to promote connector mapping to primary: track_id={track_id}, "
                            f"connector={connector_name}, "
                            f"connector_track_db_id={conn_track.id}"
                        )
                else:
                    logger.warning(
                        f"Cannot promote — connector track unavailable: track_id={track_id}, "
                        f"connector={connector_name}"
                    )

            if promoted_count > 0:
                logger.info(
                    f"Promoted {promoted_count} connector mapping(s) to primary for track {track_id}"
                )

        except Exception as e:
            logger.error(f"Connector mapping promotion failed for track {track_id}: {e}")
            # Don't re-raise — promotion is best-effort and shouldn't interrupt the read path

    @staticmethod
    async def _get_connector_track(mapping: DBTrackMapping) -> DBConnectorTrack | None:
        """Safely get connector track using AsyncAttrs.awaitable_attrs pattern.

        Uses a single, consistent approach with SQLAlchemy 2.0 awaitable_attrs.
        """
        try:
            # Standard SQLAlchemy 2.0 pattern: use awaitable_attrs consistently
            if hasattr(mapping, "awaitable_attrs"):
                return await mapping.awaitable_attrs.connector_track
            # Simple fallback for non-AsyncAttrs models
            elif hasattr(mapping, "connector_track"):
                return mapping.connector_track
        except Exception as e:
            logger.debug(f"Error getting connector track: {e}")
            return None
        else:
            return None

    @override
    @staticmethod
    def to_db(domain_model: Track) -> DBTrack:
        """Convert domain track to database model."""
        # Create the main track entity
        return DBTrack(
            title=domain_model.title,
            artists={"names": [a.name for a in domain_model.artists]},
            album=domain_model.album,
            duration_ms=domain_model.duration_ms,
            release_date=domain_model.release_date,
            isrc=domain_model.isrc,
            spotify_id=domain_model.connector_track_identifiers.get("spotify"),
            mbid=domain_model.connector_track_identifiers.get("musicbrainz"),
        )

    @staticmethod
    def extract_artist_names(artists_data: list) -> list[str]:
        """Extract artist names from mixed format artist data."""
        if not artists_data or not isinstance(artists_data, list):
            return []

        if all(isinstance(a, str) for a in artists_data):
            return artists_data
        elif all(isinstance(a, dict) for a in artists_data):
            return [a.get("name", "") for a in artists_data if a.get("name")]

        # Mixed format - extract what we can
        names = []
        for artist in artists_data:
            match artist:
                case str() as name if name:
                    names.append(name)
                case dict() as d if d.get("name"):
                    names.append(d.get("name"))

        return names

    @override
    @staticmethod
    def get_default_relationships() -> list[Any]:
        """Get default relationships using SQLAlchemy 2.1 best practices."""
        from sqlalchemy.orm import selectinload

        from src.infrastructure.persistence.database.db_models import (
            DBTrack,
            DBTrackMapping,
        )

        return [
            selectinload(DBTrack.mappings),  # Simple relationship
            selectinload(DBTrack.mappings).selectinload(
                DBTrackMapping.connector_track
            ),  # Nested chaining
            selectinload(DBTrack.likes),  # Simple relationship
        ]
