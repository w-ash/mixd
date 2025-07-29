"""Track mappers for converting between domain and database models."""

from datetime import UTC, datetime
from typing import Any, override

from attrs import define

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
        if not db_model:
            return None

        # Use only eager-loaded relationships to avoid greenlet issues
        mappings = getattr(db_model, "mappings", []) or []
        active_mappings = [m for m in mappings if not m.is_deleted]

        likes = getattr(db_model, "likes", []) or []
        active_likes = [like for like in likes if not like.is_deleted]

        # Build connector IDs and metadata
        connector_track_ids = {}
        connector_metadata = {}

        # Add internal ID first
        if db_model.id:
            connector_track_ids["db"] = str(db_model.id)

        # Add direct IDs from the track model
        if db_model.spotify_id:
            connector_track_ids["spotify"] = db_model.spotify_id
        if db_model.mbid:
            connector_track_ids["musicbrainz"] = db_model.mbid

        # Process connector track mappings with primary awareness
        # First pass: collect all primary mappings
        for mapping in active_mappings:
            if mapping.is_primary:
                conn_track = await TrackMapper._get_connector_track(mapping)
                if conn_track and not conn_track.is_deleted:
                    connector_name = conn_track.connector_name
                    connector_track_ids[connector_name] = conn_track.connector_track_id
                    connector_metadata[connector_name] = conn_track.raw_metadata or {}

        # Second pass: fill in any missing connectors with non-primary mappings (fallback)
        for mapping in active_mappings:
            if not mapping.is_primary:
                conn_track = await TrackMapper._get_connector_track(mapping)
                if conn_track and not conn_track.is_deleted:
                    connector_name = conn_track.connector_name
                    # Only use non-primary if no primary exists for this connector
                    if connector_name not in connector_track_ids:
                        connector_track_ids[connector_name] = (
                            conn_track.connector_track_id
                        )
                        connector_metadata[connector_name] = (
                            conn_track.raw_metadata or {}
                        )
                        logger.debug(
                            f"Using non-primary mapping as fallback for {connector_name} "
                            f"on track {db_model.id}"
                        )

        # Process likes into connector metadata
        for like in active_likes:
            service = like.service
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
            connector_track_ids=connector_track_ids,
            connector_metadata=connector_metadata,
        )

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
            spotify_id=domain_model.connector_track_ids.get("spotify"),
            mbid=domain_model.connector_track_ids.get("musicbrainz"),
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
    def create_connector_track(
        connector: str, connector_id: str, metadata: dict[str, Any]
    ) -> DBConnectorTrack:
        """Create a connector track from connector data."""
        # Extract artist names from either string or object lists
        artist_names = TrackMapper.extract_artist_names(metadata.get("artists", []))

        # Create track with only service data
        return DBConnectorTrack(
            connector_name=connector,
            connector_track_id=connector_id,
            title=metadata.get("title"),
            artists={"names": artist_names} if artist_names else None,
            album=metadata.get("album"),
            duration_ms=metadata.get("duration_ms"),
            release_date=metadata.get("release_date"),
            isrc=metadata.get("isrc"),
            raw_metadata=metadata,
            last_updated=datetime.now(UTC),
        )

    @staticmethod
    @override
    def get_default_relationships() -> list[str]:
        """Get default relationships to load for tracks."""
        return ["mappings", "likes"]
