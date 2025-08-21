"""Track mappers for converting between domain and database models."""

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


@define(frozen=True, slots=True)
class TrackMapper(BaseModelMapper[DBTrack, Track]):
    """Bidirectional mapper between DB and domain models for Track."""

    @staticmethod
    @override
    async def to_domain(db_model: DBTrack) -> Track:
        """Convert database track to domain model."""
        return await TrackMapper._to_domain_with_session(db_model, None, None)

    @staticmethod
    async def to_domain_with_session(
        db_model: DBTrack, session: AsyncSession | None = None
    ) -> Track:
        """Convert database track to domain model with session for auto-healing."""
        return await TrackMapper._to_domain_with_session(db_model, session, None)

    @staticmethod
    async def _to_domain_with_session(
        db_model: DBTrack,
        session: AsyncSession | None = None,
        connector_filter: set[str] | None = None,
    ) -> Track:
        """Convert database track to domain model."""
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
        fallback_mappings = {}

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

                        # Track this for auto-healing
                        connectors_needing_primary.add(connector_name)
                        fallback_mappings[connector_name] = mapping

        # Auto-heal: promote fallback mappings to primary
        if (
            connectors_needing_primary
            and hasattr(db_model, "id")
            and session is not None
        ):
            await TrackMapper._auto_heal_primary_mappings(
                db_model.id, fallback_mappings, session
            )
        elif connectors_needing_primary:
            # Track has non-primary mappings but can't be auto-healed
            logger.warning(
                f"Track {db_model.id} has non-primary mappings that cannot be auto-healed: "
                f"connectors={list(connectors_needing_primary)}, "
                f"session_available={session is not None}"
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
    async def _auto_heal_primary_mappings(
        track_id: int, fallback_mappings: dict[str, Any], session: AsyncSession
    ) -> None:
        """Auto-heal missing primary mappings by promoting the best non-primary mapping.

        This corrects data inconsistency where a track has connector mappings but
        none are marked as primary, which can happen due to migration issues,
        partial failures, or data corruption.

        Args:
            track_id: The track ID needing primary mapping healing
            fallback_mappings: Dict mapping connector_name to the mapping being used as fallback
        """
        # Start auto-healing process (removed debug clutter)
        try:
            from src.infrastructure.persistence.repositories.track.connector import (
                TrackConnectorRepository,
            )

            connector_repo = TrackConnectorRepository(session)
            healed_count = 0

            for connector_name, mapping in fallback_mappings.items():
                # Get the connector track to find the database ID
                conn_track = await TrackMapper._get_connector_track(mapping)
                if conn_track and conn_track.id:
                    # Promote this mapping to primary using the DB connector track ID
                    success = await connector_repo.set_primary_mapping(
                        track_id, conn_track.id, connector_name
                    )
                    if success:
                        healed_count += 1
                        logger.info(
                            f"Auto-healed primary mapping: track_id={track_id}, "
                            f"connector={connector_name}, "
                            f"connector_track_db_id={conn_track.id}, "
                            f"external_id={conn_track.connector_track_identifier}"
                        )
                    else:
                        logger.warning(
                            f"Failed to auto-heal primary mapping: track_id={track_id}, "
                            f"connector={connector_name}, "
                            f"connector_track_db_id={conn_track.id}"
                        )
                else:
                    logger.warning(
                        f"Could not get connector track for auto-healing: track_id={track_id}, "
                        f"connector={connector_name}"
                    )

            if healed_count > 0:
                logger.info(
                    f"Auto-healed {healed_count} primary mappings for track {track_id}"
                )

        except Exception as e:
            logger.error(f"Auto-healing failed for track {track_id}: {e}")
            # Don't re-raise - auto-healing is best-effort and shouldn't break the main flow

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
            return None
        except Exception as e:
            logger.debug(f"Error getting connector track: {e}")
            return None

    @staticmethod
    @override
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
            if isinstance(artist, str) and artist:
                names.append(artist)
            elif isinstance(artist, dict) and artist.get("name"):
                names.append(artist.get("name"))

        return names

    @staticmethod
    @override
    def get_default_relationships() -> list[str]:
        """Get default relationships to load for tracks."""
        return ["mappings", "likes"]
