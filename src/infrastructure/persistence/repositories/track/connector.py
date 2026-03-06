"""Manages track connections between internal database and external music services.

Handles track ingestion from Spotify, Last.fm, and other music platforms, maps external
tracks to canonical internal tracks, and stores service-specific metadata and IDs.
"""

# pyright: reportImportCycles=false, reportExplicitAny=false, reportAny=false
# Intentional lazy import cycle: mapper.py lazily imports this module to delegate
# primary mapping promotion to set_primary_mapping() (avoids duplicating SQL logic).

from datetime import UTC, datetime
from typing import Any, cast, override

from attrs import define
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.config.constants import BusinessLimits
from src.domain.entities import Artist, ConnectorTrack, Track, TrackMapping
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBTrack,
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


@define(frozen=True, slots=True)
class ConnectorTrackMapper(BaseModelMapper[DBConnectorTrack, ConnectorTrack]):
    """Converts external service track data between database and domain formats."""

    @override
    @staticmethod
    async def to_domain(db_model: DBConnectorTrack) -> ConnectorTrack:
        """Convert database connector track to domain ConnectorTrack.

        Args:
            db_model: Database model instance.

        Returns:
            ConnectorTrack domain entity.
        """
        return ConnectorTrack(
            id=db_model.id,
            connector_name=db_model.connector_name,
            connector_track_identifier=db_model.connector_track_identifier,
            title=db_model.title,
            artists=[Artist(name=n) for n in db_model.artists.get("names", [])],
            album=db_model.album,
            duration_ms=db_model.duration_ms,
            release_date=db_model.release_date,
            isrc=db_model.isrc,
            raw_metadata=db_model.raw_metadata or {},
            last_updated=db_model.last_updated,
        )

    @override
    @staticmethod
    def to_db(domain_model: ConnectorTrack) -> DBConnectorTrack:
        """Convert ConnectorTrack domain entity to database model.

        Args:
            domain_model: ConnectorTrack domain entity.

        Returns:
            Database model instance ready for persistence.
        """
        return DBConnectorTrack(
            connector_name=domain_model.connector_name,
            connector_track_identifier=domain_model.connector_track_identifier,
            title=domain_model.title,
            artists={"names": [a.name for a in domain_model.artists]},
            album=domain_model.album,
            duration_ms=domain_model.duration_ms,
            release_date=domain_model.release_date,
            isrc=domain_model.isrc,
            raw_metadata=domain_model.raw_metadata,
            last_updated=domain_model.last_updated,
        )

    @override
    @staticmethod
    def get_default_relationships() -> list[str]:
        """Get related entities to load when querying connector tracks."""
        return ["mappings"]


@define(frozen=True, slots=True)
class TrackMappingMapper(BaseModelMapper[DBTrackMapping, TrackMapping]):
    """Converts track-to-service mapping data between database and domain formats."""

    @override
    @staticmethod
    async def to_domain(db_model: DBTrackMapping) -> TrackMapping:
        """Convert database mapping to TrackMapping domain entity.

        Args:
            db_model: Database mapping instance.

        Returns:
            TrackMapping domain entity with confidence scores.
        """
        return TrackMapping(
            id=db_model.id,
            track_id=db_model.track_id,
            connector_track_id=db_model.connector_track_id,
            connector_name=db_model.connector_name,
            match_method=db_model.match_method,
            confidence=db_model.confidence,
            confidence_evidence=db_model.confidence_evidence,
            is_primary=db_model.is_primary,
        )

    @override
    @staticmethod
    def to_db(domain_model: TrackMapping) -> DBTrackMapping:
        """Convert TrackMapping domain entity to database mapping.

        Args:
            domain_model: TrackMapping domain entity.

        Returns:
            Database mapping instance ready for persistence.
        """
        return DBTrackMapping(
            track_id=domain_model.track_id,
            connector_track_id=domain_model.connector_track_id,
            connector_name=domain_model.connector_name,
            match_method=domain_model.match_method,
            confidence=domain_model.confidence,
            confidence_evidence=domain_model.confidence_evidence,
            is_primary=domain_model.is_primary,
        )

    @override
    @staticmethod
    def get_default_relationships() -> list[str]:
        """Get related entities to load when querying track mappings."""
        return ["track", "connector_track"]


class ConnectorTrackRepository(BaseRepository[DBConnectorTrack, ConnectorTrack]):
    """Manages external service track data storage and retrieval."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize with database session and data mapper."""
        super().__init__(
            session=session,
            model_class=DBConnectorTrack,
            mapper=ConnectorTrackMapper(),
        )


class TrackMappingRepository(BaseRepository[DBTrackMapping, TrackMapping]):
    """Manages track-to-service mapping storage and retrieval."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize with database session and data mapper."""
        super().__init__(
            session=session,
            model_class=DBTrackMapping,
            mapper=TrackMappingMapper(),
        )


class TrackConnectorRepository:
    """Connects internal tracks with external music services like Spotify and Last.fm.

    Handles ingesting tracks from external APIs, mapping them to canonical internal
    tracks, and managing service-specific metadata. Optimized for bulk operations
    to efficiently process large playlists and libraries.
    """

    session: AsyncSession
    track_mapper: TrackMapper
    connector_repo: ConnectorTrackRepository
    mapping_repo: TrackMappingRepository
    track_repo: TrackRepository

    def __init__(self, session: AsyncSession) -> None:
        """Initialize with database session and dependent repositories."""
        self.session = session
        self.track_mapper = TrackMapper()
        self.connector_repo = ConnectorTrackRepository(session)
        self.mapping_repo = TrackMappingRepository(session)
        self.track_repo = TrackRepository(session)

    @staticmethod
    def _build_connector_track_dict(
        connector_name: str,
        identifier: str,
        title: str,
        artists: list[Artist],
        album: str | None,
        duration_ms: int | None,
        release_date: datetime | None,
        isrc: str | None,
        raw_metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Build a dict suitable for bulk_upsert of connector tracks."""
        return {
            "connector_name": connector_name,
            "connector_track_identifier": identifier,
            "title": title,
            "artists": {"names": [a.name for a in artists]}
            if artists
            else {"names": []},
            "album": album,
            "duration_ms": duration_ms,
            "release_date": release_date,
            "isrc": isrc,
            "raw_metadata": raw_metadata or {},
            "last_updated": datetime.now(UTC),
        }

    @db_operation("find_tracks_by_connectors")
    async def find_tracks_by_connectors(
        self, connections: list[tuple[str, str]]
    ) -> dict[tuple[str, str], Track]:
        """Find internal tracks by their external service IDs.

        Args:
            connections: List of (service_name, external_id) pairs to lookup.

        Returns:
            Dictionary mapping (service_name, external_id) to Track objects.
        """
        if not connections:
            return {}

        # Group by connector for efficiency
        by_connector: dict[str, list[str]] = {}
        for connector, connector_id in connections:
            by_connector.setdefault(connector, []).append(connector_id)

        # Process each connector group
        results: dict[tuple[str, str], Track] = {}
        for connector, connector_ids in by_connector.items():
            # Find connector tracks
            connector_tracks = await self.connector_repo.find_by([
                self.connector_repo.model_class.connector_name == connector,
                self.connector_repo.model_class.connector_track_identifier.in_(
                    connector_ids
                ),
            ])

            if not connector_tracks:
                continue

            # Create useful lookups
            ct_id_to_external_id = {
                ct.id: ct.connector_track_identifier
                for ct in connector_tracks
                if ct.id is not None
            }
            ct_ids = [ct.id for ct in connector_tracks if ct.id is not None]

            # Find mappings
            mappings = await self.mapping_repo.find_by([
                self.mapping_repo.model_class.connector_track_id.in_(ct_ids),
            ])

            # Create mapping from connector_track_id to track_id
            track_ids = [m.track_id for m in mappings]

            # Get unique track IDs and fetch tracks
            if track_ids:
                tracks_dict = await self.track_repo.find_tracks_by_ids(track_ids)

                # Build the result mapping with O(1) lookups
                for mapping in mappings:
                    ct_id = mapping.connector_track_id
                    track_id = mapping.track_id

                    # Use dictionary lookups for efficient access
                    if ct_id in ct_id_to_external_id and track_id in tracks_dict:
                        conn_id = ct_id_to_external_id[ct_id]
                        results[connector, conn_id] = tracks_dict[track_id]

        return results

    @db_operation("find_track_by_connector")
    async def find_track_by_connector(
        self, connector: str, connector_id: str
    ) -> Track | None:
        """Find an internal track by its external service ID."""
        results = await self.find_tracks_by_connectors([(connector, connector_id)])
        return results.get((connector, connector_id))

    @db_operation("map_tracks_to_connectors")
    async def map_tracks_to_connectors(
        self,
        mappings: list[
            tuple[
                Track, str, str, str, int, dict[str, Any] | None, dict[str, Any] | None
            ]
        ],
    ) -> list[Track]:
        """Link existing internal tracks to external service IDs with confidence scores.

        Args:
            mappings: List of (track, service_name, external_id, match_method,
                    confidence, metadata, confidence_evidence) tuples.

        Returns:
            List of Track objects updated with external service connections.
        """
        if not mappings:
            return []

        # Collect all connector tracks for bulk insert
        connector_tracks_data: list[dict[str, Any]] = []
        connector_track_keys: set[tuple[str, str]] = set()
        mapping_data: list[dict[str, Any]] = []
        updated_tracks: list[Track] = []
        track_metadata_map: dict[int, dict[str, Any]] = {}

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
                connector_tracks_data.append(
                    self._build_connector_track_dict(
                        connector,
                        connector_id,
                        track.title,
                        track.artists,
                        track.album,
                        track.duration_ms,
                        track.release_date,
                        track.isrc,
                        metadata,
                    )
                )
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
        connector_tracks = await self.connector_repo.bulk_upsert(
            connector_tracks_data,
            lookup_keys=["connector_name", "connector_track_identifier"],
            return_models=True,
        )

        # Create connector ID to DB ID mapping
        connector_id_map: dict[tuple[str, str], int] = {
            (ct.connector_name, ct.connector_track_identifier): ct.id
            for ct in connector_tracks
            if ct.id is not None
        }

        # Prepare mapping data
        for (
            track,
            connector,
            connector_id,
            match_method,
            confidence,
            _,
            confidence_evidence,
        ) in mappings:
            if track.id is None or (connector, connector_id) not in connector_id_map:
                continue

            connector_track_id = connector_id_map[connector, connector_id]
            mapping_data.append({
                "track_id": track.id,
                "connector_track_id": connector_track_id,
                "connector_name": connector,
                "match_method": match_method,
                "confidence": confidence,
                "confidence_evidence": confidence_evidence,
                "is_primary": False,  # Don't set primary here, handle it separately
            })

        # Bulk upsert mappings
        if mapping_data:
            _ = await self.mapping_repo.bulk_upsert(
                mapping_data,
                lookup_keys=["connector_track_id", "connector_name"],
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
        metadata: dict[str, Any] | None = None,
        confidence_evidence: dict[str, Any] | None = None,
        auto_set_primary: bool = True,
    ) -> Track:
        """Link an existing internal track to an external service ID."""
        if track.id is None:
            raise ValueError("Cannot map track with no ID")

        # Ensure the track exists (raises NotFoundError if missing)
        await self.track_repo.get_by_id(track.id)

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

        result_track = results[0] if results else track

        # Auto-set primary mapping if requested and track has an ID
        if auto_set_primary and result_track.id:
            _ = await self.ensure_primary_mapping(
                result_track.id, connector, connector_id
            )

        # Sync denormalized ID columns on canonical track so fast-path lookups work
        if result_track.id:
            await self._sync_denormalized_id(result_track.id, connector, connector_id)

        return result_track

    @db_operation("ingest_external_tracks_bulk")
    async def ingest_external_tracks_bulk(
        self,
        connector: str,
        tracks: list[ConnectorTrack],
    ) -> list[Track]:
        """Import tracks from external music services into the internal database.

        Creates new tracks or updates existing ones, stores service-specific metadata,
        and establishes mappings. Optimized for processing large batches like playlists.

        Args:
            connector: Service name (e.g., "spotify", "lastfm").
            tracks: External track data to import.

        Returns:
            List of internal Track objects created or updated.
        """
        if not tracks:
            return []

        # 1. Bulk upsert all connector tracks
        connector_track_data: list[dict[str, Any]] = [
            self._build_connector_track_dict(
                connector,
                track.connector_track_identifier,
                track.title,
                track.artists,
                track.album,
                track.duration_ms,
                track.release_date,
                track.isrc,
                track.raw_metadata,
            )
            for track in tracks
        ]

        connector_tracks = await self.connector_repo.bulk_upsert(
            connector_track_data,
            lookup_keys=["connector_name", "connector_track_identifier"],
        )

        # 2. Create a lookup dict for connector tracks
        connector_track_lookup = {
            ct.connector_track_identifier: ct for ct in connector_tracks
        }

        # 3. Bulk-fetch all existing mappings for these connector tracks (N queries → 1)
        all_ct_db_ids = [
            ct.id for ct in connector_track_lookup.values() if ct.id is not None
        ]
        existing_mappings_list = await self.mapping_repo.find_by([
            self.mapping_repo.model_class.connector_track_id.in_(all_ct_db_ids),
        ])
        existing_mapping_by_ct_id: dict[int, TrackMapping] = {
            m.connector_track_id: m for m in existing_mappings_list
        }

        # 4. Create or find domain tracks
        domain_tracks: list[Track] = []
        track_mappings_data: list[dict[str, Any]] = []
        metrics_data: list[tuple[int | None, dict[str, Any]]] = []

        # Group tracks by connector_track_identifier to handle duplicates
        tracks_by_identifier: dict[str, list[ConnectorTrack]] = {}
        for track in tracks:
            identifier = track.connector_track_identifier
            if identifier not in tracks_by_identifier:
                tracks_by_identifier[identifier] = []
            tracks_by_identifier[identifier].append(track)

        # Process each unique connector track identifier
        for connector_track_identifier, track_group in tracks_by_identifier.items():
            # Use the first track from the group for processing (they're identical except position)
            representative_track = track_group[0]

            # Get the connector track ID from the lookup
            ct_entry = connector_track_lookup[connector_track_identifier]
            connector_track_id = ct_entry.id
            if connector_track_id is None:
                continue

            # Check pre-fetched mappings instead of per-item query
            mapping = existing_mapping_by_ct_id.get(connector_track_id)

            if mapping:
                # Track exists, retrieve it
                domain_track = await self.track_repo.get_by_id(mapping.track_id)
                logger.debug(
                    f"Found existing track {mapping.track_id} for "
                    + f"{connector}:{connector_track_identifier}"
                )

                # Add the domain track for each occurrence in the playlist
                domain_tracks.extend(domain_track for _ in track_group)

                # Update mapping confidence if needed
                if (
                    mapping.confidence < BusinessLimits.FULL_CONFIDENCE_SCORE
                    and mapping.id is not None
                ):
                    _ = await self.mapping_repo.update(
                        mapping.id,
                        {"confidence": BusinessLimits.FULL_CONFIDENCE_SCORE},
                    )
            else:
                # Create new track using the representative track
                artists = (
                    [Artist(name=a.name) for a in representative_track.artists]
                    if representative_track.artists
                    else []
                )
                track_obj = Track(
                    title=representative_track.title,
                    artists=artists,
                    album=representative_track.album,
                    duration_ms=representative_track.duration_ms,
                    release_date=representative_track.release_date,
                    isrc=representative_track.isrc,
                )

                # Add connector ID and metadata
                track_obj = track_obj.with_connector_track_id(
                    connector, representative_track.connector_track_identifier
                )
                track_obj = track_obj.with_connector_metadata(
                    connector, representative_track.raw_metadata or {}
                )

                # Save track and get ID
                domain_track = await self.track_repo.save_track(track_obj)

                # Add the domain track for each occurrence in the playlist
                domain_tracks.extend(domain_track for _ in track_group)

                # Prepare mapping data for bulk insert (only once per unique connector track)
                if domain_track.id is not None:
                    track_mappings_data.append({
                        "track_id": domain_track.id,
                        "connector_track_id": connector_track_id,
                        "connector_name": connector,
                        "match_method": "direct",
                        "confidence": 100,
                        "is_primary": False,  # Will be set properly via ensure_primary_mapping post-processing
                    })

                    # Prepare metrics data (only once per unique connector track)
                    if representative_track.raw_metadata:
                        metrics_data.append((
                            domain_track.id,
                            representative_track.raw_metadata,
                        ))

        # 5. Bulk create mappings if any
        if track_mappings_data:
            _ = await self.mapping_repo.bulk_upsert(
                track_mappings_data,
                lookup_keys=["connector_track_id", "connector_name"],
                return_models=False,
            )

            # 6. Set primary mappings in bulk (one per track-connector pair)
            primaries_set: dict[int, str] = {}
            for track in domain_tracks:
                if track.id is not None and track.id not in primaries_set:
                    cid = track.connector_track_identifiers.get(connector)
                    if cid:
                        primaries_set[track.id] = cid

            primaries = [(tid, connector, cid) for tid, cid in primaries_set.items()]
            if primaries:
                _ = await self.batch_ensure_primary_mappings(primaries)

        # 7. Create secondary mappings for Spotify relinked tracks
        if connector == "spotify":
            await self._create_relink_secondary_mappings(
                connector, tracks_by_identifier, domain_tracks
            )

        return domain_tracks

    async def _create_relink_secondary_mappings(
        self,
        connector: str,
        tracks_by_identifier: dict[str, list[ConnectorTrack]],
        domain_tracks: list[Track],
    ) -> None:
        """Create secondary connector mappings for Spotify relinked tracks.

        When a Spotify track has been relinked (linked_from_id in raw_metadata),
        creates a secondary (non-primary) connector track + mapping for the
        original ID so future lookups under either ID find the canonical track.
        """
        if connector != "spotify":
            return

        # Build identifier → domain track lookup from domain_tracks
        id_to_domain: dict[str, Track] = {}
        for track in domain_tracks:
            if track.id is not None:
                cid = track.connector_track_identifiers.get(connector)
                if cid and cid not in id_to_domain:
                    id_to_domain[cid] = track

        # Collect relinked pairs: (domain_track, original_id)
        relink_pairs: list[tuple[Track, str, ConnectorTrack]] = []
        for identifier, ct_group in tracks_by_identifier.items():
            representative = ct_group[0]
            alt = representative.raw_metadata.get("linked_from_id")
            if alt and alt != identifier and identifier in id_to_domain:
                relink_pairs.append((id_to_domain[identifier], alt, representative))

        if not relink_pairs:
            return

        # Bulk upsert secondary connector tracks
        secondary_ct_data: list[dict[str, Any]] = [
            self._build_connector_track_dict(
                connector,
                alt_id,
                ct.title,
                ct.artists,
                ct.album,
                ct.duration_ms,
                ct.release_date,
                ct.isrc,
                ct.raw_metadata,
            )
            for _, alt_id, ct in relink_pairs
        ]

        secondary_cts = await self.connector_repo.bulk_upsert(
            secondary_ct_data,
            lookup_keys=["connector_name", "connector_track_identifier"],
            return_models=True,
        )

        # Build alt_id → secondary connector track DB ID
        secondary_ct_id_map: dict[str, int] = {
            ct.connector_track_identifier: ct.id
            for ct in secondary_cts
            if ct.id is not None
        }

        # Bulk upsert secondary mappings (non-primary)
        secondary_mapping_data: list[dict[str, Any]] = []
        for domain_track, alt_id, _ in relink_pairs:
            ct_db_id = secondary_ct_id_map.get(alt_id)
            if ct_db_id is not None and domain_track.id is not None:
                secondary_mapping_data.append({
                    "track_id": domain_track.id,
                    "connector_track_id": ct_db_id,
                    "connector_name": connector,
                    "match_method": "spotify_relink",
                    "confidence": 100,
                    "is_primary": False,
                })

        if secondary_mapping_data:
            _ = await self.mapping_repo.bulk_upsert(
                secondary_mapping_data,
                lookup_keys=["connector_track_id", "connector_name"],
                return_models=False,
            )

            logger.debug(
                f"Created {len(secondary_mapping_data)} secondary relink mappings",
                connector=connector,
            )

    async def _sync_denormalized_id(
        self, track_id: int, connector: str, connector_id: str
    ) -> None:
        """Sync denormalized ID column on DBTrack after a mapping is created.

        When a track gains a new Spotify or MusicBrainz mapping, the fast-path
        lookup columns (spotify_id, mbid) on DBTrack must be updated so that
        save_track() deduplication and _TRACK_ID_TYPES lookups find the track.
        """
        column_map = {"spotify": "spotify_id", "musicbrainz": "mbid"}
        column_name = column_map.get(connector)
        if column_name:
            await self.session.execute(
                update(DBTrack)
                .where(DBTrack.id == track_id)
                .values(**{column_name: connector_id})
            )

    @db_operation("get_connector_mappings")
    async def get_connector_mappings(
        self,
        track_ids: list[int],
        connector: str | None = None,
    ) -> dict[int, dict[str, str]]:
        """Get external service IDs for internal tracks.

        Args:
            track_ids: Internal track IDs to lookup.
            connector: Optional service filter (e.g., "spotify").

        Returns:
            Dict mapping track_id to {service_name: external_id}.
        """
        if not track_ids:
            return {}

        # Build efficient join between mappings and connector tracks (primary only)
        stmt = (
            select(
                self.mapping_repo.model_class.track_id,
                self.connector_repo.model_class.connector_name,
                self.connector_repo.model_class.connector_track_identifier,
            )
            .join(
                self.connector_repo.model_class,
                self.mapping_repo.model_class.connector_track_id
                == self.connector_repo.model_class.id,
            )
            .where(
                self.mapping_repo.model_class.track_id.in_(track_ids),
                self.mapping_repo.model_class.is_primary.is_(True),
            )
        )

        if connector:
            stmt = stmt.where(
                self.connector_repo.model_class.connector_name == connector
            )

        # Execute and build response
        result = await self.session.execute(stmt)

        mappings_dict: dict[int, dict[str, str]] = {}
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
        """Get service-specific metadata for tracks.

        Args:
            track_ids: Internal track IDs to lookup.
            connector: Service name (e.g., "spotify").
            metadata_field: Optional specific field to extract.

        Returns:
            Dict mapping track_id to metadata or specific field value.
        """
        if not track_ids:
            return {}

        # Build efficient join query (primary mappings only)
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
                self.mapping_repo.model_class.is_primary.is_(True),
                self.connector_repo.model_class.connector_name == connector,
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

    @db_operation("get_metadata_timestamps")
    async def get_metadata_timestamps(
        self, track_ids: list[int], connector: str
    ) -> dict[int, datetime]:
        """Get when metadata was last collected from an external service.

        Args:
            track_ids: Internal track IDs to check.
            connector: Service name to filter by.

        Returns:
            Dict mapping track_id to most recent collection timestamp.
        """
        if not track_ids:
            return {}

        from sqlalchemy import func

        from src.infrastructure.persistence.database.db_models import DBTrackMetric

        stmt = (
            select(
                DBTrackMetric.track_id,
                func.max(DBTrackMetric.collected_at).label("latest_collected_at"),
            )
            .where(
                DBTrackMetric.track_id.in_(track_ids),
                DBTrackMetric.connector_name == connector,
            )
            .group_by(DBTrackMetric.track_id)
        )

        result = await self.session.execute(stmt)
        return {
            track_id: (
                collected_at.replace(tzinfo=UTC)
                if collected_at.tzinfo is None
                else collected_at
            )
            for track_id, collected_at in result.fetchall()
            if collected_at
        }

    @db_operation("ensure_primary_mapping")
    async def ensure_primary_mapping(
        self, track_id: int, connector: str, connector_id: str
    ) -> bool:
        """Ensure a mapping exists and is set as primary for the given track-connector pair.

        This method is used when we know a specific external ID should be the primary
        mapping (e.g., when Spotify returns a track ID in an API response).

        Args:
            track_id: Internal canonical track ID.
            connector: Service name (e.g., "spotify").
            connector_id: External track ID that should be primary.

        Returns:
            True if primary mapping was successfully set.
        """
        # First find the connector track
        connector_track = await self.connector_repo.find_one_by({
            "connector_name": connector,
            "connector_track_identifier": connector_id,
        })

        if not connector_track or connector_track.id is None:
            logger.warning(f"Connector track not found: {connector}:{connector_id}")
            return False

        return await self.set_primary_mapping(track_id, connector, connector_track.id)

    @db_operation("set_primary_mapping")
    async def set_primary_mapping(
        self, track_id: int, connector_name: str, connector_track_id: int
    ) -> bool:
        """Mark one external track as the primary mapping for a service.

        Handles cases like Spotify track relinking where multiple external tracks
        map to the same internal track. Ensures only one mapping per service is primary.

        Args:
            track_id: Internal canonical track ID.
            connector_name: Service name (e.g., "spotify").
            connector_track_id: Database ID of the external track record.

        Returns:
            True if primary mapping was successfully updated.
        """
        log = logger.bind(
            track_id=track_id,
            connector=connector_name,
            connector_track_id=connector_track_id,
        )

        try:
            # Step 1: Reset all primaries for this track-connector pair
            _ = await self.session.execute(
                update(DBTrackMapping)
                .where(
                    DBTrackMapping.track_id == track_id,
                    DBTrackMapping.connector_name == connector_name,
                )
                .values(is_primary=False)
            )

            # Step 2: Set the specified mapping as primary
            result = cast(
                CursorResult[Any],
                await self.session.execute(
                    update(DBTrackMapping)
                    .where(
                        DBTrackMapping.track_id == track_id,
                        DBTrackMapping.connector_track_id == connector_track_id,
                    )
                    .values(is_primary=True)
                ),
            )

            success = result.rowcount > 0
            if success:
                log.debug("Set primary mapping")
            else:
                log.warning("Failed to set primary mapping - no matching record found")

        except Exception:
            log.opt(exception=True).error("Error setting primary mapping")
            return False
        else:
            return success

    @db_operation("batch_ensure_primary_mappings")
    async def batch_ensure_primary_mappings(
        self,
        primaries: list[tuple[int, str, str]],
    ) -> int:
        """Set primary mappings for multiple track-connector pairs in bulk.

        Replaces per-track ensure_primary_mapping() loops with fewer queries.
        Each tuple is (track_id, connector_name, connector_track_identifier).

        Args:
            primaries: List of (track_id, connector_name, connector_track_identifier).

        Returns:
            Number of mappings successfully promoted to primary.
        """
        if not primaries:
            return 0

        # Single bulk query: find all connector track DB IDs
        connector_ids = list({cid for _, _, cid in primaries})
        connectors = list({cn for _, cn, _ in primaries})

        ct_records = await self.connector_repo.find_by([
            self.connector_repo.model_class.connector_name.in_(connectors),
            self.connector_repo.model_class.connector_track_identifier.in_(
                connector_ids
            ),
        ])
        ct_id_map: dict[tuple[str, str], int] = {
            (ct.connector_name, ct.connector_track_identifier): ct.id
            for ct in ct_records
            if ct.id is not None
        }

        # Step 1: Bulk reset all primaries for affected track-connector pairs
        track_ids = [tid for tid, _, _ in primaries]
        for connector_name in connectors:
            await self.session.execute(
                update(DBTrackMapping)
                .where(
                    DBTrackMapping.track_id.in_(track_ids),
                    DBTrackMapping.connector_name == connector_name,
                )
                .values(is_primary=False)
            )

        # Step 2: Set new primaries (per-row — SQLite lacks multi-column IN for UPDATE)
        promoted = 0
        for track_id, connector_name, connector_id in primaries:
            ct_db_id = ct_id_map.get((connector_name, connector_id))
            if ct_db_id is None:
                continue
            result = cast(
                CursorResult[Any],
                await self.session.execute(
                    update(DBTrackMapping)
                    .where(
                        DBTrackMapping.track_id == track_id,
                        DBTrackMapping.connector_track_id == ct_db_id,
                    )
                    .values(is_primary=True)
                ),
            )
            if result.rowcount > 0:
                promoted += 1

        return promoted
