"""Track connector repository for mapping tracks to external services."""

from datetime import UTC, datetime
from typing import Any, TypeVar, cast

from attrs import define
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities import Artist, ConnectorTrack, Track
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBTrackMapping,
)
from src.infrastructure.persistence.repositories.base_repo import (
    BaseModelMapper,
    BaseRepository,
)
from src.infrastructure.persistence.repositories.repo_decorator import db_operation
from src.infrastructure.persistence.repositories.track.core import TrackRepository
from src.infrastructure.persistence.repositories.track.mapper import TrackMapper

logger = get_logger(__name__)
T = TypeVar("T")


@define(frozen=True, slots=True)
class ConnectorTrackMapper(BaseModelMapper[DBConnectorTrack, dict[str, Any]]):
    """Maps between DBConnectorTrack and dictionary representation."""

    @staticmethod
    async def to_domain(db_model: DBConnectorTrack) -> dict[str, Any]:
        """Convert DB connector track to dictionary."""
        if not db_model:
            return {}

        return {
            "id": db_model.id,
            "connector_name": db_model.connector_name,
            "connector_track_id": db_model.connector_track_id,
            "title": db_model.title,
            "artists": db_model.artists,
            "album": db_model.album,
            "duration_ms": db_model.duration_ms,
            "release_date": db_model.release_date,
            "isrc": db_model.isrc,
            "raw_metadata": db_model.raw_metadata,
            "last_updated": db_model.last_updated,
        }

    @staticmethod
    def to_db(domain_model: dict[str, Any]) -> DBConnectorTrack:
        """Convert dictionary to DB connector track."""
        return DBConnectorTrack(
            connector_name=domain_model.get("connector_name"),
            connector_track_id=domain_model.get("connector_track_id"),
            title=domain_model.get("title"),
            artists=domain_model.get("artists"),
            album=domain_model.get("album"),
            duration_ms=domain_model.get("duration_ms"),
            release_date=domain_model.get("release_date"),
            isrc=domain_model.get("isrc"),
            raw_metadata=domain_model.get("raw_metadata"),
            last_updated=domain_model.get("last_updated", datetime.now(UTC)),
        )

    @staticmethod
    def get_default_relationships() -> list[str]:
        """Get default relationships to load for connector tracks."""
        return ["mappings"]


@define(frozen=True, slots=True)
class TrackMappingMapper(BaseModelMapper[DBTrackMapping, dict[str, Any]]):
    """Maps between DBTrackMapping and dictionary representation."""

    @staticmethod
    async def to_domain(db_model: DBTrackMapping) -> dict[str, Any]:
        """Convert DB mapping to dictionary."""
        if not db_model:
            return {}

        return {
            "id": db_model.id,
            "track_id": db_model.track_id,
            "connector_track_id": db_model.connector_track_id,
            "match_method": db_model.match_method,
            "confidence": db_model.confidence,
            "confidence_evidence": db_model.confidence_evidence,
        }

    @staticmethod
    def to_db(domain_model: dict[str, Any]) -> DBTrackMapping:
        """Convert dictionary to DB mapping."""
        return DBTrackMapping(
            track_id=domain_model.get("track_id"),
            connector_track_id=domain_model.get("connector_track_id"),
            match_method=domain_model.get("match_method"),
            confidence=domain_model.get("confidence"),
            confidence_evidence=domain_model.get("confidence_evidence"),
        )

    @staticmethod
    def get_default_relationships() -> list[str]:
        """Get default relationships to load for track mappings."""
        return ["track", "connector_track"]


class ConnectorTrackRepository(BaseRepository[DBConnectorTrack, dict[str, Any]]):
    """Repository for connector track operations."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with session and mapper."""
        super().__init__(
            session=session,
            model_class=DBConnectorTrack,
            mapper=ConnectorTrackMapper(),
        )


class TrackMappingRepository(BaseRepository[DBTrackMapping, dict[str, Any]]):
    """Repository for track mapping operations."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with session and mapper."""
        super().__init__(
            session=session,
            model_class=DBTrackMapping,
            mapper=TrackMappingMapper(),
        )


class TrackConnectorRepository:
    """Repository for track connector operations.

    Implements batch-first repository operations for connecting tracks
    to external music services. All single-item operations are implemented
    as degenerate cases of their bulk counterparts.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with session."""
        self.session = session
        self.track_mapper = TrackMapper()
        self.connector_repo = ConnectorTrackRepository(session)
        self.mapping_repo = TrackMappingRepository(session)
        self.track_repo = TrackRepository(session)

    @db_operation("find_tracks_by_connectors")
    async def find_tracks_by_connectors(
        self, connections: list[tuple[str, str]]
    ) -> dict[tuple[str, str], Track]:
        """Find tracks by connector name and ID in bulk.

        Args:
            connections: List of (connector, connector_id) tuples

        Returns:
            Dictionary mapping (connector, connector_id) tuples to Track objects
        """
        if not connections:
            return {}

        # Group by connector for efficiency
        by_connector = {}
        for connector, connector_id in connections:
            by_connector.setdefault(connector, []).append(connector_id)

        # Process each connector group
        results = {}
        for connector, connector_ids in by_connector.items():
            # Find connector tracks
            connector_tracks = await self.connector_repo.find_by([
                self.connector_repo.model_class.connector_name == connector,
                self.connector_repo.model_class.connector_track_id.in_(connector_ids),
            ])

            if not connector_tracks:
                continue

            # Create useful lookups
            ct_id_to_external_id = {
                ct["id"]: ct["connector_track_id"] for ct in connector_tracks
            }
            ct_ids = [ct["id"] for ct in connector_tracks]

            # Find mappings
            mappings = await self.mapping_repo.find_by([
                self.mapping_repo.model_class.connector_track_id.in_(ct_ids),
            ])

            # Create mapping from connector_track_id to track_id
            track_ids = [m["track_id"] for m in mappings]

            # Get unique track IDs and fetch tracks
            if track_ids:
                tracks_dict = await self.track_repo.find_tracks_by_ids(track_ids)

                # Build the result mapping with O(1) lookups
                for mapping in mappings:
                    ct_id = mapping["connector_track_id"]
                    track_id = mapping["track_id"]

                    # Use dictionary lookups for efficient access
                    if ct_id in ct_id_to_external_id and track_id in tracks_dict:
                        conn_id = ct_id_to_external_id[ct_id]
                        results[connector, conn_id] = tracks_dict[track_id]

        return results

    @db_operation("find_track_by_connector")
    async def find_track_by_connector(
        self, connector: str, connector_id: str
    ) -> Track | None:
        """Find a track by connector name and ID (degenerate case of bulk method)."""
        results = await self.find_tracks_by_connectors([(connector, connector_id)])
        return results.get((connector, connector_id))

    @db_operation("find_connector_track_id")
    async def find_connector_track_id(
        self, connector: str, connector_id: str
    ) -> int | None:
        """Find connector track database ID by connector name and ID."""
        stmt = (
            select(DBConnectorTrack.id)
            .filter(
                DBConnectorTrack.connector_name == connector,
                DBConnectorTrack.connector_track_id == connector_id,
                DBConnectorTrack.deleted_at.is_(None),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    @db_operation("map_tracks_to_connectors")
    async def map_tracks_to_connectors(
        self,
        mappings: list[tuple[Track, str, str, str, int, dict | None, dict | None]],
    ) -> list[Track]:
        """Map multiple tracks to connectors in a single bulk operation.

        Args:
            mappings: List of tuples with (track, connector, connector_id, match_method,
                    confidence, metadata, confidence_evidence)

        Returns:
            List of updated Track objects
        """
        if not mappings:
            return []

        # Collect all connector tracks for bulk insert
        connector_tracks_data = []
        connector_track_keys = set()
        mapping_data = []
        updated_tracks = []
        track_metadata_map = {}

        # Prepare all necessary data
        for (
            track,
            connector,
            connector_id,
            _,
            _,
            metadata,
            _,
        ) in mappings:
            if track.id is None:
                continue

            # Prepare connector track data
            connector_track_key = (connector, connector_id)
            if connector_track_key not in connector_track_keys:
                connector_tracks_data.append({
                    "connector_name": connector,
                    "connector_track_id": connector_id,
                    "title": track.title,
                    "artists": {"names": [a.name for a in track.artists]}
                    if track.artists
                    else {"names": []},
                    "album": track.album,
                    "duration_ms": track.duration_ms,
                    "release_date": track.release_date,
                    "isrc": track.isrc,
                    "raw_metadata": metadata or {},
                    "last_updated": datetime.now(UTC),
                })
                connector_track_keys.add(connector_track_key)

            # Create updated track object
            updated_track = track.with_connector_track_id(connector, connector_id)
            if metadata:
                updated_track = updated_track.with_connector_metadata(
                    connector, metadata
                )
                track_metadata_map.setdefault(track.id, {})[connector] = metadata

            updated_tracks.append(updated_track)

        # Bulk upsert connector tracks
        connector_tracks_result = await self.connector_repo.bulk_upsert(
            connector_tracks_data,
            lookup_keys=["connector_name", "connector_track_id"],
            return_models=True,  # Add this parameter to ensure we get models back
        )

        # Handle the case where bulk_upsert returns an integer count instead of models
        if isinstance(connector_tracks_result, int):
            # If we got an integer result, we need to fetch the tracks explicitly
            connector_name_ids = [
                (data["connector_name"], data["connector_track_id"])
                for data in connector_tracks_data
            ]

            # Build query to find all connector tracks by name and ID pairs
            stmt = select(self.connector_repo.model_class).where(
                self.connector_repo.model_class.connector_name.in_([
                    c[0] for c in connector_name_ids
                ]),
                self.connector_repo.model_class.connector_track_id.in_([
                    c[1] for c in connector_name_ids
                ]),
            )

            # Fetch the connector tracks
            result = await self.session.execute(stmt)
            connector_tracks = []
            for row in result.scalars().all():
                domain_model = await self.connector_repo.mapper.to_domain(row)
                if domain_model:
                    connector_tracks.append(domain_model)
        else:
            # Use the returned models directly
            connector_tracks = cast("list[dict[str, Any]]", connector_tracks_result)

        # Create connector ID to DB ID mapping
        connector_id_map = {
            (ct["connector_name"], ct["connector_track_id"]): ct["id"]
            for ct in connector_tracks
        }

        # Prepare mapping data
        for _i, (
            track,
            connector,
            connector_id,
            match_method,
            confidence,
            _,
            confidence_evidence,
        ) in enumerate(mappings):
            if track.id is None or (connector, connector_id) not in connector_id_map:
                continue

            connector_track_id = connector_id_map[connector, connector_id]
            mapping_data.append({
                "track_id": track.id,
                "connector_track_id": connector_track_id,
                "match_method": match_method,
                "confidence": confidence,
                "confidence_evidence": confidence_evidence,
            })

        # Bulk upsert mappings
        if mapping_data:
            await self.mapping_repo.bulk_upsert(
                mapping_data,
                lookup_keys=["track_id", "connector_track_id"],
                return_models=False,
            )

        # Note: Metrics processing moved to MetricsApplicationService
        # This repository focuses on track mapping only
        # Metrics extraction is handled at the application layer

        return updated_tracks

    @db_operation("map_track_to_connector")
    async def map_track_to_connector(
        self,
        track: Track,
        connector: str,
        connector_id: str,
        match_method: str,
        confidence: int,
        metadata: dict | None = None,
        confidence_evidence: dict | None = None,
    ) -> Track:
        """Map an existing track to a connector (degenerate case of bulk method)."""
        if track.id is None:
            raise ValueError("Cannot map track with no ID")

        # Ensure the track exists
        try:
            await self.track_repo.get_by_id(track.id)
        except ValueError as err:
            raise ValueError(f"Track with ID {track.id} not found") from err

        results = await self.map_tracks_to_connectors([
            (
                track,
                connector,
                connector_id,
                match_method,
                confidence,
                metadata,
                confidence_evidence,
            )
        ])

        return results[0] if results else track

    @db_operation("ingest_external_tracks_bulk")
    async def ingest_external_tracks_bulk(
        self,
        connector: str,
        tracks: list[ConnectorTrack],
    ) -> list[Track]:
        """Bulk ingest multiple tracks from external connector.

        This is the primary method for track ingestion, optimized for bulk operations.
        Single-track operations are implemented as a special case of this method.

        Args:
            connector: Connector name (e.g., "spotify")
            tracks: List of connector tracks to ingest

        Returns:
            List of domain Track models
        """
        if not tracks:
            return []

        # 1. Bulk upsert all connector tracks
        connector_track_data = [
            {
                "connector_name": connector,
                "connector_track_id": track.connector_track_id,
                "title": track.title,
                "artists": {"names": [a.name for a in track.artists]}
                if track.artists
                else {"names": []},
                "album": track.album,
                "duration_ms": track.duration_ms,
                "release_date": track.release_date,
                "isrc": track.isrc,
                "raw_metadata": track.raw_metadata or {},
                "last_updated": datetime.now(UTC),
            }
            for track in tracks
        ]

        connector_tracks = await self.connector_repo.bulk_upsert(
            connector_track_data,
            lookup_keys=["connector_name", "connector_track_id"],
        )

        # 2. Create a lookup dict for connector tracks
        connector_track_lookup: dict[str, dict[str, Any]] = {}
        if isinstance(connector_tracks, list):
            for ct in connector_tracks:
                connector_track_lookup[ct["connector_track_id"]] = ct

        # 3. Create or find domain tracks
        domain_tracks = []
        track_mappings_data = []
        metrics_data = []

        for track in tracks:
            # Try to find existing mapping first
            connector_track_id = connector_track_lookup[track.connector_track_id]["id"]

            mapping = await self.mapping_repo.find_one_by({
                "connector_track_id": connector_track_id,
                "is_deleted": False,
            })

            if mapping:
                # Track exists, retrieve it
                domain_track = await self.track_repo.get_by_id(mapping["track_id"])
                logger.debug(
                    f"Found existing track {mapping['track_id']} for "
                    f"{connector}:{track.connector_track_id}"
                )
                domain_tracks.append(domain_track)

                # Update mapping confidence if needed
                if mapping["confidence"] < 100:
                    await self.mapping_repo.update(mapping["id"], {"confidence": 100})
            else:
                # Create new track
                artists = (
                    [Artist(name=a.name) for a in track.artists]
                    if track.artists
                    else []
                )
                track_obj = Track(
                    title=track.title,
                    artists=artists,
                    album=track.album,
                    duration_ms=track.duration_ms,
                    release_date=track.release_date,
                    isrc=track.isrc,
                )

                # Add connector ID and metadata
                track_obj = track_obj.with_connector_track_id(
                    connector, track.connector_track_id
                )
                track_obj = track_obj.with_connector_metadata(
                    connector, track.raw_metadata or {}
                )

                # Save track and get ID
                domain_track = await self.track_repo.save_track(track_obj)
                domain_tracks.append(domain_track)

                # Prepare mapping data for bulk insert
                if domain_track.id is not None:
                    track_mappings_data.append({
                        "track_id": domain_track.id,
                        "connector_track_id": connector_track_id,
                        "match_method": "direct",
                        "confidence": 100,
                    })

                    # Prepare metrics data
                    if track.raw_metadata:
                        metrics_data.append((domain_track.id, track.raw_metadata))

        # 5. Bulk create mappings if any
        if track_mappings_data:
            await self.mapping_repo.bulk_upsert(
                track_mappings_data,
                lookup_keys=["track_id", "connector_track_id"],
                return_models=False,
            )

        # Note: Metrics processing moved to MetricsApplicationService
        # This repository focuses on track ingestion only
        # Metrics extraction is handled at the application layer

        return domain_tracks

    @db_operation("ingest_external_track")
    async def ingest_external_track(
        self,
        connector: str,
        connector_id: str,
        metadata: dict | None,
        title: str,
        artists: list[str],
        album: str | None = None,
        duration_ms: int | None = None,
        release_date: datetime | None = None,
        isrc: str | None = None,
        added_at: str | None = None,
    ) -> Track | None:
        """Ingest a single track from external source.

        This is implemented as a special case of ingest_external_tracks_bulk
        for DRY purposes. All the logic is in the bulk method.

        Args:
            connector: Connector name (e.g., "spotify")
            connector_id: ID of the track in the external service
            metadata: Raw metadata from the external service
            title: Track title
            artists: List of artist names
            album: Album name
            duration_ms: Duration in milliseconds
            release_date: Release date of the track
            isrc: ISRC code for the track
            added_at: Timestamp when the track was added to a playlist

        Returns:
            Domain Track model
        """
        # Ensure we have a metadata dictionary
        actual_metadata = metadata or {}

        # Add the added_at timestamp to metadata if provided
        if added_at and "added_at" not in actual_metadata:
            actual_metadata["added_at"] = added_at

        # Convert parameters to a ConnectorTrack
        connector_track = ConnectorTrack(
            connector_name=connector,
            connector_track_id=connector_id,
            title=title,
            artists=[Artist(name=name) for name in artists] if artists else [],
            album=album,
            duration_ms=duration_ms,
            release_date=release_date,
            isrc=isrc,
            raw_metadata=actual_metadata,
        )

        # Call the bulk method with a list of one item
        tracks = await self.ingest_external_tracks_bulk(connector, [connector_track])
        if not tracks:
            logger.warning(f"Failed to ingest track {connector}:{connector_id}")
            return None
        return tracks[0]

    @db_operation("create_track_from_connector_data")
    async def create_track_from_connector_data(
        self, track: Track, connector: str
    ) -> Track | None:
        """Create a track directly from connector data.

        This is a convenience method that leverages the bulk ingest operation
        under the hood for consistency.
        """
        if not track.title or not track.artists:
            raise ValueError("Track must have title and artists")

        if connector not in track.connector_track_ids:
            raise ValueError(f"Track doesn't have an ID for connector {connector}")

        # Use the ingest method for consistency
        connector_id = track.connector_track_ids[connector]
        metadata = (
            track.connector_metadata.get(connector, {})
            if hasattr(track, "connector_metadata")
            else {}
        )

        # Extract added_at from metadata if present
        added_at = metadata.get("added_at")

        return await self.ingest_external_track(
            connector=connector,
            connector_id=connector_id,
            metadata=metadata,
            title=track.title,
            artists=[a.name for a in track.artists] if track.artists else [],
            album=track.album,
            duration_ms=track.duration_ms,
            release_date=track.release_date,
            isrc=track.isrc,
            added_at=added_at,
        )

    @db_operation("get_connector_mappings")
    async def get_connector_mappings(
        self,
        track_ids: list[int],
        connector: str | None = None,
    ) -> dict[int, dict[str, str]]:
        """Get mappings between tracks and external connectors."""
        if not track_ids:
            return {}

        # Build efficient join between mappings and connector tracks
        stmt = (
            select(
                self.mapping_repo.model_class.track_id,
                self.connector_repo.model_class.connector_name,
                self.connector_repo.model_class.connector_track_id,
            )
            .join(
                self.connector_repo.model_class,
                self.mapping_repo.model_class.connector_track_id
                == self.connector_repo.model_class.id,
            )
            .where(
                self.mapping_repo.model_class.track_id.in_(track_ids),
                self.mapping_repo.model_class.is_deleted == False,  # noqa: E712
                self.connector_repo.model_class.is_deleted == False,  # noqa: E712
            )
        )

        if connector:
            stmt = stmt.where(
                self.connector_repo.model_class.connector_name == connector
            )

        # Execute and build response
        result = await self.session.execute(stmt)

        mappings_dict = {}
        for track_id, conn_name, conn_id in result:
            mappings_dict.setdefault(track_id, {})[conn_name] = conn_id

        return mappings_dict

    @db_operation("get_connector_metadata")
    async def get_connector_metadata(
        self,
        track_ids: list[int],
        connector: str,
        metadata_field: str | None = None,
    ) -> dict[int, dict[str, Any] | Any]:
        """Get connector metadata for tracks."""
        if not track_ids:
            return {}

        # Build efficient join query
        stmt = (
            select(
                self.mapping_repo.model_class.track_id,
                self.connector_repo.model_class.raw_metadata,
            )
            .join(
                self.connector_repo.model_class,
                self.mapping_repo.model_class.connector_track_id
                == self.connector_repo.model_class.id,
            )
            .where(
                self.mapping_repo.model_class.track_id.in_(track_ids),
                self.connector_repo.model_class.connector_name == connector,
                self.mapping_repo.model_class.is_deleted == False,  # noqa: E712
                self.connector_repo.model_class.is_deleted == False,  # noqa: E712
            )
        )

        # Execute and build response
        result = await self.session.execute(stmt)

        # Return either the specific field or all metadata
        if metadata_field:
            return {
                track_id: metadata.get(metadata_field)
                for track_id, metadata in result
                if metadata and metadata_field in metadata
            }
        else:
            return {track_id: metadata for track_id, metadata in result if metadata}

    @db_operation("get_connector_metadata_with_timestamps")
    async def get_connector_metadata_with_timestamps(
        self,
        track_ids: list[int],
        connector: str,
    ) -> dict[int, dict[str, Any]]:
        """Get connector metadata for tracks with last_updated timestamps."""
        if not track_ids:
            return {}

        # Build efficient join query including last_updated
        stmt = (
            select(
                self.mapping_repo.model_class.track_id,
                self.connector_repo.model_class.raw_metadata,
                self.connector_repo.model_class.last_updated,
            )
            .join(
                self.connector_repo.model_class,
                self.mapping_repo.model_class.connector_track_id
                == self.connector_repo.model_class.id,
            )
            .where(
                self.mapping_repo.model_class.track_id.in_(track_ids),
                self.connector_repo.model_class.connector_name == connector,
                self.mapping_repo.model_class.is_deleted == False,  # noqa: E712
                self.connector_repo.model_class.is_deleted == False,  # noqa: E712
            )
        )

        # Execute and build response
        result = await self.session.execute(stmt)

        response = {}
        for track_id, metadata, last_updated in result:
            if metadata:
                response[track_id] = {"data": metadata, "last_updated": last_updated}
        return response

    @db_operation("save_mapping_confidence")
    async def save_mapping_confidence(
        self,
        track_id: int,
        connector: str,
        connector_id: str,
        confidence: int,
        match_method: str | None = None,
        confidence_evidence: dict | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Save confidence information to the track mapping."""
        # Find the connector track first
        connector_track = await self.connector_repo.find_one_by({
            "connector_name": connector,
            "connector_track_id": connector_id,
        })

        if not connector_track or "id" not in connector_track:
            return False

        # Update connector track metadata if provided
        if metadata is not None:
            try:
                from datetime import UTC, datetime

                # Update the connector track with fresh metadata and timestamp
                await self.connector_repo.update(
                    connector_track["id"],
                    {
                        "raw_metadata": metadata,
                        "last_updated": datetime.now(UTC),
                    },
                )
            except Exception as e:
                logger.warning(f"Failed to update connector track metadata: {e}")
                # Don't fail the entire operation if metadata update fails

        # Update mapping using upsert
        update_data: dict[str, Any] = {"confidence": confidence}
        if match_method:
            update_data["match_method"] = match_method
        if confidence_evidence:
            update_data["confidence_evidence"] = confidence_evidence

        try:
            await self.mapping_repo.upsert(
                lookup_attrs={
                    "track_id": track_id,
                    "connector_track_id": connector_track["id"],
                },
                create_attrs=update_data,
            )
            return True
        except ValueError:
            return False

    @db_operation("get_mapping_info")
    async def get_mapping_info(
        self, track_id: int, connector: str, connector_id: str
    ) -> dict:
        """Get mapping information including confidence and method."""
        # Find connector track
        connector_track = await self.connector_repo.find_one_by({
            "connector_name": connector,
            "connector_track_id": connector_id,
        })

        if not connector_track:
            return {}

        # Find mapping
        mapping = await self.mapping_repo.find_one_by({
            "track_id": track_id,
            "connector_track_id": connector_track["id"],
        })

        if not mapping:
            return {}

        return {
            "confidence": mapping["confidence"],
            "match_method": mapping["match_method"],
            "confidence_evidence": mapping["confidence_evidence"],
        }

    @db_operation("get_metadata_timestamps")
    async def get_metadata_timestamps(
        self, track_ids: list[int], connector: str
    ) -> dict[int, datetime]:
        """Get most recent metadata collection timestamps for tracks.

        Args:
            track_ids: Track IDs to check timestamps for.
            connector: Connector name to filter by.

        Returns:
            Dictionary mapping track_id to most recent collected_at timestamp.
        """
        if not track_ids:
            return {}

        try:
            from sqlalchemy import func, select

            from src.infrastructure.persistence.database.db_models import DBTrackMetric

            # Query for the most recent collected_at timestamp for each track
            stmt = (
                select(
                    DBTrackMetric.track_id,
                    func.max(DBTrackMetric.collected_at).label("latest_collected_at"),
                )
                .where(
                    DBTrackMetric.track_id.in_(track_ids),
                    DBTrackMetric.connector_name == connector,
                    DBTrackMetric.is_deleted == False,  # noqa: E712
                )
                .group_by(DBTrackMetric.track_id)
            )

            result = await self.session.execute(stmt)
            rows = result.fetchall()

            timestamps = {}
            for row in rows:
                track_id = row[0]
                collected_at = row[1]

                # Ensure UTC timezone for consistency - database stores UTC timestamps
                # If no timezone info, assume it's already UTC (our standard)
                if collected_at and collected_at.tzinfo is None:
                    collected_at = collected_at.replace(tzinfo=UTC)

                timestamps[track_id] = collected_at

            return timestamps

        except Exception as e:
            logger.error(f"Failed to get metadata timestamps: {e}")
            return {}

    @db_operation("set_primary_mapping")
    async def set_primary_mapping(
        self, track_id: int, connector_track_id: int, connector_name: str
    ) -> bool:
        """Set the primary mapping for a track-connector pair.

        This method handles Spotify track relinking and other scenarios where
        multiple connector tracks map to the same canonical track. It ensures
        only one mapping per (track_id, connector_name) is marked as primary.

        Args:
            track_id: Internal canonical track ID
            connector_track_id: Database ID of the connector track (not external ID)
            connector_name: Name of the connector (e.g., "spotify")

        Returns:
            True if the primary mapping was successfully updated, False otherwise
        """
        try:
            # Step 1: Reset all primaries for this track-connector pair
            await self.session.execute(
                update(DBTrackMapping)
                .where(
                    DBTrackMapping.track_id == track_id,
                    DBTrackMapping.connector_name == connector_name,
                    DBTrackMapping.is_deleted == False,  # noqa: E712
                )
                .values(is_primary=False)
            )

            # Step 2: Set the specified mapping as primary
            result = await self.session.execute(
                update(DBTrackMapping)
                .where(
                    DBTrackMapping.track_id == track_id,
                    DBTrackMapping.connector_track_id == connector_track_id,
                    DBTrackMapping.is_deleted == False,  # noqa: E712
                )
                .values(is_primary=True)
            )

            success = result.rowcount > 0
            if success:
                logger.debug(
                    f"Set primary mapping: track_id={track_id}, "
                    f"connector_track_id={connector_track_id}, "
                    f"connector={connector_name}"
                )
            else:
                logger.warning(
                    f"Failed to set primary mapping - no matching record found: "
                    f"track_id={track_id}, connector_track_id={connector_track_id}, "
                    f"connector={connector_name}"
                )

            return success

        except Exception as e:
            logger.error(
                f"Error setting primary mapping for track_id={track_id}, "
                f"connector_track_id={connector_track_id}, connector={connector_name}: {e}"
            )
            return False
