"""Persists track connections between the internal database and external services.

Stores connector-track payloads, asserts mappings from canonical tracks to
external ids, elects primaries and records the resolution events those
writes earn. Which canonical an incoming payload belongs to is not decided
here: ``application/services/track_resolution.py`` plans that with the domain
planner and calls the persistence seams below in order.

The mapping mechanism itself — assert, live scoping, election, event
recording — is the generic :class:`MappingRepository` in
``_shared/mapping.py``; :class:`TrackMappingRepository` is its track
instantiation, adding only what the generic cannot know about tracks: the
``DBTrack.mappings`` identity-map collection.
"""

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
    func,
    select,
    text,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities import Artist, ConnectorTrack, Track, TrackMapping
from src.domain.entities.shared import JsonDict, JsonValue
from src.domain.entities.track_mapping import (
    MappingOrigin,
    MatchMethod,
    SupersessionReason,
    is_mapping_origin,
    is_match_method,
)
from src.domain.exceptions import NotFoundError
from src.domain.repositories.connector import (
    ConnectorMappingSpec,
    FullMappingInfo,
    MatchMethodStatRow,
    PrimaryMappingDetail,
)
from src.domain.repositories.mapping import (
    ElectionMode,
    PrimaryCandidate,
    PrimaryVacancyRepair,
)
from src.domain.repositories.resolution import (
    ResolutionDecision,
    ResolutionRecorderProtocol,
)
from src.infrastructure.persistence.database.live_rows import (
    INCLUDE_SUPERSEDED,
    expire_mapping_identity,
    live_only,
)
from src.infrastructure.persistence.database.models import (
    DBConnectorTrack,
    DBResolutionNegative,
    DBTrackMapping,
)
from src.infrastructure.persistence.repositories._shared.connector_tracks import (
    artist_names_column,
    build_connector_track_row,
    extract_db_artist_names,
)
from src.infrastructure.persistence.repositories._shared.mapping import (
    STALE_ID_METHODS,
    MappingAssertion,
    MappingRepository,
    MappingShape,
)
from src.infrastructure.persistence.repositories.base_repo import BaseRepository
from src.infrastructure.persistence.repositories.mappers import BaseModelMapper
from src.infrastructure.persistence.repositories.repo_decorator import db_operation
from src.infrastructure.persistence.repositories.resolution import actively_suppressing
from src.infrastructure.persistence.repositories.track.core import TrackRepository
from src.infrastructure.services.resolution_recorder import ResolutionRecorder

logger = get_logger(__name__)


# Confidence-band thresholds for the matching-health distribution (mirror SQL
# pack Q1): reject <50, review 50-84, accept 85-99, certain =100.
_BAND_REVIEW_MIN = 50
_BAND_REVIEW_MAX = 84
_BAND_ACCEPT_MIN = 85
_BAND_ACCEPT_MAX = 99
_CONFIDENCE_CERTAIN = 100


def _not_stale_id(model: type[DBTrackMapping]) -> ColumnElement[bool]:
    """Rows that may hold primacy — paired with ``live_only`` in every election.

    Kept separate from ``live_only`` so each statement still names the
    live-rows invariant itself (``test_live_rows_conformance`` reads for it).
    """
    return model.match_method.notin_(STALE_ID_METHODS)


def _connector_id_map(stored: Sequence[ConnectorTrack]) -> dict[tuple[str, str], UUID]:
    """Stored connector tracks keyed ``(connector, external id) -> row id``."""
    return {(ct.connector_name, ct.connector_track_identifier): ct.id for ct in stored}


def _vocabulary_match_method(value: str, *, row: UUID) -> MatchMethod:
    """Narrow a persisted ``match_method`` to the domain vocabulary.

    One policy for every reader: a row outside the vocabulary is a schema
    fact nobody designed, so it raises rather than being skipped by one
    reader and surfaced by another.
    """
    if not is_match_method(value):
        raise ValueError(
            f"track_mapping row {row} carries a match_method outside the domain "
            f"vocabulary: {value!r}"
        )
    return value


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
            artists=artist_names_column(a.name for a in domain_model.artists),
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
        method = _vocabulary_match_method(db_model.match_method, row=db_model.id)
        origin = db_model.origin
        if not is_mapping_origin(origin):
            raise ValueError(
                f"track_mapping {db_model.id} carries an origin outside the domain "
                f"vocabulary: {origin!r}"
            )
        return TrackMapping(
            id=db_model.id,
            user_id=db_model.user_id,
            track_id=db_model.track_id,
            connector_track_id=db_model.connector_track_id,
            connector_name=db_model.connector_name,
            match_method=method,
            confidence=db_model.confidence,
            confidence_evidence=evidence,
            origin=origin,
            is_primary=db_model.is_primary,
            last_seen_at=db_model.last_seen_at,
            superseded_by_id=db_model.superseded_by_id,
            superseded_at=db_model.superseded_at,
            supersession_reason=cast(
                "SupersessionReason | None", db_model.supersession_reason
            ),
            supersession_scope=db_model.supersession_scope,
            next_verify_at=db_model.next_verify_at,
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
            superseded_by_id=domain_model.superseded_by_id,
            superseded_at=domain_model.superseded_at,
            supersession_reason=domain_model.supersession_reason,
            supersession_scope=domain_model.supersession_scope,
            next_verify_at=domain_model.next_verify_at,
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


# The live-identity key — the columns behind ``uq_track_mappings_live_connector``.
TRACK_MAPPING_SHAPE = MappingShape(
    entity_kind="track",
    owner_id_col="track_id",
    connector_id_col="connector_track_id",
    live_key=("user_id", "connector_track_id", "connector_name"),
    supersession=True,
)


class TrackMappingRepository(MappingRepository[DBTrackMapping, TrackMapping]):
    """The track instantiation of the generic mapping mechanism.

    Assert, live scoping, election and event recording are the generic's;
    the hook below is what tracks add — the ``DBTrack.mappings`` collection a
    Core write leaves stale.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize with database session and data mapper."""
        super().__init__(
            session=session,
            model_class=DBTrackMapping,
            mapper=TrackMappingMapper(),
            shape=TRACK_MAPPING_SHAPE,
        )

    @override
    def _expire_owner_identity(self, owner_ids: Sequence[UUID]) -> None:
        expire_mapping_identity(self.session, track_ids=owner_ids)


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
        # Application-layer rows are ``Mapping[str, object]`` — the casts state
        # what each key is known to hold; the shared builder types the columns.
        upsert_data: list[dict[str, object]] = [
            build_connector_track_row(
                connector_name,
                cast("str", td["connector_id"]),
                title=cast("str", td.get("title", "")),
                artist_names=cast("Sequence[str]", td.get("artists", [])),
                album=cast("str | None", td.get("album")),
                duration_ms=cast("int | None", td.get("duration_ms")),
                release_date=cast("datetime | None", td.get("release_date")),
                isrc=cast("str | None", td.get("isrc")),
                raw_metadata=cast(
                    "Mapping[str, object] | None", td.get("raw_metadata")
                ),
                last_updated=now,
            )
            for td in tracks_data
        ]

        return _connector_id_map(await self._upsert_connector_tracks(upsert_data))

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
            .where(live_only(DBTrackMapping))
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

            by_ct_id = await self.find_tracks_by_connector_track_ids(
                ct_ids, user_id=user_id
            )
            for ct_id, track in by_ct_id.items():
                results[connector, ct_id_to_external_id[ct_id]] = track

        return results

    @db_operation("find_tracks_by_connector_track_ids")
    async def find_tracks_by_connector_track_ids(
        self, connector_track_ids: Sequence[UUID], *, user_id: str
    ) -> dict[UUID, Track]:
        """The canonical each of these ``connector_tracks`` rows is live-mapped to.

        Mappings alone answer it: only ``track_id``/``connector_track_id``
        are read off the mapping rows (no relationship loads), and the
        tracks are hydrated by id.
        """
        if not connector_track_ids:
            return {}
        mappings = await self.mapping_repo.find_by(
            [
                self.mapping_repo.model_class.connector_track_id.in_(
                    list(connector_track_ids)
                ),
                self.mapping_repo.model_class.user_id == user_id,
            ],
            load_relationships=[],
        )
        tracks = await self.track_repo.find_tracks_by_ids([
            m.track_id for m in mappings
        ])
        return {
            m.connector_track_id: tracks[m.track_id]
            for m in mappings
            if m.track_id in tracks
        }

    @db_operation("map_tracks_to_connectors")
    async def map_tracks_to_connectors(
        self,
        mappings: list[ConnectorMappingSpec],
        *,
        connector_track_ids: Mapping[tuple[str, str], UUID] | None = None,
    ) -> list[Track]:
        """Link existing internal tracks to external service IDs with confidence scores.

        Pipeline: build connector-track rows → bulk upsert → build mapping rows →
        drop rows that would overwrite manual overrides → assert mappings
        (append-only: a changed decision supersedes rather than overwrites) →
        elect primaries.

        ``connector_track_ids`` — the ``(connector, external id) -> id`` map
        of rows a caller has *already* upserted from the payload — skips the
        upsert step. The ingest path passes it so that mapping a reused
        canonical never rewrites ``connector_tracks`` with the canonical's
        own title and length in place of what the service actually said.

        Every asserted pair leaves with a primary. A spec with ``primary=True``
        deposes and elects (``ensure_primaries`` in ``reset`` mode); every
        other spec fills a vacancy only (``fill`` mode), which is a no-op
        where a primary already holds the slot (including a manual override).
        Both run in a fixed handful of statements for the whole batch. Reads
        never elect — the mapper is a pure function of the row — so a pair
        gains its primary here or in an explicit repair
        (``ensure_primary_for_connector``), never on the way out. Mapping one
        track is ``map_track_to_connector``, a one-spec call to exactly this.

        Args:
            mappings: Mapping specs pairing each track with its connector, external
                id, match method, confidence, optional metadata/evidence, and
                whether the mapping takes primacy.

        Returns:
            List of Track objects updated with external service connections.
        """
        if not mappings:
            return []

        updated_tracks = self._build_updated_tracks(mappings)
        connector_id_map = (
            dict(connector_track_ids)
            if connector_track_ids is not None
            else _connector_id_map(
                await self._upsert_connector_tracks(
                    self._build_connector_track_rows(mappings)
                )
            )
        )
        mapping_rows = await self.mapping_repo.filter_manual_overrides(
            self._build_mapping_rows(mappings, connector_id_map)
        )

        if mapping_rows:
            assertion = await self.mapping_repo.assert_mappings(mapping_rows)
            await self.mapping_repo.record_assertion(assertion)
            await self._restore_superseded_primaries(assertion)

        # The specs that named which connector id each track's primary should
        # be: a deposition, not a vacancy fill. ``connector_id_map`` was just
        # built (or passed) from these same rows, so the external-id →
        # internal-id conversion is a dict lookup, never a query. Only a row
        # this batch actually asserted can be elected: a spec the
        # manual-override filter dropped has no row under this track, and a
        # stale-id spec is never electable — a ``reset`` that cannot promote
        # what it deposed raises, so neither may reach it. Their pairs fall
        # through to the vacancy fill below instead.
        asserted = {
            (row["track_id"], row["connector_track_id"]) for row in mapping_rows
        }
        promote_to_primary = [
            PrimaryCandidate(spec.track.id, spec.connector, ct_id)
            for spec in mappings
            if spec.primary
            and spec.track.id
            and spec.match_method not in STALE_ID_METHODS
            and (ct_id := connector_id_map.get((spec.connector, spec.connector_id)))
            and (spec.track.id, ct_id) in asserted
        ]
        if promote_to_primary:
            _ = await self.mapping_repo.ensure_primaries(
                promote_to_primary, mode="reset"
            )

        if mapping_rows:
            await self._fill_primary_vacancies(
                mappings, asserted, connector_id_map, promote_to_primary
            )

        # Note: metrics extraction lives in the application layer
        # (MetricsApplicationService); this repository maps track identity only.
        return updated_tracks

    def _build_connector_track_rows(
        self, mappings: list[ConnectorMappingSpec]
    ) -> list[dict[str, object]]:
        """Build deduplicated connector-track upsert rows (one per external id)."""
        now = datetime.now(UTC)
        rows: list[dict[str, object]] = []
        seen_keys: set[tuple[str, str]] = set()
        for spec in mappings:
            key = (spec.connector, spec.connector_id)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            rows.append(
                build_connector_track_row(
                    spec.connector,
                    spec.connector_id,
                    title=spec.track.title,
                    artist_names=[a.name for a in spec.track.artists],
                    album=spec.track.album,
                    duration_ms=spec.track.duration_ms,
                    release_date=spec.track.release_date,
                    isrc=spec.track.isrc,
                    raw_metadata=spec.metadata,
                    last_updated=now,
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
    ) -> list[ConnectorTrack]:
        """The one ``connector_tracks`` write: bulk upsert on the external id.

        Intra-batch duplicates are last-wins inside ``bulk_upsert``.
        """
        return await self.connector_repo.bulk_upsert(
            rows, lookup_keys=["connector_name", "connector_track_identifier"]
        )

    async def _restore_superseded_primaries(
        self,
        assertion: MappingAssertion,
    ) -> None:
        """Re-promote the successors of mappings that had been primary.

        Supersession strips the incumbent's primacy, so without this a re-score
        of a track's primary mapping leaves it with a live mapping and no
        primary at all — the review queue's provenance reads as absent.

        When the successor landed on a *different* track, the track it left
        needs the opposite treatment: not a re-promotion (the identifier it
        held now belongs elsewhere) but ``ensure_primary_for_connector``, which
        promotes a surviving sibling.

        The restorations already carry the connector track's internal UUID,
        so this goes straight to ``ensure_primaries`` in ``fill`` mode — no
        round trip through the external string id and back. That mode fills
        a vacancy and never deposes, so a successor arriving on a track that
        already has a primary (pinned or automatic) leaves it be: the
        restoration exists to repair the slot supersession emptied, not to
        claim one that is still occupied.

        The vacated-pairs loop above stays per-pair on purpose. It is 0-2
        entries in practice, and each one needs
        ``ensure_primary_for_connector``'s selection policy — pick the
        highest-confidence survivor — which is a decision per track, not a set
        operation.
        """
        for track_id, connector_name in assertion.vacated_owners:
            await self.ensure_primary_for_connector(track_id, connector_name)

        if assertion.primacy_restorations:
            _ = await self.mapping_repo.ensure_primaries(
                assertion.primacy_restorations, mode="fill"
            )

    async def _fill_primary_vacancies(
        self,
        mappings: list[ConnectorMappingSpec],
        asserted: set[tuple[object, object]],
        connector_id_map: dict[tuple[str, str], UUID],
        promote_to_primary: list[PrimaryCandidate],
    ) -> None:
        """Elect a primary for every asserted pair that still lacks one.

        Candidates are the rows that were actually asserted (``asserted`` is
        their (track, connector track) set) — a spec whose row
        ``filter_manual_overrides`` dropped would win first-wins dedup and
        then match nothing, leaving the pair vacant. A pair that also carried
        a ``primary=True`` spec in this batch was just elected and is skipped,
        and a stale-id secondary is never a candidate: it exists so a dead id
        resolves from cache, and a pair's primary names its current identity.
        Highest confidence first, so first-wins is the same choice
        ``ensure_primary_for_connector`` makes.
        """
        elected_pairs = {
            (candidate.owner_id, candidate.connector_name)
            for candidate in promote_to_primary
        }
        fill_vacancies = [
            PrimaryCandidate(spec.track.id, spec.connector, ct_id)
            for spec in sorted(mappings, key=lambda s: -s.confidence)
            if spec.track.id
            and not spec.primary
            and spec.match_method not in STALE_ID_METHODS
            and (spec.track.id, spec.connector) not in elected_pairs
            and (ct_id := connector_id_map.get((spec.connector, spec.connector_id)))
            and (spec.track.id, ct_id) in asserted
        ]
        if fill_vacancies:
            _ = await self.mapping_repo.ensure_primaries(fill_vacancies, mode="fill")

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
                "origin": spec.origin,
                "is_primary": False,  # Don't set primary here, handle it separately
            })
        return rows

    @db_operation("map_track_to_connector")
    async def map_track_to_connector(
        self,
        track: Track,
        connector: str,
        connector_id: str,
        match_method: MatchMethod,
        confidence: int,
        metadata: dict[str, object] | None = None,
        confidence_evidence: dict[str, object] | None = None,
        auto_set_primary: bool = True,
        origin: MappingOrigin = "automatic",
    ) -> Track:
        """Link an existing internal track to an external service ID.

        One mapping is the degenerate case of a batch of them, all the way
        down to the election: ``auto_set_primary`` is the spec's ``primary``
        flag, so the promotion and the vacancy rules are the batch path's and
        there is no second election chain to keep in agreement with it.

        No existence probe on ``track``: every caller holds a canonical it
        either just persisted or just read back inside this same transaction,
        so the SELECT could only ever confirm what the caller already knows —
        and it did so once per mapping, which is twice per relinked Spotify
        track. The mapping insert carries ``track_id`` under a foreign key, so
        a genuinely absent track still fails, at the write rather than a
        round trip earlier.
        """
        results = await self.map_tracks_to_connectors([
            ConnectorMappingSpec(
                track=track,
                connector=connector,
                connector_id=connector_id,
                match_method=match_method,
                confidence=confidence,
                metadata=metadata,
                confidence_evidence=confidence_evidence,
                origin=origin,
                primary=auto_set_primary,
            )
        ])
        return results[0] if results else track

    @db_operation("upsert_connector_tracks")
    async def upsert_connector_tracks(
        self, connector: str, tracks: Sequence[ConnectorTrack]
    ) -> dict[str, ConnectorTrack]:
        """Upsert one ``connector_tracks`` row per external id (last occurrence wins).

        Returns the stored rows keyed by external identifier, each carrying
        its database id — the id every mapping and review for that payload
        references.
        """
        if not tracks:
            return {}
        now = datetime.now(UTC)
        rows: list[dict[str, object]] = [
            build_connector_track_row(
                connector,
                track.connector_track_identifier,
                title=track.title,
                artist_names=[a.name for a in track.artists],
                album=track.album,
                duration_ms=track.duration_ms,
                release_date=track.release_date,
                isrc=track.isrc,
                raw_metadata=track.raw_metadata,
                last_updated=now,
            )
            for track in tracks
        ]
        stored = await self._upsert_connector_tracks(rows)
        return {ct.connector_track_identifier: ct for ct in stored}

    @db_operation("touch_last_seen")
    async def touch_last_seen(
        self, connector: str, connector_track_ids: Sequence[UUID], *, user_id: str
    ) -> None:
        """Stamp ``last_seen_at`` on the live mappings of re-encountered payloads.

        Re-encounter is a freshness signal, not evidence: it proves the
        connector track still exists, not that the canonical match was
        right, so confidence is never touched here (FM1a) — and freshness is
        origin-independent, so manual overrides are stamped too.
        """
        if not connector_track_ids:
            return
        _ = await self.session.execute(
            update(DBTrackMapping)
            .where(
                DBTrackMapping.user_id == user_id,
                DBTrackMapping.connector_name == connector,
                DBTrackMapping.connector_track_id.in_(list(connector_track_ids)),
                live_only(DBTrackMapping),
            )
            .values(last_seen_at=datetime.now(UTC))
        )

    def _resolution_recorder(self) -> ResolutionRecorderProtocol:
        """The identity write seam bound to this repository's transaction."""
        return ResolutionRecorder(self.session)

    async def _live_mapping_row(
        self, mapping_id: UUID, *, user_id: str
    ) -> DBTrackMapping | None:
        """Fetch one live mapping row by id, refreshed past the identity map.

        The shared body behind every "fetch the live row, then act on it" site
        — ``get_mapping_by_id``, ``delete_mapping``, and the two lookups inside
        ``update_mapping_track`` used to each repeat this query verbatim.

        ``populate_existing``: supersession lands via Core DML, so a row
        already in this session's identity map would otherwise answer from
        its pre-flip state (v0.10.0 hit exactly this on plays).

        ``user_id`` is required, with no "scope it if you happen to have one"
        escape: per PDR-002 the Neon owner role has BYPASSRLS in production, so
        this WHERE clause — not RLS — is the isolation that actually runs, and
        a fetch-then-act helper that can be called unscoped is one refactor
        away from being called unscoped somewhere it matters.
        """
        # ``live_only`` stays inside the statement that builds the select rather
        # than in a conditions list assembled above it. The source-level guard in
        # ``test_live_rows_conformance`` reads one statement at a time, so a
        # predicate added from elsewhere is invisible to it — and a guard that
        # cannot see the predicate is a guard that cannot protect the next
        # reader who forgets it.
        result = await self.session.execute(
            select(DBTrackMapping)
            .where(
                DBTrackMapping.id == mapping_id,
                DBTrackMapping.user_id == user_id,
                live_only(DBTrackMapping),
            )
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    @db_operation("get_mapping_by_id")
    async def get_mapping_by_id(
        self, mapping_id: UUID, *, user_id: str
    ) -> TrackMapping | None:
        """Get a single live track mapping by its database ID, scoped to user.

        A superseded mapping reads as absent — callers here are acting on
        current identity, and ``get_supersession_chain`` is the way to ask for
        history.
        """
        row = await self._live_mapping_row(mapping_id, user_id=user_id)
        if row is None:
            return None
        return await TrackMappingMapper.to_domain(row)

    @db_operation("get_supersession_chain")
    async def get_supersession_chain(
        self, mapping_id: UUID, *, user_id: str, max_depth: int = 32
    ) -> list[TrackMapping]:
        """Walk ``superseded_by_id`` forward from a mapping, oldest first.

        The chain *is* the history — no MusicBrainz-style path compression — so
        a walk is the only way to read it, and walks are rare (hot paths hit
        the live partial index instead). Two guards make the walk safe on data
        no constraint can rule out: a seen-set terminates a cycle, and
        ``max_depth`` bounds an adversarially long chain. The returned list
        starts at ``mapping_id`` itself and ends at the live successor (or at
        the last retirement, when there is no replacement).
        """
        chain: list[TrackMapping] = []
        seen: set[UUID] = set()
        current: UUID | None = mapping_id

        while current is not None and len(chain) < max_depth:
            if current in seen:
                logger.warning(
                    "supersession_chain_cycle",
                    mapping_id=mapping_id,
                    repeated_id=current,
                    depth=len(chain),
                )
                break
            seen.add(current)
            result = await self.session.execute(
                select(DBTrackMapping)
                .where(
                    DBTrackMapping.id == current,
                    DBTrackMapping.user_id == user_id,
                )
                .execution_options(**{INCLUDE_SUPERSEDED: True}, populate_existing=True)
            )
            row = result.scalar_one_or_none()
            if row is None:
                break
            chain.append(await TrackMappingMapper.to_domain(row))
            current = row.superseded_by_id

        return chain

    @db_operation("delete_mapping")
    async def delete_mapping(self, mapping_id: UUID, *, user_id: str) -> TrackMapping:
        """Retire a live mapping and return its pre-retirement entity.

        No longer a hard delete (v0.10.2): the row stays, stamped
        ``superseded_at`` + ``manual`` with **no successor**, because "the user
        unlinked this" is exactly the kind of change the append-only ledger
        exists to preserve — deleting it would make an intentional correction
        indistinguishable from a mapping that never existed.

        The observable contract is unchanged: the mapping reads as absent
        everywhere afterwards (every reader is live-scoped), the same entity
        comes back, and the same ``NotFoundError`` is raised when there is
        nothing live to retire.
        """
        row = await self._live_mapping_row(mapping_id, user_id=user_id)
        if row is None:
            raise NotFoundError(f"Mapping {mapping_id} not found")
        mapping = await TrackMappingMapper.to_domain(row)

        recorder = self._resolution_recorder()
        _ = await recorder.retire_mapping(mapping_id, user_id=user_id, reason="manual")
        _ = await recorder.record(
            [
                ResolutionDecision(
                    event_type="superseded",
                    connector_name=mapping.connector_name,
                    connector_track_id=mapping.connector_track_id,
                    track_id=mapping.track_id,
                    resulting_mapping_id=mapping_id,
                    confidence=mapping.confidence,
                    payload={"reason": "manual", "retired": True},
                )
            ],
            user_id=user_id,
        )
        return mapping

    @db_operation("update_mapping_track")
    async def update_mapping_track(
        self,
        mapping_id: UUID,
        new_track_id: UUID,
        origin: str,
        *,
        user_id: str,
    ) -> TrackMapping:
        """Move a live mapping to a different canonical track, append-only.

        A relink used to be an in-place UPDATE, which is precisely the flip
        that leaves no history: afterwards nothing could say the mapping ever
        pointed elsewhere. It now goes through the same ``assert_mappings``
        machinery every other identity write uses — the incumbent is retired
        with reason ``manual`` and a successor row is inserted on the same live
        key — so the chain answers "what changed, and when".

        The returned entity is the *successor*, carrying the new track and the
        given origin; its id differs from ``mapping_id``, which is what
        "append-only" means.

        ``user_id`` scopes both lookups the same way ``get_mapping_by_id`` and
        ``delete_mapping`` are scoped (see ``_live_mapping_row``). The sole
        caller already validates ownership via ``require_owned_mapping``, so
        this is not the only guard — but PDR-002 records the Neon owner role as
        BYPASSRLS in production, and a move that can be issued unscoped should
        not be reachable just because today's caller happens to check first.
        """
        row = await self._live_mapping_row(mapping_id, user_id=user_id)
        if row is None:
            raise NotFoundError(f"Mapping {mapping_id} not found")

        # Read off the row *before* asserting: ``assert_mappings`` expires this
        # row from the identity map (it supersedes it via Core DML), and an
        # attribute touched after that would try to refresh itself with sync IO
        # from an async context.
        owner = row.user_id

        assertion = await self.mapping_repo.assert_mappings(
            [
                {
                    "user_id": owner,
                    "track_id": new_track_id,
                    "connector_track_id": row.connector_track_id,
                    "connector_name": row.connector_name,
                    "match_method": row.match_method,
                    "confidence": row.confidence,
                    "confidence_evidence": row.confidence_evidence,
                    "origin": origin,
                    "is_primary": False,
                }
            ],
            reason="manual",
        )
        await self.mapping_repo.record_assertion(assertion)

        successor_id = next(
            iter(set(assertion.created) | set(assertion.superseded.values())),
            mapping_id,
        )
        # The predecessor's owner rather than the argument: the successor was
        # inserted under it, and the two are equal only because the fetch above
        # was scoped. Using the row's own value keeps this correct even if that
        # ever stops being true.
        refreshed = await self._live_mapping_row(successor_id, user_id=owner)
        if refreshed is None:
            raise NotFoundError(f"Mapping {successor_id} not found")
        return await TrackMappingMapper.to_domain(refreshed)

    @db_operation("count_mappings_for_connector_track")
    async def count_mappings_for_connector_track(self, connector_track_id: UUID) -> int:
        """Count remaining live mappings for a given connector track."""
        result = await self.session.execute(
            select(func.count())
            .select_from(DBTrackMapping)
            .where(
                DBTrackMapping.connector_track_id == connector_track_id,
                live_only(DBTrackMapping),
            )
        )
        return result.scalar_one()

    @db_operation("get_remaining_mappings")
    async def _get_remaining_mappings(
        self, track_id: UUID, connector_name: str
    ) -> list[TrackMapping]:
        """Electable mappings for a (track, connector) pair, confidence desc.

        Stale-id cache rows are left out (``_not_stale_id``): a pair with only
        those left has no live identity, and a dead id is never promoted. The
        ``id`` ascending secondary
        key makes the ordering total: on an equal-confidence tie
        ``remaining[0]`` is deterministic, and the mapper's display-fallback
        selection applies the SAME (confidence desc, id asc) tiebreak, so the
        displayed identifier and the promoted primary agree (v0.8.18 FM4c: one
        promotion policy).
        """
        result = await self.session.execute(
            select(DBTrackMapping)
            .where(
                DBTrackMapping.track_id == track_id,
                DBTrackMapping.connector_name == connector_name,
                live_only(DBTrackMapping),
                _not_stale_id(DBTrackMapping),
            )
            .order_by(DBTrackMapping.confidence.desc(), DBTrackMapping.id.asc())
            # Promotion runs right after Core supersession/primary flips.
            .execution_options(populate_existing=True)
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

        Promotes the highest-confidence electable mapping if none is primary.
        A pair with no electable mapping left has no live identity to name,
        so there is nothing to do.
        """
        remaining = await self._get_remaining_mappings(track_id, connector_name)
        if not remaining or any(m.is_primary for m in remaining):
            return
        # Choosing the winner is this method's whole job; promoting it is the
        # election's, and going through it is what keeps one promotion path.
        #
        # ``fill`` only fills a vacancy, which is precisely the state the
        # check above has just established this track to be in.
        _ = await self.mapping_repo.ensure_primaries(
            [
                PrimaryCandidate(
                    track_id, connector_name, remaining[0].connector_track_id
                )
            ],
            mode="fill",
        )

    @db_operation("get_primary_mapping_details")
    async def get_primary_mapping_details(
        self, track_ids: list[UUID], connector: str
    ) -> dict[UUID, PrimaryMappingDetail]:
        """Get primary-mapping provenance (id, confidence, method) per track.

        A primary-only track→connector join widened with the mapping row's
        stored confidence and match method (v0.8.18 FM1b — it re-asserts real
        provenance, not a synthetic constant).
        """
        if not track_ids:
            return {}

        stmt = (
            select(
                DBTrackMapping.track_id,
                DBConnectorTrack.connector_track_identifier,
                DBTrackMapping.confidence,
                DBTrackMapping.match_method,
            )
            .join(
                DBConnectorTrack,
                DBTrackMapping.connector_track_id == DBConnectorTrack.id,
            )
            .where(
                DBTrackMapping.track_id.in_(track_ids),
                DBTrackMapping.is_primary.is_(True),
                DBConnectorTrack.connector_name == connector,
                live_only(DBTrackMapping),
            )
        )

        result = await self.session.execute(stmt)
        return {
            track_id: PrimaryMappingDetail(
                connector_id=conn_id,
                confidence=confidence,
                match_method=_vocabulary_match_method(match_method, row=track_id),
            )
            for track_id, conn_id, confidence, match_method in result.tuples()
        }

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

        # Build efficient join query (primary live mappings only)
        stmt = (
            select(
                DBTrackMapping.track_id,
                DBConnectorTrack.raw_metadata,
            )
            .join(
                DBConnectorTrack,
                DBTrackMapping.connector_track_id == DBConnectorTrack.id,
            )
            .where(
                DBTrackMapping.track_id.in_(track_ids),
                DBTrackMapping.is_primary.is_(True),
                DBConnectorTrack.connector_name == connector,
                live_only(DBTrackMapping),
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

    async def ensure_primaries(
        self, candidates: Sequence[PrimaryCandidate], *, mode: ElectionMode
    ) -> list[PrimaryCandidate]:
        """Elect the named mapping primary for each pair — see the generic.

        The protocol-facing spelling of
        :meth:`MappingRepository.ensure_primaries`; ``fill`` and ``reset`` are
        the only two elections there are.
        """
        return await self.mapping_repo.ensure_primaries(candidates, mode=mode)

    # ── Integrity check queries ──────────────────────────────────────

    # Every check below is supersession-aware by default: a retired mapping is
    # not an integrity violation, it is history (the Wikibase precedent —
    # constraint checks ignore deprecated ranks).

    @db_operation("find_multiple_primary_violations")
    async def find_multiple_primary_violations(self) -> list[dict[str, object]]:
        """Find tracks with more than one live primary mapping per connector."""
        # InstrumentedAttribute.cast() returns Any in SQLAlchemy stubs; the
        # declared annotation caps the spread to this one boundary line.
        is_primary_int: ColumnElement[int] = DBTrackMapping.is_primary.cast(Integer)  # pyright: ignore[reportAny]  # SQLAlchemy cast() stub
        stmt = (
            select(
                DBTrackMapping.track_id,
                DBTrackMapping.connector_name,
                func.sum(is_primary_int).label("primary_count"),
            )
            .where(live_only(DBTrackMapping))
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
        """Find tracks with electable live mappings for a connector but no primary.

        A pair whose only live rows are stale-id cache entries is not counted:
        it has no live identity to elect (``_not_stale_id``).
        """
        has_primary = (
            select(DBTrackMapping.track_id, DBTrackMapping.connector_name)
            .where(DBTrackMapping.is_primary.is_(True), live_only(DBTrackMapping))
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
            .where(
                has_primary.c.track_id.is_(None),
                live_only(DBTrackMapping),
                _not_stale_id(DBTrackMapping),
            )
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

    async def repair_missing_primaries(
        self, *, user_id: str, dry_run: bool = False
    ) -> list[PrimaryVacancyRepair]:
        """Fill every vacant primary slot this user's live mappings have left.

        The bulk sibling of ``ensure_primary_for_connector``, for the stock a
        past writer left behind: every writer elects now and no read repairs,
        so a legacy vacancy is a permanent FAIL on the
        ``missing_primary_mappings`` integrity check with nothing to clear it.
        The election itself is the generic's.
        """
        return await self.mapping_repo.repair_missing_primaries(
            user_id=user_id, dry_run=dry_run
        )

    @db_operation("count_orphaned_connector_tracks")
    async def count_orphaned_connector_tracks(self) -> int:
        """Count connector tracks with no live mapping and no negative-cache row.

        The live predicate belongs in the JOIN, not the WHERE: on an outer join
        a WHERE clause would discard the unmatched rows this query exists to
        count.

        The second anti-join is what keeps the number meaning "leaked row". A
        connector track that exists *only* to key a cannot-link constraint or a
        no-match backoff clock (v0.10.2 materializes one for exactly that) has
        no mapping by design — counting it as an orphan would report the
        negative cache working correctly as a data-integrity failure, and the
        count grows with every rejection.

        ``actively_suppressing`` is what bounds that excuse in time. Matching
        any negative row ever written would blind the metric permanently: a
        withdrawn rejection or a long-expired backoff clock explains nothing,
        and a connector track whose mapping later leaked for real would stay
        uncounted forever because of a row that stopped mattering months ago.
        """
        stmt = (
            select(func.count(DBConnectorTrack.id))
            .outerjoin(
                DBTrackMapping,
                (DBConnectorTrack.id == DBTrackMapping.connector_track_id)
                & live_only(DBTrackMapping),
            )
            .outerjoin(
                DBResolutionNegative,
                (DBConnectorTrack.id == DBResolutionNegative.connector_track_id)
                & actively_suppressing(),
            )
            .where(DBTrackMapping.id.is_(None), DBResolutionNegative.id.is_(None))
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
            .where(DBTrackMapping.user_id == user_id, live_only(DBTrackMapping))
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
            live_only(DBTrackMapping),
            text("(confidence_evidence->>'final_score')::numeric < 100"),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()
