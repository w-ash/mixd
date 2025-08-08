"""Core track repository implementation for basic track operations."""

from typing import Any, ClassVar

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities import Artist, Track
from src.infrastructure.persistence.database.db_models import DBTrack
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
    # ENHANCED QUERY METHODS
    # -------------------------------------------------------------------------

    def select_with_relations(self) -> Select:
        """Create select statement with standard relations loaded."""
        return self.with_default_relationships(self.select())

    def select_by_id_type(self, id_type: str, id_value: str) -> Select:
        """Build a query to fetch tracks by a specific ID type."""
        # Direct attribute lookup for known ID types
        if id_type in self._TRACK_ID_TYPES:
            column_name = self._TRACK_ID_TYPES[id_type]
            return self.select_with_relations().where(
                getattr(self.model_class, column_name) == id_value,
            )

        # Return empty result for unsupported ID types
        return self.select().where(self.model_class.id == -1)

    # -------------------------------------------------------------------------
    # PUBLIC API METHODS
    # -------------------------------------------------------------------------

    @db_operation("get_track")
    async def get_track(self, id_type: str, id_value: str) -> Track:
        """Get track by any identifier type."""
        if id_type not in self._TRACK_ID_TYPES:
            raise ValueError(f"Unsupported ID type: {id_type}")

        if id_type == "internal":
            # Use get_by_id for internal IDs
            return await self.get_by_id(int(id_value))

        # Use find_one_by for other ID types
        track = await self.find_one_by({self._TRACK_ID_TYPES[id_type]: id_value})
        if not track:
            raise ValueError(f"Track with {id_type}={id_value} not found")
        return track

    @db_operation("find_track")
    async def find_track(self, id_type: str, id_value: str) -> Track | None:
        """Find track by identifier, returning None if not found."""
        if id_type not in self._TRACK_ID_TYPES:
            return None

        if id_type == "internal":
            try:
                return await self.get_by_id(int(id_value))
            except ValueError:
                return None

        # Use find_one_by for other ID types
        return await self.find_one_by({self._TRACK_ID_TYPES[id_type]: id_value})

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
        if "spotify" in track.connector_track_ids:
            values["spotify_id"] = track.connector_track_ids["spotify"]
        if "musicbrainz" in track.connector_track_ids:
            values["mbid"] = track.connector_track_ids["musicbrainz"]

        # Handle lookups by ISRC or Spotify ID - leverage the improved upsert with direct values
        # The upsert method has been updated to use a two-phase approach that avoids greenlet issues
        if track.isrc:
            return await self.upsert({"isrc": track.isrc}, values)
        elif "spotify" in track.connector_track_ids:
            return await self.upsert(
                {"spotify_id": track.connector_track_ids["spotify"]}, values
            )

        # Create new track with explicit eager loading for relationships
        db_track = DBTrack(**values)
        self.session.add(db_track)
        await self.session.flush()

        # Refresh with explicit eager loading of relationships to avoid lazy loading
        default_rels = self.mapper.get_default_relationships()
        if default_rels:
            await self.session.refresh(db_track, attribute_names=default_rels)
        else:
            await self.session.refresh(db_track)

        # Map back to domain model - the to_domain method has been updated to use AsyncAttrs safely
        return await TrackMapper.to_domain(db_track, self.session)

    @db_operation("get_or_create")
    async def get_or_create(
        self,
        lookup_attrs: dict[str, Any],
        create_attrs: dict[str, Any] | None = None,
    ) -> tuple[Track, bool]:
        """Find a track by attributes or create it if it doesn't exist."""
        # Try to find using lookup attributes
        conditions = [
            getattr(self.model_class, k) == v
            for k, v in lookup_attrs.items()
            if hasattr(self.model_class, k)
        ]

        existing = await self.find_one_by(conditions)
        if existing:
            return existing, False

        # Prepare combined attributes
        all_attrs = {**lookup_attrs}
        if create_attrs:
            all_attrs.update(create_attrs)

        # Process artists data
        artists = []
        for artist_data in all_attrs.get("artists", []):
            if isinstance(artist_data, str):
                artists.append(Artist(name=artist_data))
            elif isinstance(artist_data, Artist):
                artists.append(artist_data)
            elif isinstance(artist_data, dict) and "name" in artist_data:
                artists.append(Artist(name=artist_data["name"]))

        # Create track object
        track = Track(
            title=all_attrs.get("title", ""),
            artists=artists or [Artist(name="")] if all_attrs.get("title") else [],
            album=all_attrs.get("album"),
            duration_ms=all_attrs.get("duration_ms"),
            release_date=all_attrs.get("release_date"),
            isrc=all_attrs.get("isrc"),
            connector_track_ids=all_attrs.get("connector_track_ids", {}),
            connector_metadata=all_attrs.get("connector_metadata", {}),
        )

        # Create and return
        created_track = await self.create(track)
        return created_track, True
