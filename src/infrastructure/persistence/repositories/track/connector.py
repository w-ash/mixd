"""Manages track connections between internal database and external music services.

Handles track ingestion from Spotify, Last.fm, and other music platforms, maps external
tracks to canonical internal tracks, and stores service-specific metadata and IDs.
"""

# Lazy import cycle (mapper.py → this module for set_primary_mapping) handled by TYPE_CHECKING guard

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import cast, overload, override
from uuid import UUID

from attrs import define
from sqlalchemy import (
    ColumnElement,
    Integer,
    Numeric,
    case,
    delete,
    func,
    select,
    text,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.stdlib import BoundLogger

from src.config import get_logger
from src.config.constants import DenormalizedTrackColumns, MappingOrigin, MatchMethod
from src.domain.entities import Artist, ConnectorTrack, Track, TrackMapping
from src.domain.entities.match_review import MatchReview
from src.domain.entities.shared import JsonDict, JsonValue
from src.domain.exceptions import NotFoundError
from src.domain.matching.isrc_validation import (
    assess_isrc_match_reliability,
    compute_duration_diff_ms,
)
from src.domain.matching.types import RawProviderMatch
from src.domain.repositories.connector import (
    ConnectorMappingSpec,
    FullMappingInfo,
    MatchMethodStatRow,
    PrimaryMappingDetail,
)
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBMatchReview,
    DBTrack,
    DBTrackMapping,
)
from src.infrastructure.persistence.repositories.base_repo import (
    BaseRepository,
    rows_affected,
)
from src.infrastructure.persistence.repositories.mappers import BaseModelMapper
from src.infrastructure.persistence.repositories.repo_decorator import db_operation
from src.infrastructure.persistence.repositories.track.core import TrackRepository
from src.infrastructure.persistence.repositories.track.mapper import (
    extract_db_artist_names,
)

logger = get_logger(__name__)

# Confidence-band thresholds for the matching-health distribution (mirror SQL
# pack Q1): reject <50, review 50-84, accept 85-99, certain =100.
_BAND_REVIEW_MIN = 50
_BAND_REVIEW_MAX = 84
_BAND_ACCEPT_MIN = 85
_BAND_ACCEPT_MAX = 99
_CONFIDENCE_CERTAIN = 100


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
            artists=[Artist(name=n) for n in extract_db_artist_names(db_model.artists)],
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

        ``confidence_evidence`` widens from ``JsonDict`` (DB column) to
        ``dict[str, object]`` (entity field) — JsonValue ⊂ object is safe;
        the entity is frozen so no mutation risk. Cast avoids a copy.
        """
        evidence = cast("dict[str, object] | None", db_model.confidence_evidence)
        return TrackMapping(
            id=db_model.id,
            user_id=db_model.user_id,
            track_id=db_model.track_id,
            connector_track_id=db_model.connector_track_id,
            connector_name=db_model.connector_name,
            match_method=db_model.match_method,
            confidence=db_model.confidence,
            confidence_evidence=evidence,
            origin=db_model.origin,
            is_primary=db_model.is_primary,
            last_seen_at=db_model.last_seen_at,
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
            user_id=domain_model.user_id,
            track_id=domain_model.track_id,
            connector_track_id=domain_model.connector_track_id,
            connector_name=domain_model.connector_name,
            match_method=domain_model.match_method,
            confidence=domain_model.confidence,
            confidence_evidence=domain_model.confidence_evidence,
            origin=domain_model.origin,
            is_primary=domain_model.is_primary,
            last_seen_at=domain_model.last_seen_at,
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
    connector_repo: ConnectorTrackRepository
    mapping_repo: TrackMappingRepository
    track_repo: TrackRepository

    def __init__(self, session: AsyncSession) -> None:
        """Initialize with database session and dependent repositories."""
        self.session = session
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
        raw_metadata: Mapping[str, object] | None,
    ) -> dict[str, object]:
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

    @db_operation("ensure_connector_tracks")
    async def ensure_connector_tracks(
        self,
        connector_name: str,
        tracks_data: Sequence[Mapping[str, object]],
    ) -> dict[tuple[str, str], UUID]:
        """Ensure connector_tracks rows exist, returning a (name, external_id) -> UUID map.

        Builds persistence-format dicts from application-layer data and bulk-upserts.
        """
        if not tracks_data:
            return {}

        now = datetime.now(UTC)
        upsert_data: list[dict[str, object]] = [
            {
                "connector_name": connector_name,
                "connector_track_identifier": td["connector_id"],
                "title": td.get("title", ""),
                "artists": {"names": td.get("artists", [])},
                "album": td.get("album"),
                "duration_ms": td.get("duration_ms"),
                "release_date": td.get("release_date"),
                "isrc": td.get("isrc"),
                "raw_metadata": td.get("raw_metadata", {}),
                "last_updated": now,
            }
            for td in tracks_data
        ]

        connector_tracks = await self.connector_repo.bulk_upsert(
            upsert_data,
            lookup_keys=["connector_name", "connector_track_identifier"],
            return_models=True,
        )

        return {
            (ct.connector_name, ct.connector_track_identifier): ct.id
            for ct in connector_tracks
        }

    @db_operation("get_full_mappings_for_track")
    async def get_full_mappings_for_track(
        self, track_id: UUID, *, user_id: str
    ) -> list[FullMappingInfo]:
        """Get all mappings for a track with joined connector track metadata."""
        stmt = (
            select(
                DBTrackMapping.id,
                DBTrackMapping.connector_name,
                DBConnectorTrack.connector_track_identifier,
                DBTrackMapping.match_method,
                DBTrackMapping.confidence,
                DBTrackMapping.origin,
                DBTrackMapping.is_primary,
                DBConnectorTrack.title,
                DBConnectorTrack.artists,
            )
            .join(
                DBConnectorTrack,
                DBTrackMapping.connector_track_id == DBConnectorTrack.id,
            )
            .where(DBTrackMapping.track_id == track_id)
            .where(DBTrackMapping.user_id == user_id)
            .order_by(
                DBTrackMapping.is_primary.desc(), DBTrackMapping.confidence.desc()
            )
        )
        result = await self.session.execute(stmt)
        return [
            FullMappingInfo(
                mapping_id=mapping_id,
                connector_name=connector_name,
                connector_track_id=connector_track_id,
                match_method=match_method,
                confidence=confidence,
                origin=origin,
                is_primary=is_primary,
                connector_track_title=title,
                connector_track_artists=extract_db_artist_names(artists),
            )
            for (
                mapping_id,
                connector_name,
                connector_track_id,
                match_method,
                confidence,
                origin,
                is_primary,
                title,
                artists,
            ) in result.tuples()
        ]

    @db_operation("find_tracks_by_connectors")
    async def find_tracks_by_connectors(
        self, connections: list[tuple[str, str]], *, user_id: str
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
                ct.id: ct.connector_track_identifier for ct in connector_tracks
            }
            ct_ids = [ct.id for ct in connector_tracks]

            mappings = await self.mapping_repo.find_by([
                self.mapping_repo.model_class.connector_track_id.in_(ct_ids),
                self.mapping_repo.model_class.user_id == user_id,
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

    @db_operation("map_tracks_to_connectors")
    async def map_tracks_to_connectors(
        self,
        mappings: list[ConnectorMappingSpec],
    ) -> list[Track]:
        """Link existing internal tracks to external service IDs with confidence scores.

        Pipeline: build connector-track rows → bulk upsert → build mapping rows →
        drop rows that would overwrite manual overrides → bulk upsert mappings.

        Args:
            mappings: Mapping specs pairing each track with its connector, external
                id, match method, confidence, and optional metadata/evidence.

        Returns:
            List of Track objects updated with external service connections.
        """
        if not mappings:
            return []

        updated_tracks = self._build_updated_tracks(mappings)
        connector_id_map = await self._upsert_connector_tracks(
            self._build_connector_track_rows(mappings)
        )
        mapping_rows = await self._filter_manual_overrides(
            self._build_mapping_rows(mappings, connector_id_map)
        )

        if mapping_rows:
            _ = await self.mapping_repo.bulk_upsert(
                mapping_rows,
                lookup_keys=["user_id", "connector_track_id", "connector_name"],
                return_models=False,
            )

        # Note: metrics extraction lives in the application layer
        # (MetricsApplicationService); this repository maps track identity only.
        return updated_tracks

    def _build_connector_track_rows(
        self, mappings: list[ConnectorMappingSpec]
    ) -> list[dict[str, object]]:
        """Build deduplicated connector-track upsert rows (one per external id)."""
        rows: list[dict[str, object]] = []
        seen_keys: set[tuple[str, str]] = set()
        for spec in mappings:
            key = (spec.connector, spec.connector_id)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            rows.append(
                self._build_connector_track_dict(
                    spec.connector,
                    spec.connector_id,
                    spec.track.title,
                    spec.track.artists,
                    spec.track.album,
                    spec.track.duration_ms,
                    spec.track.release_date,
                    spec.track.isrc,
                    spec.metadata,
                )
            )
        return rows

    @staticmethod
    def _build_updated_tracks(mappings: list[ConnectorMappingSpec]) -> list[Track]:
        """Build the returned Track objects with connector id + metadata applied.

        Application-layer metadata is ``dict[str, object]`` (mixed types) but
        Track stores ``Mapping[str, JsonValue]`` — cast at the boundary; the
        values are JSON-serialisable at runtime, the type system can't see it.
        """
        updated_tracks: list[Track] = []
        for spec in mappings:
            updated_track = spec.track.with_connector_track_id(
                spec.connector, spec.connector_id
            )
            if spec.metadata:
                updated_track = updated_track.with_connector_metadata(
                    spec.connector, cast("JsonDict", spec.metadata)
                )
            updated_tracks.append(updated_track)
        return updated_tracks

    async def _upsert_connector_tracks(
        self, rows: list[dict[str, object]]
    ) -> dict[tuple[str, str], UUID]:
        """Bulk upsert connector-track rows, returning an (name, external_id) -> id map."""
        connector_tracks = await self.connector_repo.bulk_upsert(
            rows,
            lookup_keys=["connector_name", "connector_track_identifier"],
            return_models=True,
        )
        return {
            (ct.connector_name, ct.connector_track_identifier): ct.id
            for ct in connector_tracks
        }

    @staticmethod
    def _build_mapping_rows(
        mappings: list[ConnectorMappingSpec],
        connector_id_map: dict[tuple[str, str], UUID],
    ) -> list[dict[str, object]]:
        """Build track_mapping upsert rows for specs whose connector track exists."""
        rows: list[dict[str, object]] = []
        for spec in mappings:
            key = (spec.connector, spec.connector_id)
            if key not in connector_id_map:
                continue
            rows.append({
                "user_id": spec.track.user_id,
                "track_id": spec.track.id,
                "connector_track_id": connector_id_map[key],
                "connector_name": spec.connector,
                "match_method": spec.match_method,
                "confidence": spec.confidence,
                "confidence_evidence": spec.confidence_evidence,
                "is_primary": False,  # Don't set primary here, handle it separately
            })
        return rows

    async def _filter_manual_overrides(
        self, mapping_rows: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        """Drop mapping rows whose connector track has a manual-override mapping.

        MANUAL_OVERRIDE rows are user-pinned identity decisions — an automatic
        bulk map must never clobber them.
        """
        if not mapping_rows:
            return mapping_rows

        ct_ids_in_batch = [d["connector_track_id"] for d in mapping_rows]
        result = await self.session.execute(
            select(DBTrackMapping.connector_track_id).where(
                DBTrackMapping.connector_track_id.in_(ct_ids_in_batch),
                DBTrackMapping.origin == MappingOrigin.MANUAL_OVERRIDE,
            )
        )
        manual_override_ct_ids = {row[0] for row in result.fetchall()}
        if not manual_override_ct_ids:
            return mapping_rows

        return [
            d
            for d in mapping_rows
            if d["connector_track_id"] not in manual_override_ct_ids
        ]

    @db_operation("map_track_to_connector")
    async def map_track_to_connector(
        self,
        track: Track,
        connector: str,
        connector_id: str,
        match_method: str,
        confidence: int,
        metadata: dict[str, object] | None = None,
        confidence_evidence: dict[str, object] | None = None,
        auto_set_primary: bool = True,
        origin: str = "automatic",
    ) -> Track:
        """Link an existing internal track to an external service ID."""

        # Ensure the track exists (raises NotFoundError if missing)
        await self.track_repo.get_by_id(track.id)

        results = await self.map_tracks_to_connectors([
            ConnectorMappingSpec(
                track=track,
                connector=connector,
                connector_id=connector_id,
                match_method=match_method,
                confidence=confidence,
                metadata=metadata,
                confidence_evidence=confidence_evidence,
            )
        ])

        result_track = results[0] if results else track

        # Override origin if not the default (e.g., manual_override for orphan tracks)
        if origin != "automatic" and result_track.id:
            await self.session.execute(
                update(DBTrackMapping)
                .where(
                    DBTrackMapping.track_id == result_track.id,
                    DBTrackMapping.connector_name == connector,
                )
                .values(origin=origin)
            )

        # Auto-set primary mapping if requested and track has an ID. The
        # denormalized fast-path column syncs ONLY when the mapping actually
        # became primary — an unconditional sync let the redirect flow's
        # stale-secondary write (auto_set_primary=False, written last) leave
        # the dead ID in the column (v0.8.18 FM4d).
        if auto_set_primary and result_track.id:
            primary_set = await self.ensure_primary_mapping(
                result_track.id, connector, connector_id
            )
            if primary_set:
                await self._sync_denormalized_id(
                    result_track.id, connector, connector_id
                )

        return result_track

    @db_operation("ingest_external_tracks_bulk")
    async def ingest_external_tracks_bulk(
        self,
        connector: str,
        tracks: list[ConnectorTrack],
        *,
        user_id: str,
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

        # 1. Group tracks by identifier upfront to handle duplicates in a single
        # batch (Spotify can return the same track across pagination boundaries),
        # then bulk upsert one connector track per unique identifier.
        tracks_by_identifier = self._group_tracks_by_identifier(tracks)
        connector_track_lookup = await self._upsert_connector_tracks_from_groups(
            connector, tracks_by_identifier
        )

        # 2. Bulk-fetch all existing mappings for these connector tracks (N → 1).
        existing_mapping_by_ct_id = await self._fetch_existing_mappings_by_ct_id(
            [ct.id for ct in connector_track_lookup.values()], user_id
        )

        # 2.5. Pre-collect ISRC owners for groups that will create new tracks,
        # so suspect collisions route to review instead of merging (FM2a).
        new_isrcs = {
            group[0].isrc
            for identifier, group in tracks_by_identifier.items()
            if group[0].isrc
            and connector_track_lookup[identifier].id not in existing_mapping_by_ct_id
        }
        isrc_owners: dict[str, Track] = (
            await self.track_repo.find_tracks_by_isrcs(
                sorted(new_isrcs), user_id=user_id
            )
            if new_isrcs
            else {}
        )

        # 3. Create or find a domain track per unique identifier, collecting the
        # mapping rows that new tracks need.
        domain_tracks: list[Track] = []
        track_mappings_data: list[dict[str, object]] = []
        for identifier, track_group in tracks_by_identifier.items():
            domain_track, mapping_row = await self._ingest_one_group(
                connector,
                identifier,
                track_group,
                connector_track_lookup,
                existing_mapping_by_ct_id,
                isrc_owners,
                user_id=user_id,
            )
            # Add the domain track for each occurrence in the playlist
            domain_tracks.extend(domain_track for _ in track_group)
            if mapping_row is not None:
                track_mappings_data.append(mapping_row)

        # 3.5. Re-encounter is a freshness signal, not evidence: stamp
        # last_seen_at on every mapping this batch re-encountered — including
        # manual overrides (freshness is origin-independent) — instead of
        # overwriting confidence (FM1a).
        if existing_mapping_by_ct_id:
            await self._touch_last_seen([
                m.id for m in existing_mapping_by_ct_id.values()
            ])

        # 4. Bulk create mappings + set primaries for the newly created tracks.
        await self._create_mappings_and_set_primaries(
            connector, domain_tracks, track_mappings_data
        )

        return domain_tracks

    @staticmethod
    def _group_tracks_by_identifier(
        tracks: list[ConnectorTrack],
    ) -> dict[str, list[ConnectorTrack]]:
        """Group connector tracks by external identifier, preserving order."""
        groups: dict[str, list[ConnectorTrack]] = {}
        for track in tracks:
            groups.setdefault(track.connector_track_identifier, []).append(track)
        return groups

    async def _upsert_connector_tracks_from_groups(
        self, connector: str, groups: dict[str, list[ConnectorTrack]]
    ) -> dict[str, ConnectorTrack]:
        """Bulk upsert one connector track per group (last occurrence wins).

        Returns a lookup keyed by external identifier.
        """
        connector_track_data: list[dict[str, object]] = [
            self._build_connector_track_dict(
                connector,
                identifier,
                group[-1].title,
                group[-1].artists,
                group[-1].album,
                group[-1].duration_ms,
                group[-1].release_date,
                group[-1].isrc,
                group[-1].raw_metadata,
            )
            for identifier, group in groups.items()
        ]
        connector_tracks = await self.connector_repo.bulk_upsert(
            connector_track_data,
            lookup_keys=["connector_name", "connector_track_identifier"],
        )
        return {ct.connector_track_identifier: ct for ct in connector_tracks}

    async def _fetch_existing_mappings_by_ct_id(
        self, connector_track_ids: list[UUID], user_id: str
    ) -> dict[UUID, TrackMapping]:
        """Bulk-fetch existing mappings for connector tracks, keyed by connector_track_id."""
        existing = await self.mapping_repo.find_by([
            self.mapping_repo.model_class.connector_track_id.in_(connector_track_ids),
            self.mapping_repo.model_class.user_id == user_id,
        ])
        return {m.connector_track_id: m for m in existing}

    async def _ingest_one_group(
        self,
        connector: str,
        identifier: str,
        track_group: list[ConnectorTrack],
        connector_track_lookup: dict[str, ConnectorTrack],
        existing_mapping_by_ct_id: dict[UUID, TrackMapping],
        isrc_owners: dict[str, Track],
        *,
        user_id: str,
    ) -> tuple[Track, dict[str, object] | None]:
        """Resolve one unique connector identifier to a domain track.

        Returns the domain track and, when a new track was created, the mapping
        row it needs (``None`` when an existing mapping was reused).
        """
        # Tracks in a group are identical except playlist position
        representative_track = track_group[0]
        connector_track_id = connector_track_lookup[identifier].id
        mapping = existing_mapping_by_ct_id.get(connector_track_id)

        if mapping:
            domain_track = await self.track_repo.get_by_id(mapping.track_id)
            logger.debug(
                f"Found existing track {mapping.track_id} for "
                + f"{connector}:{identifier}"
            )
            return domain_track, None

        return await self._create_track_with_mapping_row(
            connector, representative_track, connector_track_id, isrc_owners, user_id
        )

    async def _touch_last_seen(self, mapping_ids: list[UUID]) -> None:
        """Bulk-stamp last_seen_at on re-encountered mappings.

        Replaces the pre-v0.8.18 bump-to-100: re-encountering a connector
        track proves it exists, not that the canonical match was right, so
        confidence is never touched here.
        """
        _ = await self.session.execute(
            update(DBTrackMapping)
            .where(DBTrackMapping.id.in_(mapping_ids))
            .values(last_seen_at=datetime.now(UTC))
        )

    async def _create_track_with_mapping_row(
        self,
        connector: str,
        representative_track: ConnectorTrack,
        connector_track_id: UUID,
        isrc_owners: dict[str, Track],
        user_id: str,
    ) -> tuple[Track, dict[str, object]]:
        """Create a new canonical track from connector data; return it + its mapping row."""
        artists = (
            [Artist(name=a.name) for a in representative_track.artists]
            if representative_track.artists
            else []
        )
        isrc = await self._resolve_ingest_isrc(
            connector, representative_track, isrc_owners, user_id=user_id
        )
        track_obj = Track(
            title=representative_track.title,
            artists=artists,
            album=representative_track.album,
            duration_ms=representative_track.duration_ms,
            release_date=representative_track.release_date,
            isrc=isrc,
            user_id=user_id,
        )
        track_obj = track_obj.with_connector_track_id(
            connector, representative_track.connector_track_identifier
        )
        track_obj = track_obj.with_connector_metadata(
            connector, representative_track.raw_metadata or {}
        )
        domain_track = await self.track_repo.save_track(track_obj)

        mapping_row: dict[str, object] = {
            "user_id": domain_track.user_id,
            "track_id": domain_track.id,
            "connector_track_id": connector_track_id,
            "connector_name": connector,
            "match_method": "direct",
            "confidence": 100,
            "is_primary": False,  # Set via _batch_ensure_primary_mappings post-processing
        }
        return domain_track, mapping_row

    async def _resolve_ingest_isrc(
        self,
        connector: str,
        representative_track: ConnectorTrack,
        isrc_owners: dict[str, Track],
        *,
        user_id: str,
    ) -> str | None:
        """Decide whether an ingested track may claim its ISRC.

        A suspect collision (duration >10s off the owner's) queues an
        ``isrc_suspect`` review against the owner and withholds the ISRC —
        the new canonical is created without it, the owner untouched.
        Review-accept later merges the two (v0.8.18 FM2a routing).
        """
        isrc = representative_track.isrc
        if not isrc:
            return None
        owner = isrc_owners.get(isrc)
        if owner is None:
            return isrc

        duration_diff_ms = compute_duration_diff_ms(
            representative_track.duration_ms, owner.duration_ms
        )
        if not assess_isrc_match_reliability(duration_diff_ms).suspect:
            return isrc

        _ = await self.queue_isrc_collision_review(
            owner,
            connector,
            representative_track.connector_track_identifier,
            {
                "title": representative_track.title,
                "artist": representative_track.artists[0].name
                if representative_track.artists
                else "",
                "artists": [a.name for a in representative_track.artists],
                "duration_ms": representative_track.duration_ms,
                "isrc": isrc,
            },
            user_id=user_id,
        )
        return None

    async def _create_mappings_and_set_primaries(
        self,
        connector: str,
        domain_tracks: list[Track],
        track_mappings_data: list[dict[str, object]],
    ) -> None:
        """Bulk create new mappings, then set one primary per track-connector pair."""
        if not track_mappings_data:
            return

        _ = await self.mapping_repo.bulk_upsert(
            track_mappings_data,
            lookup_keys=["user_id", "connector_track_id", "connector_name"],
            return_models=False,
        )

        primaries_set: dict[UUID, str] = {}
        for track in domain_tracks:
            if track.id not in primaries_set:
                cid = track.connector_track_identifiers.get(connector)
                if cid:
                    primaries_set[track.id] = cid

        primaries = [(tid, connector, cid) for tid, cid in primaries_set.items()]
        if primaries:
            _ = await self._batch_ensure_primary_mappings(primaries)

    async def _sync_denormalized_id(
        self, track_id: UUID, connector: str, connector_id: str
    ) -> None:
        """Sync denormalized ID column on DBTrack after a mapping is created.

        When a track gains a new Spotify or MusicBrainz mapping, the fast-path
        lookup columns (spotify_id, mbid) on DBTrack must be updated so that
        save_track() deduplication and _TRACK_ID_TYPES lookups find the track.
        """
        column_name = DenormalizedTrackColumns.COLUMN_MAP.get(connector)
        if column_name:
            await self.session.execute(
                update(DBTrack)
                .where(DBTrack.id == track_id)
                .values(**{column_name: connector_id})
            )

    async def _clear_denormalized_id(self, track_id: UUID, connector: str) -> None:
        """Clear denormalized ID column on DBTrack when no mappings remain for a connector."""
        column_name = DenormalizedTrackColumns.COLUMN_MAP.get(connector)
        if column_name:
            await self.session.execute(
                update(DBTrack)
                .where(DBTrack.id == track_id)
                .values(**{column_name: None})
            )

    @db_operation("get_mapping_by_id")
    async def get_mapping_by_id(
        self, mapping_id: UUID, *, user_id: str
    ) -> TrackMapping | None:
        """Get a single track mapping by its database ID, scoped to user."""
        result = await self.session.execute(
            select(DBTrackMapping).where(
                DBTrackMapping.id == mapping_id,
                DBTrackMapping.user_id == user_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return await TrackMappingMapper.to_domain(row)

    @db_operation("delete_mapping")
    async def delete_mapping(self, mapping_id: UUID, *, user_id: str) -> TrackMapping:
        """Delete a track mapping and return the pre-deletion entity."""
        result = await self.session.execute(
            select(DBTrackMapping).where(
                DBTrackMapping.id == mapping_id,
                DBTrackMapping.user_id == user_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise NotFoundError(f"Mapping {mapping_id} not found")
        mapping = await TrackMappingMapper.to_domain(row)
        await self.session.execute(
            delete(DBTrackMapping).where(
                DBTrackMapping.id == mapping_id,
                DBTrackMapping.user_id == user_id,
            )
        )
        return mapping

    @db_operation("update_mapping_track")
    async def update_mapping_track(
        self, mapping_id: UUID, new_track_id: UUID, origin: str
    ) -> TrackMapping:
        """Move a mapping to a different canonical track."""
        result = await self.session.execute(
            update(DBTrackMapping)
            .where(DBTrackMapping.id == mapping_id)
            .values(track_id=new_track_id, origin=origin, is_primary=False)
        )
        if rows_affected(result) == 0:
            raise NotFoundError(f"Mapping {mapping_id} not found")
        # Re-fetch updated mapping
        refreshed = await self.session.execute(
            select(DBTrackMapping).where(DBTrackMapping.id == mapping_id)
        )
        row = refreshed.scalar_one()
        return await TrackMappingMapper.to_domain(row)

    @db_operation("count_mappings_for_connector_track")
    async def count_mappings_for_connector_track(self, connector_track_id: UUID) -> int:
        """Count remaining mappings for a given connector track."""
        result = await self.session.execute(
            select(func.count())
            .select_from(DBTrackMapping)
            .where(DBTrackMapping.connector_track_id == connector_track_id)
        )
        return result.scalar_one()

    @db_operation("get_remaining_mappings")
    async def _get_remaining_mappings(
        self, track_id: UUID, connector_name: str
    ) -> list[TrackMapping]:
        """Get all mappings for a (track, connector) pair, ordered by confidence desc.

        The ``id`` ascending secondary key makes the ordering total: on an
        equal-confidence tie ``remaining[0]`` is deterministic, and the mapper's
        display-fallback selection applies the SAME (confidence desc, id asc)
        tiebreak, so the displayed identifier and the promoted primary agree
        (v0.8.18 FM4c: one promotion policy).
        """
        result = await self.session.execute(
            select(DBTrackMapping)
            .where(
                DBTrackMapping.track_id == track_id,
                DBTrackMapping.connector_name == connector_name,
            )
            .order_by(DBTrackMapping.confidence.desc(), DBTrackMapping.id.asc())
        )
        return [
            await TrackMappingMapper.to_domain(row) for row in result.scalars().all()
        ]

    @db_operation("get_connector_track_by_id")
    async def get_connector_track_by_id(
        self, connector_track_id: UUID
    ) -> ConnectorTrack | None:
        """Get a connector track entity by its database ID."""
        result = await self.session.execute(
            select(DBConnectorTrack).where(DBConnectorTrack.id == connector_track_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return await ConnectorTrackMapper.to_domain(row)

    @db_operation("ensure_primary_for_connector")
    async def ensure_primary_for_connector(
        self, track_id: UUID, connector_name: str
    ) -> None:
        """Ensure a primary mapping exists for a (track, connector) pair.

        Promotes the highest-confidence mapping if none is primary,
        or clears the denormalized ID if no mappings remain.
        """
        remaining = await self._get_remaining_mappings(track_id, connector_name)
        if not remaining:
            await self._clear_denormalized_id(track_id, connector_name)
            return
        if any(m.is_primary for m in remaining):
            return
        # Promote highest-confidence mapping
        best = remaining[0]
        await self.set_primary_mapping(
            track_id, connector_name, best.connector_track_id
        )
        # Sync denormalized ID column
        ct_result = await self.session.execute(
            select(DBConnectorTrack.connector_track_identifier).where(
                DBConnectorTrack.id == best.connector_track_id
            )
        )
        external_id = ct_result.scalar_one_or_none()
        if external_id:
            await self._sync_denormalized_id(track_id, connector_name, external_id)

    @db_operation("get_primary_mapping_details")
    async def get_primary_mapping_details(
        self, track_ids: list[UUID], connector: str
    ) -> dict[UUID, PrimaryMappingDetail]:
        """Get primary-mapping provenance (id, confidence, method) per track.

        A primary-only track→connector join widened with the mapping row's
        stored confidence and match method (v0.8.18 FM1b — the fast path
        re-asserts real provenance, not a synthetic constant).
        """
        if not track_ids:
            return {}

        stmt = (
            select(
                self.mapping_repo.model_class.track_id,
                self.connector_repo.model_class.connector_track_identifier,
                self.mapping_repo.model_class.confidence,
                self.mapping_repo.model_class.match_method,
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

        result = await self.session.execute(stmt)
        return {
            track_id: PrimaryMappingDetail(
                connector_id=conn_id,
                confidence=confidence,
                match_method=match_method,
            )
            for track_id, conn_id, confidence, match_method in result.tuples()
        }

    @db_operation("queue_isrc_collision_review")
    async def queue_isrc_collision_review(
        self,
        existing_track: Track,
        connector: str,
        connector_id: str,
        service_data: Mapping[str, JsonValue],
        *,
        user_id: str,
    ) -> bool:
        """Queue a review for a suspect ISRC collision instead of merging.

        The incoming track is evaluated against the ISRC owner with the
        engine's own scoring (``match_method="isrc"`` makes the duration-based
        suspect check run with real durations); routing to review is
        unconditional — a high score must not silently merge what the suspect
        check flagged (v0.8.18 FM2a/FM2c).
        """
        from src.infrastructure.persistence.repositories.match_review import (
            MatchReviewRepository,
        )

        # Ensure the connector_tracks row exists so the review can reference it.
        ct_ids = await self.ensure_connector_tracks(
            connector,
            [
                {
                    "connector_id": connector_id,
                    "title": service_data.get("title", ""),
                    "artists": service_data.get("artists", []),
                    "duration_ms": service_data.get("duration_ms"),
                    "isrc": service_data.get("isrc"),
                }
            ],
        )
        connector_track_uuid = ct_ids[connector, connector_id]

        # Any-status dedupe: re-imports must not resurrect rejected reviews.
        existing_review = await self.session.execute(
            select(DBMatchReview.id).where(
                DBMatchReview.user_id == user_id,
                DBMatchReview.track_id == existing_track.id,
                DBMatchReview.connector_name == connector,
                DBMatchReview.connector_track_id == connector_track_uuid,
            )
        )
        if existing_review.first() is not None:
            return False

        from src.config import create_evaluation_service

        raw_match = RawProviderMatch(
            connector_id=connector_id,
            match_method="isrc",
            service_data=service_data,
        )
        match = create_evaluation_service().evaluate_single_match(
            existing_track, raw_match, connector
        )

        review = MatchReview(
            user_id=user_id,
            track_id=existing_track.id,
            connector_name=connector,
            connector_track_id=connector_track_uuid,
            match_method=MatchMethod.ISRC_SUSPECT,
            confidence=match.confidence,
            match_weight=match.evidence.match_weight if match.evidence else 0.0,
            confidence_evidence=match.evidence_dict,
        )
        _ = await MatchReviewRepository(self.session).create_review(review)
        logger.warning(
            "isrc_collision_deferred",
            track_id=existing_track.id,
            connector=connector,
            connector_id=connector_id,
            isrc=service_data.get("isrc"),
            confidence=match.confidence,
        )
        return True

    @overload
    async def get_connector_metadata(
        self,
        track_ids: list[UUID],
        connector: str,
        metadata_field: None = ...,
    ) -> dict[UUID, JsonDict]: ...

    @overload
    async def get_connector_metadata(
        self,
        track_ids: list[UUID],
        connector: str,
        metadata_field: str,
    ) -> dict[UUID, JsonValue]: ...

    @db_operation("get_connector_metadata")
    async def get_connector_metadata(
        self,
        track_ids: list[UUID],
        connector: str,
        metadata_field: str | None = None,
    ) -> dict[UUID, JsonDict] | dict[UUID, JsonValue]:
        """Get service-specific metadata for tracks.

        When ``metadata_field`` is None, returns the full metadata dict per track.
        When ``metadata_field`` is set, extracts that specific field's value.

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

        # Return either the specific field or all metadata.
        if metadata_field:
            field_result: dict[UUID, JsonValue] = {}
            for track_id, metadata in result.tuples():
                if metadata and metadata_field in metadata:
                    field_result[track_id] = metadata.get(metadata_field)
            return field_result
        full_result: dict[UUID, JsonDict] = {
            track_id: metadata for track_id, metadata in result.tuples() if metadata
        }
        return full_result

    @db_operation("ensure_primary_mapping")
    async def ensure_primary_mapping(
        self, track_id: UUID, connector: str, connector_id: str
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

        if not connector_track:
            logger.warning(f"Connector track not found: {connector}:{connector_id}")
            return False

        return await self.set_primary_mapping(track_id, connector, connector_track.id)

    @db_operation("set_primary_mapping")
    async def set_primary_mapping(
        self, track_id: UUID, connector_name: str, connector_track_id: UUID
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
            success = await self._reset_and_set_primary_mapping(
                track_id, connector_name, connector_track_id, log
            )
        except Exception:
            log.error("Error setting primary mapping", exc_info=True)
            return False
        else:
            return success

    async def _reset_and_set_primary_mapping(
        self,
        track_id: UUID,
        connector_name: str,
        connector_track_id: UUID,
        log: BoundLogger,
    ) -> bool:
        """Reset existing primaries then mark one mapping primary; return success."""
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
        result = await self.session.execute(
            update(DBTrackMapping)
            .where(
                DBTrackMapping.track_id == track_id,
                DBTrackMapping.connector_track_id == connector_track_id,
            )
            .values(is_primary=True)
        )

        success = rows_affected(result) > 0
        if success:
            log.debug("Set primary mapping")
        else:
            log.warning("Failed to set primary mapping - no matching record found")
        return success

    @db_operation("batch_ensure_primary_mappings")
    async def _batch_ensure_primary_mappings(
        self,
        primaries: list[tuple[UUID, str, str]],
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
        ct_id_map: dict[tuple[str, str], UUID] = {
            (ct.connector_name, ct.connector_track_identifier): ct.id
            for ct in ct_records
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

        # Step 2: Set new primaries (per-row — each has a unique (track_id, ct_db_id) pair)
        promoted = 0
        for track_id, connector_name, connector_id in primaries:
            ct_db_id = ct_id_map.get((connector_name, connector_id))
            if ct_db_id is None:
                continue
            result = await self.session.execute(
                update(DBTrackMapping)
                .where(
                    DBTrackMapping.track_id == track_id,
                    DBTrackMapping.connector_track_id == ct_db_id,
                )
                .values(is_primary=True)
            )
            if rows_affected(result) > 0:
                promoted += 1

        return promoted

    # ── Integrity check queries ──────────────────────────────────────

    @db_operation("find_multiple_primary_violations")
    async def find_multiple_primary_violations(self) -> list[dict[str, object]]:
        """Find tracks with more than one primary mapping per connector."""
        # InstrumentedAttribute.cast() returns Any in SQLAlchemy stubs; the
        # declared annotation caps the spread to this one boundary line.
        is_primary_int: ColumnElement[int] = DBTrackMapping.is_primary.cast(Integer)  # pyright: ignore[reportAny]  # SQLAlchemy cast() stub
        stmt = (
            select(
                DBTrackMapping.track_id,
                DBTrackMapping.connector_name,
                func.sum(is_primary_int).label("primary_count"),
            )
            .group_by(DBTrackMapping.track_id, DBTrackMapping.connector_name)
            .having(func.sum(is_primary_int) > 1)
        )
        result = await self.session.execute(stmt)
        return [
            {
                "track_id": track_id,
                "connector_name": connector_name,
                "primary_count": primary_count,
            }
            for track_id, connector_name, primary_count in result.tuples()
        ]

    @db_operation("find_missing_primary_violations")
    async def find_missing_primary_violations(self) -> list[dict[str, object]]:
        """Find tracks with mappings for a connector but none marked primary."""
        has_primary = (
            select(DBTrackMapping.track_id, DBTrackMapping.connector_name)
            .where(DBTrackMapping.is_primary.is_(True))
            .subquery()
        )
        stmt = (
            select(
                DBTrackMapping.track_id,
                DBTrackMapping.connector_name,
                func.count().label("mapping_count"),
            )
            .outerjoin(
                has_primary,
                (DBTrackMapping.track_id == has_primary.c.track_id)
                & (DBTrackMapping.connector_name == has_primary.c.connector_name),
            )
            .where(has_primary.c.track_id.is_(None))
            .group_by(DBTrackMapping.track_id, DBTrackMapping.connector_name)
        )
        result = await self.session.execute(stmt)
        return [
            {
                "track_id": track_id,
                "connector_name": connector_name,
                "mapping_count": mapping_count,
            }
            for track_id, connector_name, mapping_count in result.tuples()
        ]

    @db_operation("count_orphaned_connector_tracks")
    async def count_orphaned_connector_tracks(self) -> int:
        """Count connector tracks with no track_mappings pointing to them."""
        stmt = (
            select(func.count(DBConnectorTrack.id))
            .outerjoin(
                DBTrackMapping,
                DBConnectorTrack.id == DBTrackMapping.connector_track_id,
            )
            .where(DBTrackMapping.id.is_(None))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    @db_operation("get_match_method_stats")
    async def get_match_method_stats(
        self, *, user_id: str, recent_days: int = 30
    ) -> list[MatchMethodStatRow]:
        """Aggregate match method statistics grouped by method and connector."""
        recent_cutoff = datetime.now(UTC) - timedelta(days=recent_days)
        stmt = (
            select(
                DBTrackMapping.match_method,
                DBTrackMapping.connector_name,
                func.count().label("total_count"),
                func.count(case((DBTrackMapping.created_at >= recent_cutoff, 1))).label(
                    "recent_count"
                ),
                # type_ declares AVG's NUMERIC result type (asyncpg yields
                # Decimal either way); emitted SQL is unchanged.
                func.avg(DBTrackMapping.confidence, type_=Numeric()).label(
                    "avg_confidence"
                ),
                func.min(DBTrackMapping.confidence).label("min_confidence"),
                func.max(DBTrackMapping.confidence).label("max_confidence"),
                # Confidence-band distribution in one scan (mirrors SQL pack
                # Q1): reject <50, review 50-84, accept 85-99, certain =100.
                func
                .count()
                .filter(DBTrackMapping.confidence < _BAND_REVIEW_MIN)
                .label("band_reject"),
                func
                .count()
                .filter(
                    DBTrackMapping.confidence.between(
                        _BAND_REVIEW_MIN, _BAND_REVIEW_MAX
                    )
                )
                .label("band_review"),
                func
                .count()
                .filter(
                    DBTrackMapping.confidence.between(
                        _BAND_ACCEPT_MIN, _BAND_ACCEPT_MAX
                    )
                )
                .label("band_accept"),
                func
                .count()
                .filter(DBTrackMapping.confidence == _CONFIDENCE_CERTAIN)
                .label("band_certain"),
            )
            .where(DBTrackMapping.user_id == user_id)
            .group_by(DBTrackMapping.match_method, DBTrackMapping.connector_name)
            .order_by(func.count().desc())
        )
        result = await self.session.execute(stmt)
        # 11 select() columns exceeds SQLAlchemy's typed-tuple overloads (capped
        # at 10 — see _selectable_constructors.py), which fall back to
        # Select[Any]. The declared annotation caps the Any spread to this one
        # boundary line instead of leaking into every field below.
        rows: Sequence[
            tuple[str, str, int, int, float, int, int, int, int, int, int]
        ] = result.tuples().all()
        return [
            MatchMethodStatRow(
                match_method=match_method,
                connector_name=connector_name,
                total_count=total_count,
                recent_count=recent_count,
                avg_confidence=round(float(avg_confidence), 1),
                min_confidence=min_confidence,
                max_confidence=max_confidence,
                band_reject=band_reject,
                band_review=band_review,
                band_accept=band_accept,
                band_certain=band_certain,
            )
            for (
                match_method,
                connector_name,
                total_count,
                recent_count,
                avg_confidence,
                min_confidence,
                max_confidence,
                band_reject,
                band_review,
                band_accept,
                band_certain,
            ) in rows
        ]

    @db_operation("count_stale_denormalized_ids")
    async def count_stale_denormalized_ids(self, *, user_id: str) -> int:
        """Count tracks with a stale or dangling denormalized spotify_id.

        Sums two disjoint failure modes (mirrors SQL pack Q7,
        scripts/sql/identity-quantification.sql):
          - a primary spotify mapping exists but the column disagrees with
            its connector identifier
          - the column is set but no primary spotify mapping exists at all

        Epic 5 fixed the write flow that caused this drift; this watches the
        stock drain via the read-path healing in ``ensure_primary_for_connector``.
        """
        primary_spotify = (DBTrackMapping.connector_name == "spotify") & (
            DBTrackMapping.is_primary.is_(True)
        )
        stmt = (
            select(
                func
                .count()
                .filter(
                    DBTrackMapping.id.is_not(None),
                    DBTrack.spotify_id.is_distinct_from(
                        DBConnectorTrack.connector_track_identifier
                    ),
                )
                .label("column_disagrees_with_primary"),
                func
                .count()
                .filter(
                    DBTrack.spotify_id.is_not(None),
                    DBTrackMapping.id.is_(None),
                )
                .label("column_set_but_no_mapping"),
            )
            .select_from(DBTrack)
            .outerjoin(
                DBTrackMapping,
                (DBTrackMapping.track_id == DBTrack.id)
                & primary_spotify
                & (DBTrackMapping.user_id == user_id),
            )
            .outerjoin(
                DBConnectorTrack,
                DBConnectorTrack.id == DBTrackMapping.connector_track_id,
            )
            .where(
                DBTrack.user_id == user_id,
                (DBTrack.spotify_id.is_not(None)) | (DBTrackMapping.id.is_not(None)),
            )
        )
        result = await self.session.execute(stmt)
        # .tuples().one() (not .one()) so the pair unpacks as (int, int)
        # instead of an untyped Row.
        disagrees, no_mapping = result.tuples().one()
        return disagrees + no_mapping

    @db_operation("count_confidence_evidence_divergence")
    async def count_confidence_evidence_divergence(self, *, user_id: str) -> int:
        """Count mappings bumped to confidence=100 while the evidence disagrees.

        Mirrors SQL pack Q6 — NULL evidence (constant-assigned mappings) is
        excluded naturally by the ``< 100`` comparison against SQL NULL.
        """
        # JSONB numeric extraction (``->>`` then ``::numeric``) as a raw predicate:
        # SQLAlchemy's JSON-subscript comparator types Any under basedpyright, and a
        # text() fragment keeps this typed without a suppression. The literal has no
        # interpolation (user_id is bound via the ORM predicate), so it is
        # injection-safe.
        stmt = select(func.count()).where(
            DBTrackMapping.user_id == user_id,
            DBTrackMapping.confidence == _CONFIDENCE_CERTAIN,
            text("(confidence_evidence->>'final_score')::numeric < 100"),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()
