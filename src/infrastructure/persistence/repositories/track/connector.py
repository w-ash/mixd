"""Manages track connections between internal database and external music services.

Handles track ingestion from Spotify, Last.fm, and other music platforms, maps external
tracks to canonical internal tracks, and stores service-specific metadata and IDs.
"""

# Lazy import cycle (mapper.py → this module for set_primary_mapping) handled by TYPE_CHECKING guard

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import NamedTuple, cast, overload, override
from uuid import UUID, uuid7

from attrs import define, field
from psycopg.errors import UniqueViolation
from sqlalchemy import (
    ColumnElement,
    Integer,
    Numeric,
    String,
    case,
    column,
    func,
    select,
    text,
    tuple_,
    update,
    values,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID, insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from structlog.stdlib import BoundLogger

from src.config import get_logger
from src.config.constants import DenormalizedTrackColumns, MappingOrigin, MatchMethod
from src.domain.entities import Artist, ConnectorTrack, Track, TrackMapping
from src.domain.entities.match_review import MatchReview
from src.domain.entities.shared import JsonDict, JsonValue
from src.domain.entities.track_mapping import SupersessionReason
from src.domain.exceptions import NotFoundError
from src.domain.matching.isrc_validation import (
    assess_isrc_match_reliability,
    compute_duration_diff_ms,
)
from src.domain.matching.recording_identity import (
    RecordingDescription,
    describe_track,
    describes_same_recording,
    identity_key,
)
from src.domain.matching.types import RawProviderMatch
from src.domain.repositories.connector import (
    ConnectorMappingSpec,
    FullMappingInfo,
    IsrcCollisionSpec,
    MatchMethodStatRow,
    PrimaryMappingDetail,
)
from src.domain.repositories.resolution import (
    ResolutionDecision,
    ResolutionRecorderProtocol,
    SupersessionEdge,
)
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBMatchReview,
    DBResolutionNegative,
    DBTrack,
    DBTrackMapping,
)
from src.infrastructure.persistence.database.live_rows import (
    INCLUDE_SUPERSEDED,
    expire_mapping_identity,
    live_only,
)
from src.infrastructure.persistence.repositories._shared.connector_tracks import (
    build_connector_track_row,
    extract_db_artist_names,
)
from src.infrastructure.persistence.repositories.base_repo import (
    BaseRepository,
    rows_affected,
)
from src.infrastructure.persistence.repositories.mappers import BaseModelMapper
from src.infrastructure.persistence.repositories.repo_decorator import db_operation
from src.infrastructure.persistence.repositories.resolution import actively_suppressing
from src.infrastructure.persistence.repositories.track.core import TrackRepository
from src.infrastructure.services.resolution_recorder import ResolutionRecorder

logger = get_logger(__name__)


def _evidence_score(evidence: JsonDict | None) -> float | None:
    """The matcher's raw final score out of stored evidence, when it recorded one."""
    if not evidence:
        return None
    score = evidence.get("final_score")
    return float(score) if isinstance(score, (int, float)) else None


# Confidence-band thresholds for the matching-health distribution (mirror SQL
# pack Q1): reject <50, review 50-84, accept 85-99, certain =100.
_BAND_REVIEW_MIN = 50
_BAND_REVIEW_MAX = 84
_BAND_ACCEPT_MIN = 85
_BAND_ACCEPT_MAX = 99
_CONFIDENCE_CERTAIN = 100

# ``assert_mappings`` races another writer on the same live key: ON CONFLICT
# guarantees atomic insert-or-update, but the branch where the conflicting row
# is superseded mid-statement is undocumented PostgreSQL internals, so a unique
# violation is retried rather than trusted away. ``db_operation`` does not
# retry anything.
_ASSERT_RETRY_ATTEMPTS = 3

# Columns an asserted mapping row may carry, with the defaults applied when a
# caller omits them. Every row in one INSERT must present the same key set.
_ASSERT_DEFAULTS: dict[str, object] = {
    "match_method": "",
    "confidence": 0,
    "confidence_evidence": None,
    "origin": MappingOrigin.AUTOMATIC,
    "is_primary": False,
}
_ASSERT_REQUIRED = ("user_id", "track_id", "connector_track_id", "connector_name")

# The live-identity key — the columns behind ``uq_track_mappings_live_connector``.
_LIVE_MAPPING_KEY = ("user_id", "connector_track_id", "connector_name")


@define(slots=True)
class _IdentityReuseIndex:
    """The canonicals one ingest batch may reuse instead of minting a twin.

    Two populations, one lookup: the canonicals already in the database that
    the title+artist probe proposed, and the ones this same batch has just
    created. The second is not an optimisation — a playlist can carry an
    original and its remaster with neither yet known to the database, and
    without leaders accumulating here the batch would mint both.

    Keyed by :func:`identity_key`, the equality partition
    :func:`describes_same_recording` induces, so a lookup only ever has to
    price the one bucket that could possibly answer. The predicate still rules
    on every hit — the key is necessary, not sufficient.
    """

    # Identifiers whose group may reuse or become a leader. Everything else is
    # excluded upstream by ``_build_identity_reuse_index`` and stays out of
    # both roles.
    eligible: set[str]
    by_key: dict[tuple[str, str], Track]

    def find(self, description: RecordingDescription) -> Track | None:
        """The canonical that already describes this recording, if any."""
        candidate = self.by_key.get(identity_key(description))
        if candidate is None:
            return None
        if not describes_same_recording(describe_track(candidate), description):
            return None
        return candidate

    def remember(self, description: RecordingDescription, track: Track) -> None:
        """Offer a freshly created canonical as a leader for later groups.

        First writer keeps the bucket: chunk order decides, so the earliest
        group to describe a recording owns the canonical every later one folds
        onto.
        """
        _ = self.by_key.setdefault(identity_key(description), track)


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


class PrimacyRestoration(NamedTuple):
    """A (track, connector track) pair whose primary mapping was just retired.

    Supersession clears the incumbent's ``is_primary`` — two live primaries for
    one track/connector would violate ``uq_primary_mapping``, and a retired row
    holding primacy is a ghost the denormalized id columns still follow. The
    successor therefore has to inherit it, and this is the batch of pairs that
    need it re-asserted.
    """

    track_id: UUID
    connector_name: str
    connector_track_id: UUID


@define(frozen=True, slots=True)
class AssertedMappingRow:
    """One row ``assert_mappings`` actually wrote this batch — new or successor.

    Populated in ``_assert_once`` straight from ``prepared`` (the repository's
    own normalised insert rows, keyed by the pre-generated ``id``), never
    re-read from the database. ``_record_assertion`` groups over these to emit
    events instead of re-querying: within one transaction, the row this
    session just inserted already *is* what a same-transaction SELECT would
    return.
    """

    id: UUID
    user_id: str
    track_id: UUID
    connector_track_id: UUID
    connector_name: str
    confidence: int
    origin: str
    confidence_evidence: JsonDict | None


@define(frozen=True, slots=True)
class MappingAssertion:
    """Outcome of one ``assert_mappings`` batch.

    ``superseded`` maps each retired mapping to the successor that replaced it
    — the edges a resolution event needs (v0.10.2 Stage 3 consumes this; no
    events are emitted here).

    ``primacy_restorations`` names the pairs whose retired incumbent had been
    primary, for the caller that owns external identifiers to re-promote.

    ``vacated_tracks`` names the (track, connector) pairs a supersession left
    without a primary because the successor landed on a *different* track. The
    restoration above only re-promotes on the successor's track; the track the
    mapping departed keeps a stale denormalized ``spotify_id``/``mbid`` and no
    primary at all — the FM4d drift migration 044's pre-pass had to repair on
    366 production rows. These need the read-path healer, not a re-promotion:
    the identifier they used to point at now belongs to someone else.

    ``written`` carries the full row — id, owner, track, connector identity,
    confidence, origin, evidence — for every mapping that landed live this
    batch (``created`` plus ``superseded.values()``). It is what
    ``_record_assertion`` sources its events from.

    ``reason`` is the reason stamped on every retired row this batch produced,
    so event emission agrees with the column. Without it the supersession
    events always read ``rematch`` while the rows themselves said ``manual``.
    """

    created: tuple[UUID, ...] = ()
    touched: tuple[UUID, ...] = ()
    superseded: Mapping[UUID, UUID] = field(factory=dict[UUID, UUID])
    primacy_restorations: tuple[PrimacyRestoration, ...] = ()
    vacated_tracks: tuple[tuple[UUID, str], ...] = ()
    written: tuple[AssertedMappingRow, ...] = ()
    reason: SupersessionReason = "rematch"


def _is_unique_violation(error: IntegrityError) -> bool:
    """True for SQLSTATE 23505 — psycopg3 maps unique_violation to this class."""
    return isinstance(error.orig, UniqueViolation)


class TrackMappingRepository(BaseRepository[DBTrackMapping, TrackMapping]):
    """Manages track-to-service mapping storage and retrieval."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize with database session and data mapper."""
        super().__init__(
            session=session,
            model_class=DBTrackMapping,
            mapper=TrackMappingMapper(),
        )

    @db_operation("assert_mappings")
    async def assert_mappings(
        self,
        rows: Sequence[Mapping[str, object]],
        *,
        reason: SupersessionReason = "rematch",
    ) -> MappingAssertion:
        """Assert a batch of mappings append-only, superseding what they replace.

        Replaces ``bulk_upsert`` for mappings, which overwrote the identity
        decision in place and left no trace of what was believed before. Three
        outcomes per row, decided by the database rather than a read-modify-write:

        - **new key** → inserted live;
        - **same key, same decision** → freshness touch only (``last_seen_at``
          moves, no new row — the no-churn rule);
        - **same key, different decision** → the incumbent is retired
          (``superseded_at`` / ``supersession_reason`` / ``superseded_by_id``)
          and the successor is inserted live.

        Two statements in one transaction: an ``ON CONFLICT … DO UPDATE``
        restating the partial index predicate (PostgreSQL requires it to infer
        a partial unique index), then a plain insert of exactly the successors
        the first statement named — those keys are free by then, because their
        predecessors left the partial index the moment they were superseded.
        """
        if not rows:
            return MappingAssertion(reason=reason)

        prepared = self._deduplicate_batch(
            [self._prepare_assert_row(row) for row in rows],
            list(_LIVE_MAPPING_KEY),
            label="assert_mappings",
        )

        remaining = _ASSERT_RETRY_ATTEMPTS
        while True:
            remaining -= 1
            try:
                # Savepoint per attempt: a unique violation poisons the
                # transaction, so a retry needs a clean point to resume from.
                async with self.session.begin_nested():
                    return await self._assert_once(prepared, reason)
            except IntegrityError as error:
                if remaining <= 0 or not _is_unique_violation(error):
                    raise
                logger.warning(
                    "assert_mappings_conflict_retry",
                    remaining=remaining,
                    rows=len(prepared),
                )

    @staticmethod
    def _prepare_assert_row(row: Mapping[str, object]) -> dict[str, object]:
        """Normalise one caller row: uniform key set, timestamps, successor id.

        The ``id`` is pre-generated because it does double duty — it is the id
        of the row when the key is new, and the ``EXCLUDED.id`` the conflicting
        incumbent stores in ``superseded_by_id`` when the decision changed.
        """
        now = datetime.now(UTC)
        prepared: dict[str, object] = {
            "id": uuid7(),
            **{key: row[key] for key in _ASSERT_REQUIRED},
            **{key: row.get(key, default) for key, default in _ASSERT_DEFAULTS.items()},
            "last_seen_at": row.get("last_seen_at") or now,
            "created_at": now,
            "updated_at": now,
        }
        return prepared

    async def _live_primary_keys(
        self, prepared: list[dict[str, object]]
    ) -> set[tuple[str, UUID, str]]:
        """Which of this batch's live keys currently hold the primary mapping.

        Read *before* the upsert because the upsert destroys the answer: a
        superseded row's ``is_primary`` is cleared in the same statement, and
        ``RETURNING`` reports post-update values.
        """
        model = self.model_class
        keys = [
            (
                cast("str", row["user_id"]),
                cast("UUID", row["connector_track_id"]),
                cast("str", row["connector_name"]),
            )
            for row in prepared
        ]
        result = await self.session.execute(
            select(model.user_id, model.connector_track_id, model.connector_name).where(
                tuple_(
                    model.user_id, model.connector_track_id, model.connector_name
                ).in_(keys),
                model.is_primary.is_(True),
                live_only(model),
            )
        )
        return set(result.tuples().all())

    async def _assert_once(
        self, prepared: list[dict[str, object]], reason: SupersessionReason
    ) -> MappingAssertion:
        """One attempt: upsert-or-supersede, then insert the named successors."""
        model = self.model_class
        primary_keys = await self._live_primary_keys(prepared)
        insert_stmt = pg_insert(model).values(prepared)
        excluded = insert_stmt.excluded
        # The identity of a mapping *decision*: track, confidence, method,
        # origin. A change to any of them retires the incumbent; every other
        # field (evidence, freshness, primacy) is drift and never supersedes,
        # or an enrichment refresh would grow the chain (ROR's rule).
        # ROW(...) IS DISTINCT FROM ROW(...) keeps the comparison NULL-safe
        # across the whole tuple, which ``!=`` would not.
        decision_differs = tuple_(
            model.track_id, model.confidence, model.match_method, model.origin
        ).is_distinct_from(
            tuple_(
                excluded.track_id,
                excluded.confidence,
                excluded.match_method,
                excluded.origin,
            )
        )
        # else_=None on all three is what keeps an unchanged row's supersession
        # columns correct: the conflict target is the *live* partial index, so
        # a row reached here is live by construction and its three columns are
        # already NULL — writing NULL preserves them instead of clobbering.
        upsert = (
            insert_stmt
            .on_conflict_do_update(
                index_elements=list(_LIVE_MAPPING_KEY),
                index_where=model.superseded_at.is_(None),
                set_={
                    "last_seen_at": excluded.last_seen_at,
                    "updated_at": excluded.updated_at,
                    # Evidence refreshes without superseding: it is the
                    # *explanation* of a decision, not the decision. Freezing
                    # it left a live mapping permanently explained by the first
                    # scoring run that ever produced it, which is precisely the
                    # provenance the column exists to carry.
                    #
                    # But only on a freshness touch. When the decision changed,
                    # this row is about to become history, and history explained
                    # by the evidence for a *different* decision is worse than
                    # no explanation at all — the successor row carries the new
                    # evidence, inserted from ``prepared`` by the second
                    # statement, so nothing is lost by leaving the retired row
                    # holding the evidence that actually justified it.
                    "confidence_evidence": case(
                        (decision_differs, model.confidence_evidence),
                        else_=excluded.confidence_evidence,
                    ),
                    "superseded_at": case((decision_differs, func.now()), else_=None),
                    "superseded_by_id": case(
                        (decision_differs, excluded.id), else_=None
                    ),
                    "supersession_reason": case((decision_differs, reason), else_=None),
                    # A retired row must not keep primacy: it would collide
                    # with its successor under ``uq_primary_mapping`` and the
                    # denormalized id columns would follow a ghost. else_ keeps
                    # an unchanged (merely touched) row exactly as it was.
                    "is_primary": case(
                        (decision_differs, False), else_=model.is_primary
                    ),
                },
            )
            # track_id is the *predecessor's* — supersession does not rewrite
            # it, so this is the track the mapping is leaving behind.
            .returning(model.id, model.superseded_by_id, model.track_id)
            .execution_options(synchronize_session=False)
        )
        result = await self.session.execute(upsert)
        affected: Sequence[tuple[UUID, UUID | None, UUID]] = result.tuples().all()

        inserted_ids = {row["id"] for row in prepared}
        created = tuple(mid for mid, _, _ in affected if mid in inserted_ids)
        touched = tuple(
            mid
            for mid, successor, _ in affected
            if mid not in inserted_ids and successor is None
        )
        superseded = {
            mid: successor for mid, successor, _ in affected if successor is not None
        }
        departed_from = {
            successor: track_id
            for _, successor, track_id in affected
            if successor is not None
        }
        written_ids = set(created) | set(superseded.values())
        written = tuple(
            AssertedMappingRow(
                id=cast("UUID", row["id"]),
                user_id=cast("str", row["user_id"]),
                track_id=cast("UUID", row["track_id"]),
                connector_track_id=cast("UUID", row["connector_track_id"]),
                connector_name=cast("str", row["connector_name"]),
                confidence=cast("int", row["confidence"]),
                origin=cast("str", row["origin"]),
                confidence_evidence=cast("JsonDict | None", row["confidence_evidence"]),
            )
            for row in prepared
            if row["id"] in written_ids
        )

        restorations: tuple[PrimacyRestoration, ...] = ()
        vacated: tuple[tuple[UUID, str], ...] = ()
        if superseded:
            successors = [
                row for row in prepared if row["id"] in set(superseded.values())
            ]
            _ = await self.session.execute(pg_insert(model).values(successors))
            was_primary = [
                row
                for row in successors
                if (
                    row["user_id"],
                    row["connector_track_id"],
                    row["connector_name"],
                )
                in primary_keys
            ]
            restorations = tuple(
                PrimacyRestoration(
                    track_id=cast("UUID", row["track_id"]),
                    connector_name=cast("str", row["connector_name"]),
                    connector_track_id=cast("UUID", row["connector_track_id"]),
                )
                for row in was_primary
            )
            # A re-score that also moves the mapping to another track leaves
            # the old one with no primary and a denormalized id pointing at an
            # identifier it no longer owns. Only the successor's track is
            # re-promoted above, so name the departed track for the healer.
            vacated = tuple({
                (departed, cast("str", row["connector_name"]))
                for row in was_primary
                if (departed := departed_from.get(cast("UUID", row["id"]))) is not None
                and departed != row["track_id"]
            })
            # Core statements do not touch the identity map, so a session that
            # already loaded these rows would keep serving the retired one.
            expire_mapping_identity(
                self.session,
                mapping_ids=(*superseded.keys(), *superseded.values()),
                track_ids=[cast("UUID", row["track_id"]) for row in successors],
            )

        return MappingAssertion(
            created=created,
            touched=touched,
            superseded=superseded,
            primacy_restorations=restorations,
            vacated_tracks=vacated,
            written=written,
            reason=reason,
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
        self, mappings: list[ConnectorMappingSpec]
    ) -> list[Track]:
        """Link existing internal tracks to external service IDs with confidence scores.

        Pipeline: build connector-track rows → bulk upsert → build mapping rows →
        drop rows that would overwrite manual overrides → assert mappings
        (append-only: a changed decision supersedes rather than overwrites) →
        elect the primaries the specs asked for.

        Election reads ``ConnectorMappingSpec.primary`` off the specs
        themselves, re-electing and syncing the denormalized fast-path column
        in a fixed handful of statements for the whole batch instead of four
        per row. It is the only primary-election path this method has: mapping
        one track is ``map_track_to_connector``, which is a one-spec call to
        exactly this.

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
        connector_id_map = await self._upsert_connector_tracks(
            self._build_connector_track_rows(mappings)
        )
        mapping_rows = await self._filter_manual_overrides(
            self._build_mapping_rows(mappings, connector_id_map)
        )

        if mapping_rows:
            assertion = await self.mapping_repo.assert_mappings(mapping_rows)
            await self._record_assertion(assertion)
            await self._restore_superseded_primaries(assertion)

        promote_to_primary = [
            (spec.track.id, spec.connector, spec.connector_id)
            for spec in mappings
            if spec.primary and spec.track.id
        ]
        if promote_to_primary:
            _ = await self._batch_ensure_primary_mappings_by_external_id(
                promote_to_primary, connector_id_map=connector_id_map
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

    async def _restore_superseded_primaries(
        self,
        assertion: MappingAssertion,
    ) -> None:
        """Re-promote the successors of mappings that had been primary.

        Supersession strips the incumbent's primacy, so without this a re-score
        of a track's primary mapping leaves it with a live mapping and no
        primary at all — the denormalized ``spotify_id``/``mbid`` fast path
        stops resolving and the review queue's provenance reads as absent.

        When the successor landed on a *different* track, the track it left
        needs the opposite treatment: not a re-promotion (the identifier it
        held now belongs elsewhere) but the read-path healer, which promotes a
        surviving sibling or clears the denormalized column.

        ``PrimacyRestoration`` already carries the connector track's internal
        UUID, so this goes straight to ``_batch_ensure_primary_mappings`` —
        no need to round-trip it through the external string id and back. That
        path fills a vacancy and never deposes, so a successor arriving on a
        track that already has a primary (pinned or automatic) leaves it be:
        the restoration exists to repair the slot supersession emptied, not to
        claim one that is still occupied.

        The vacated-pairs loop above stays per-pair on purpose. It is 0-2
        entries in practice, and each one needs
        ``ensure_primary_for_connector``'s selection policy — pick the
        highest-confidence survivor, or clear the denormalized column when
        there is none — which is a decision per track, not a set operation.
        """
        for track_id, connector_name in assertion.vacated_tracks:
            await self.ensure_primary_for_connector(track_id, connector_name)

        if not assertion.primacy_restorations:
            return
        primaries = [
            (
                restoration.track_id,
                restoration.connector_name,
                restoration.connector_track_id,
            )
            for restoration in assertion.primacy_restorations
        ]
        _ = await self._batch_ensure_primary_mappings(primaries)

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
                live_only(DBTrackMapping),
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
        """Link an existing internal track to an external service ID.

        One mapping is the degenerate case of a batch of them, all the way
        down to the election: ``auto_set_primary`` is the spec's ``primary``
        flag, so the promotion, the vacancy rules and the FM4d denormalized-id
        sync are the batch path's and there is no second election chain to
        keep in agreement with it.

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

        # 2.6. And the canonicals a *name* match would reuse, for the groups
        # the ISRC step left to create one. A remaster never shares its
        # original's ISRC, so without this the two releases each mint their own
        # canonical even though their normalized artist+title already collide
        # (v0.10.3 finding C4).
        reuse_index = await self._build_identity_reuse_index(
            tracks_by_identifier,
            connector_track_lookup,
            existing_mapping_by_ct_id,
            isrc_owners,
            user_id=user_id,
        )

        # 3. Create or find a domain track per unique identifier, collecting the
        # mapping rows that new tracks need.
        domain_tracks: list[Track] = []
        track_mappings_data: list[dict[str, object]] = []
        reuse_primaries: list[tuple[UUID, str, UUID]] = []
        for identifier, track_group in tracks_by_identifier.items():
            domain_track, mapping_row, reused = await self._ingest_one_group(
                connector,
                identifier,
                track_group,
                connector_track_lookup,
                existing_mapping_by_ct_id,
                isrc_owners,
                reuse_index,
                user_id=user_id,
            )
            # Add the domain track for each occurrence in the playlist
            domain_tracks.extend(domain_track for _ in track_group)
            if mapping_row is not None:
                track_mappings_data.append(mapping_row)
            if reused:
                reuse_primaries.append((
                    domain_track.id,
                    connector,
                    connector_track_lookup[identifier].id,
                ))

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

        # 4.5. A reused canonical elects nothing: step 4 keys its election off
        # ``connector_track_identifiers``, which for a reuse still names the id
        # the canonical already carried, so that id keeps primacy and the new
        # mapping is correctly a secondary alias. The one case that needs
        # filling is a canonical with no mapping for this connector at all — a
        # Last.fm-born track a Spotify playlist has just matched — which would
        # otherwise be left with a live mapping and no primary. Vacancy-fill,
        # never a deposition (see ``_batch_ensure_primary_mappings``).
        if reuse_primaries:
            _ = await self._batch_ensure_primary_mappings(reuse_primaries)

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
        now = datetime.now(UTC)
        connector_track_data: list[dict[str, object]] = [
            build_connector_track_row(
                connector,
                identifier,
                title=group[-1].title,
                artist_names=[a.name for a in group[-1].artists],
                album=group[-1].album,
                duration_ms=group[-1].duration_ms,
                release_date=group[-1].release_date,
                isrc=group[-1].isrc,
                raw_metadata=group[-1].raw_metadata,
                last_updated=now,
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

    @staticmethod
    def _describe_connector_track(track: ConnectorTrack) -> RecordingDescription:
        """A connector payload as the same-recording question sees it."""
        return RecordingDescription(
            title=track.title,
            artist=track.artists[0].name if track.artists else "",
            duration_ms=track.duration_ms,
        )

    async def _build_identity_reuse_index(
        self,
        tracks_by_identifier: dict[str, list[ConnectorTrack]],
        connector_track_lookup: dict[str, ConnectorTrack],
        existing_mapping_by_ct_id: dict[UUID, TrackMapping],
        isrc_owners: dict[str, Track],
        *,
        user_id: str,
    ) -> _IdentityReuseIndex:
        """Probe for canonicals that already describe this batch's new tracks.

        One query for the batch, and only for the groups that would otherwise
        create a canonical. The exclusions are the point:

        - an **already-mapped** connector track has resolved; nothing is created
          for it and there is nothing to fold.
        - a group whose **ISRC has an owner** is decided by the ISRC path, and
          decided either way. Non-suspect, ``save_track`` upserts onto the owner
          — a name match must not get to nominate a *different* canonical first.
          Suspect, v0.8.18 deliberately mints a distinct canonical without the
          contested ISRC and queues a review; folding it onto some third
          canonical on a name match would settle by the back door exactly what
          that review exists to put in front of a person.
        - a group with **no title or no artist** cannot be compared at all.
        """
        eligible = {
            identifier
            for identifier, group in tracks_by_identifier.items()
            if connector_track_lookup[identifier].id not in existing_mapping_by_ct_id
            and group[0].title
            and group[0].artists
            and group[0].artists[0].name
            and not (group[0].isrc and group[0].isrc in isrc_owners)
        }
        if not eligible:
            return _IdentityReuseIndex(eligible=eligible, by_key={})

        owners = await self.track_repo.find_tracks_by_title_artist(
            [
                (
                    tracks_by_identifier[identifier][0].title,
                    tracks_by_identifier[identifier][0].artists[0].name,
                )
                for identifier in sorted(eligible)
            ],
            user_id=user_id,
        )
        # Keyed by what the *found canonical* normalizes to, not by the probe
        # pair that surfaced it: the probe also answers on a
        # parenthetical-stripped form, and a candidate reached that way keys
        # differently from the payload — which is precisely the pairing
        # ``describes_same_recording`` refuses.
        by_key: dict[tuple[str, str], Track] = {}
        for candidate in owners.values():
            _ = by_key.setdefault(identity_key(describe_track(candidate)), candidate)
        return _IdentityReuseIndex(eligible=eligible, by_key=by_key)

    async def _ingest_one_group(
        self,
        connector: str,
        identifier: str,
        track_group: list[ConnectorTrack],
        connector_track_lookup: dict[str, ConnectorTrack],
        existing_mapping_by_ct_id: dict[UUID, TrackMapping],
        isrc_owners: dict[str, Track],
        reuse_index: _IdentityReuseIndex,
        *,
        user_id: str,
    ) -> tuple[Track, dict[str, object] | None, bool]:
        """Resolve one unique connector identifier to a domain track.

        Returns the domain track, the mapping row it needs (``None`` when an
        existing mapping was reused), and whether the track is a canonical this
        group reused rather than created.
        """
        # Tracks in a group are identical except playlist position
        representative_track = track_group[0]
        connector_track_id = connector_track_lookup[identifier].id
        mapping = existing_mapping_by_ct_id.get(connector_track_id)

        if mapping:
            domain_track = await self.track_repo.get_by_id(mapping.track_id)
            logger.debug(
                f"Found existing track {mapping.track_id} for {connector}:{identifier}"
            )
            return domain_track, None, False

        description = self._describe_connector_track(representative_track)
        if identifier in reuse_index.eligible:
            reused = self._plan_identity_reuse(
                connector, identifier, description, connector_track_id, reuse_index
            )
            if reused is not None:
                return reused[0], reused[1], True

        domain_track, mapping_row = await self._create_track_with_mapping_row(
            connector, representative_track, connector_track_id, isrc_owners, user_id
        )
        if identifier in reuse_index.eligible:
            reuse_index.remember(description, domain_track)
        return domain_track, mapping_row, False

    def _plan_identity_reuse(
        self,
        connector: str,
        identifier: str,
        description: RecordingDescription,
        connector_track_id: UUID,
        reuse_index: _IdentityReuseIndex,
    ) -> tuple[Track, dict[str, object]] | None:
        """Map this identifier onto a canonical that already holds the recording.

        The same three-part gate the Spotify inward resolver applies before it
        creates: normalized artist+title equality, agreeing durations (both in
        ``describes_same_recording``), and the evaluation service's own accept.
        The matcher is asked last and only to price a decision already made —
        Fellegi-Sunter saturates on an exact artist+title agreement, so its
        confidence separates nothing here, but taking the number from the model
        keeps the mapping's confidence derived rather than invented.

        Returns the canonical and its mapping row, or ``None`` to create.
        """
        from src.config import create_evaluation_service

        candidate = reuse_index.find(description)
        if candidate is None:
            return None

        match = create_evaluation_service().evaluate_single_match(
            candidate,
            RawProviderMatch(
                connector_id=identifier,
                match_method=MatchMethod.CANONICAL_REUSE,
                service_data={
                    "title": description.title,
                    "artist": description.artist,
                    "duration_ms": description.duration_ms,
                },
            ),
            connector,
        )
        if not match.success:
            return None

        logger.info(
            f"Identity reuse: {connector}:{identifier} describes the recording "
            f"canonical {candidate.id} already holds",
            connector=connector,
            connector_id=identifier,
            track_id=candidate.id,
            confidence=match.confidence,
        )
        mapping_row: dict[str, object] = {
            "user_id": candidate.user_id,
            "track_id": candidate.id,
            "connector_track_id": connector_track_id,
            "connector_name": connector,
            "match_method": MatchMethod.CANONICAL_REUSE,
            "confidence": match.confidence,
            # The election in step 4.5 decides primacy: a canonical that
            # already has a primary for this connector keeps it.
            "is_primary": False,
        }
        return candidate, mapping_row

    async def _touch_last_seen(self, mapping_ids: list[UUID]) -> None:
        """Bulk-stamp last_seen_at on re-encountered mappings.

        Replaces the pre-v0.8.18 bump-to-100: re-encountering a connector
        track proves it exists, not that the canonical match was right, so
        confidence is never touched here.
        """
        _ = await self.session.execute(
            update(DBTrackMapping)
            .where(DBTrackMapping.id.in_(mapping_ids), live_only(DBTrackMapping))
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

    async def _record_assertion(self, assertion: MappingAssertion) -> None:
        """Emit the events an ``assert_mappings`` batch just earned.

        This and ``_create_mappings_and_set_primaries`` are the only two places
        that assert mappings, which is why they are the only two that emit
        ``accepted``: every accept path in the codebase — the matching
        pipeline, both inward resolvers, review-accept, orphan creation, relink
        — funnels through one of them, so no accept can reach the database
        without its event.

        A *touched* mapping earns nothing: re-encountering an unchanged
        decision is freshness, not a decision (the same reason it never
        re-scores confidence).
        """
        if not assertion.written:
            return
        # Event data comes from ``assertion.written`` — rows ``_assert_once``
        # copied out of ``prepared``, the repository's own normalised insert
        # rows — never from the caller's raw input: a batch may span users,
        # and a spec dropped by the manual-override filter (upstream of
        # ``assert_mappings``) never reaches ``prepared`` and so never reaches
        # here either. This used to re-SELECT the same rows back from the
        # database instead; that was redundant, not safer. This method and
        # the INSERT that wrote these rows run on the same ``AsyncSession``
        # inside one open transaction with no intervening commit, so the row
        # just inserted already *is* what a same-transaction SELECT would
        # see — a concurrent writer targeting the same live key blocks on the
        # row lock until this transaction commits, so there was never a
        # second answer the SELECT could have found.
        by_user: dict[str, list[ResolutionDecision]] = {}
        successor_owners: dict[UUID, tuple[str, str]] = {}
        for row in assertion.written:
            successor_owners[row.id] = (row.user_id, row.connector_name)
            by_user.setdefault(row.user_id, []).append(
                ResolutionDecision(
                    # A user-pinned mapping is an override, not an automatic
                    # accept, and the log has to be able to tell them apart —
                    # "why does my library believe this" answers differently.
                    event_type="manual_override"
                    if row.origin == MappingOrigin.MANUAL_OVERRIDE
                    else "accepted",
                    connector_name=row.connector_name,
                    connector_track_id=row.connector_track_id,
                    track_id=row.track_id,
                    resulting_mapping_id=row.id,
                    confidence=row.confidence,
                    score=_evidence_score(row.confidence_evidence),
                    zone="accept",
                )
            )

        recorder = self._resolution_recorder()
        for user_id, decisions in by_user.items():
            _ = await recorder.record(decisions, user_id=user_id)
        await self._record_supersession_edges(
            recorder,
            assertion.superseded,
            successor_owners,
            reason=assertion.reason,
        )

    @staticmethod
    async def _record_supersession_edges(
        recorder: ResolutionRecorderProtocol,
        edges: Mapping[UUID, UUID],
        successor_owners: dict[UUID, tuple[str, str]],
        *,
        reason: SupersessionReason,
    ) -> None:
        """Emit one ``superseded`` event per edge; the seam groups by owner.

        A successor absent from ``successor_owners`` was dropped by the
        manual-override filter upstream (the same guard ``_record_assertion``
        applies via ``assertion.written``) and is skipped — there is no live
        row left to describe an event about.

        ``reason`` comes from the assertion that produced these edges rather
        than defaulting: a relink asserts with ``manual``, and an event saying
        ``rematch`` about a row whose ``supersession_reason`` column says
        ``manual`` is the log contradicting the data it exists to explain.
        """
        supersession_edges = [
            SupersessionEdge(
                predecessor_id=predecessor,
                successor_id=successor,
                user_id=owner[0],
                connector_name=owner[1],
            )
            for predecessor, successor in edges.items()
            if (owner := successor_owners.get(successor)) is not None
        ]
        if not supersession_edges:
            return
        _ = await recorder.record_supersessions(supersession_edges, reason=reason)

    def _resolution_recorder(self) -> ResolutionRecorderProtocol:
        """The identity write seam bound to this repository's transaction."""
        return ResolutionRecorder(self.session)

    async def _create_mappings_and_set_primaries(
        self,
        connector: str,
        domain_tracks: list[Track],
        track_mappings_data: list[dict[str, object]],
    ) -> None:
        """Assert new mappings, then set one primary per track-connector pair."""
        if not track_mappings_data:
            return

        assertion = await self.mapping_repo.assert_mappings(track_mappings_data)
        await self._record_assertion(assertion)

        primaries_set: dict[UUID, str] = {}
        for track in domain_tracks:
            if track.id not in primaries_set:
                cid = track.connector_track_identifiers.get(connector)
                if cid:
                    primaries_set[track.id] = cid

        primaries = [(tid, connector, cid) for tid, cid in primaries_set.items()]
        if primaries:
            _ = await self._batch_ensure_primary_mappings_by_external_id(primaries)

    async def _clear_denormalized_id(self, track_id: UUID, connector: str) -> None:
        """Clear denormalized ID column on DBTrack when no mappings remain for a connector."""
        column_name = DenormalizedTrackColumns.COLUMN_MAP.get(connector)
        if column_name:
            await self.session.execute(
                update(DBTrack)
                .where(DBTrack.id == track_id)
                .values(**{column_name: None})
            )

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
        await self._record_assertion(assertion)

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
                live_only(DBTrackMapping),
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

        Promotes the highest-confidence mapping if none is primary,
        or clears the denormalized ID if no mappings remain.
        """
        remaining = await self._get_remaining_mappings(track_id, connector_name)
        if not remaining:
            await self._clear_denormalized_id(track_id, connector_name)
            return
        if any(m.is_primary for m in remaining):
            return
        # Choosing the winner is this method's whole job; promoting it is the
        # batch path's, and going through it is what keeps the FM4d rule — sync
        # the denormalized column ONLY if the promotion actually landed — in
        # one place. A second copy here is the disagreement migration 044's
        # pre-pass had to repair on 366 production rows, written again.
        #
        # The batch path only fills a vacancy, which is precisely the state the
        # check above has just established this track to be in.
        _ = await self._batch_ensure_primary_mappings([
            (track_id, connector_name, remaining[0].connector_track_id)
        ])

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

    @db_operation("queue_isrc_collision_reviews")
    async def queue_isrc_collision_reviews(
        self,
        collisions: Sequence[IsrcCollisionSpec],
        connector: str,
        *,
        user_id: str,
    ) -> int:
        """``queue_isrc_collision_review`` for a whole batch, in four statements.

        The per-item version costs ~7 round trips each; an import chunk can
        carry several. Same semantics, including the any-status dedupe that
        keeps a re-import from resurrecting a rejected review.

        Returns:
            Number of reviews queued (already-reviewed collisions excluded).
        """
        from src.config import create_evaluation_service
        from src.infrastructure.persistence.repositories.match_review import (
            MatchReviewRepository,
        )

        if not collisions:
            return 0

        ct_ids = await self.ensure_connector_tracks(
            connector,
            [
                {
                    "connector_id": collision.connector_id,
                    "title": collision.service_data.get("title", ""),
                    "artists": collision.service_data.get("artists", []),
                    "duration_ms": collision.service_data.get("duration_ms"),
                    "isrc": collision.service_data.get("isrc"),
                }
                for collision in collisions
            ],
        )

        candidates = [
            (collision, ct_uuid)
            for collision in collisions
            if (ct_uuid := ct_ids.get((connector, collision.connector_id))) is not None
        ]
        if not candidates:
            return 0

        # Any-status dedupe: re-imports must not resurrect rejected reviews.
        reviewed = await self.session.execute(
            select(DBMatchReview.track_id, DBMatchReview.connector_track_id).where(
                DBMatchReview.user_id == user_id,
                DBMatchReview.connector_name == connector,
                DBMatchReview.connector_track_id.in_([
                    ct_uuid for _, ct_uuid in candidates
                ]),
            )
        )
        already_reviewed = set(reviewed.tuples().all())

        evaluator = create_evaluation_service()
        reviews: list[MatchReview] = []
        for collision, ct_uuid in candidates:
            if (collision.owner.id, ct_uuid) in already_reviewed:
                continue
            match = evaluator.evaluate_single_match(
                collision.owner,
                RawProviderMatch(
                    connector_id=collision.connector_id,
                    match_method="isrc",
                    service_data=collision.service_data,
                ),
                connector,
            )
            reviews.append(
                MatchReview(
                    user_id=user_id,
                    track_id=collision.owner.id,
                    connector_name=connector,
                    connector_track_id=ct_uuid,
                    match_method=MatchMethod.ISRC_SUSPECT,
                    confidence=match.confidence,
                    match_weight=(
                        match.evidence.match_weight if match.evidence else 0.0
                    ),
                    confidence_evidence=match.evidence_dict,
                )
            )
            logger.warning(
                "isrc_collision_deferred",
                track_id=collision.owner.id,
                connector=connector,
                connector_id=collision.connector_id,
                isrc=collision.service_data.get("isrc"),
                confidence=match.confidence,
            )

        if not reviews:
            return 0
        _ = await MatchReviewRepository(self.session).create_reviews_batch(reviews)
        return len(reviews)

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
        # Step 1: Reset all live primaries for this track-connector pair
        _ = await self.session.execute(
            update(DBTrackMapping)
            .where(
                DBTrackMapping.track_id == track_id,
                DBTrackMapping.connector_name == connector_name,
                live_only(DBTrackMapping),
            )
            .values(is_primary=False)
        )

        # Step 2: Set the specified live mapping as primary
        result = await self.session.execute(
            update(DBTrackMapping)
            .where(
                DBTrackMapping.track_id == track_id,
                DBTrackMapping.connector_track_id == connector_track_id,
                live_only(DBTrackMapping),
            )
            .values(is_primary=True)
        )

        success = rows_affected(result) > 0
        if success:
            log.debug("Set primary mapping")
        else:
            log.warning("Failed to set primary mapping - no matching record found")
        return success

    async def _reset_primaries[T](self, primaries: list[tuple[UUID, str, T]]) -> None:
        """Clear ``is_primary`` on every live mapping for these track/connector pairs.

        One caller left: ``_batch_ensure_primary_mappings_by_external_id``,
        which deliberately re-elects the primary for every pair it is given.
        The generic type parameter survives that narrowing because the third
        element is still never read here — only the (track, connector) pair is.

        Its deliberate reset is what makes the promotion step's vacancy guard
        vacuously true for those pairs: nothing is primary by the time the
        promote runs, so "promote only into a vacancy" and "promote" coincide,
        and that path's behaviour is unchanged by the guard's introduction.

        Grouped by connector so each statement clears only the tracks that
        actually appear under *that* connector. Taking the cross-product of
        every track and every connector in the batch — one `IN` list against
        each connector in turn — demotes primaries no caller asked about and
        then promotes nothing back, leaving a track live but primary-less: the
        exact FM4d drift the promotion path exists to prevent. Inert while
        every caller passes a single-connector batch, which is why it has to be
        fixed here rather than trusted to stay that way — ``ConnectorMappingSpec``
        carries its connector per row, so the batch that spans two is legal
        input the moment someone assembles one.
        """
        by_connector: dict[str, list[UUID]] = defaultdict(list)
        for track_id, connector_name, _ in primaries:
            by_connector[connector_name].append(track_id)
        for connector_name, track_ids in by_connector.items():
            await self.session.execute(
                update(DBTrackMapping)
                .where(
                    DBTrackMapping.track_id.in_(track_ids),
                    DBTrackMapping.connector_name == connector_name,
                    live_only(DBTrackMapping),
                )
                .values(is_primary=False)
            )

    async def _promote_primaries_by_uuid(
        self,
        primaries: list[tuple[UUID, str, UUID]],
        *,
        external_by_ct_id: Mapping[UUID, str] | None = None,
    ) -> int:
        """Fill a vacant primary slot for each pair, in one statement.

        Each tuple is (track_id, connector_name, connector_track_id) — the
        connector track's internal database id.

        **Vacancy-fill, never a deposition.** The ``NOT EXISTS`` peer guard is
        what makes that true: a pair that already has a live primary is left
        exactly as it is, whether that primary is user-pinned
        (``manual_override``) or automatic. Same precedent as
        ``merge_mappings_to_track`` — "flipping an existing primary is not the
        merge's business" — and the same reasoning applies here: an arriving
        mapping's confidence is not a mandate to overrule a decision the track
        already holds. Same-track re-scoring is unaffected, because the
        upsert's ``decision_differs`` arm already cleared the predecessor's
        ``is_primary`` in an earlier statement, so the pair reads as vacant.

        **Deduplicated first, by (track_id, connector_name), first wins.**
        ``uq_primary_mapping`` admits one live primary per
        (user, track, connector), and the guard below is evaluated against the
        statement-start snapshot — it cannot see rows this same UPDATE is
        promoting. Two rows for one pair in one batch would therefore both pass
        the guard and collide.

        **``RETURNING`` is the "promotion landed" signal**, replacing the
        per-row ``rows_affected`` the loop version used: with one statement for
        the whole batch there is no per-row count left, and the FM4d rule needs
        to know exactly which pairs moved before it touches a denormalized id.
        """
        deduped: dict[tuple[UUID, str], tuple[UUID, str, UUID]] = {}
        for track_id, connector_name, ct_db_id in primaries:
            _ = deduped.setdefault(
                (track_id, connector_name), (track_id, connector_name, ct_db_id)
            )

        promotion_values = values(
            column("track_id", PGUUID(as_uuid=True)),
            column("connector_track_id", PGUUID(as_uuid=True)),
            name="promotion_values",
        ).data([(track_id, ct_db_id) for track_id, _, ct_db_id in deduped.values()])

        peer = aliased(DBTrackMapping)
        result = await self.session.execute(
            update(DBTrackMapping)
            .where(
                DBTrackMapping.track_id == promotion_values.c.track_id,
                DBTrackMapping.connector_track_id
                == promotion_values.c.connector_track_id,
                live_only(DBTrackMapping),
                ~select(peer.id)
                .where(
                    peer.user_id == DBTrackMapping.user_id,
                    peer.track_id == DBTrackMapping.track_id,
                    peer.connector_name == DBTrackMapping.connector_name,
                    peer.is_primary.is_(True),
                    live_only(peer),
                )
                .correlate(DBTrackMapping)
                .exists(),
            )
            .values(is_primary=True)
            .returning(
                DBTrackMapping.track_id,
                DBTrackMapping.connector_name,
                DBTrackMapping.connector_track_id,
            )
            .execution_options(synchronize_session=False)
        )
        promoted: list[tuple[UUID, str, UUID]] = list(result.tuples().all())

        if promoted:
            await self._sync_promoted_denormalized_ids(
                promoted, external_by_ct_id=external_by_ct_id
            )
        return len(promoted)

    async def _sync_promoted_denormalized_ids(
        self,
        promoted: list[tuple[UUID, str, UUID]],
        *,
        external_by_ct_id: Mapping[UUID, str] | None = None,
    ) -> None:
        """Sync the fast-path column for pairs whose promotion actually landed.

        FM4d rule — the denormalized id must move ONLY when the promotion
        landed (the caller's ``RETURNING`` rows, never its input), never
        unconditionally: a promotion that changes which row is primary
        without moving the column leaves ``tracks.spotify_id``/``mbid``
        describing a mapping that no longer holds primacy, the same
        disagreement migration 044's pre-pass had to repair on 366 rows.

        The external identifier *string* is what the column stores, while
        ``promoted`` only carries the connector track's UUID. Callers that came
        in by external id already hold the mapping and pass it; the rest pay a
        lookup, scoped to the promoted subset rather than every candidate.

        Written back one statement per denormalized *column* rather than one
        per row: the column name cannot be parameterised, so rows are grouped
        by it and each group joins its own VALUES list. A connector with no
        fast-path column is dropped by the ``COLUMN_MAP`` lookup — most of
        them have none, and nothing to sync is not a failure.
        """
        if external_by_ct_id is None:
            ct_ids = [ct_id for _, _, ct_id in promoted]
            result = await self.session.execute(
                select(
                    DBConnectorTrack.id, DBConnectorTrack.connector_track_identifier
                ).where(DBConnectorTrack.id.in_(ct_ids))
            )
            external_by_ct_id = dict(result.tuples().all())

        by_column: dict[str, list[tuple[UUID, str]]] = defaultdict(list)
        for track_id, connector_name, ct_id in promoted:
            column_name = DenormalizedTrackColumns.COLUMN_MAP.get(connector_name)
            external_id = external_by_ct_id.get(ct_id)
            if column_name and external_id:
                by_column[column_name].append((track_id, external_id))

        for column_name, pairs in by_column.items():
            identifier_values = values(
                column("track_id", PGUUID(as_uuid=True)),
                column("external_id", String()),
                name="denormalized_values",
            ).data(pairs)
            _ = await self.session.execute(
                update(DBTrack)
                .where(DBTrack.id == identifier_values.c.track_id)
                .values(**{column_name: identifier_values.c.external_id})
            )

    @db_operation("batch_ensure_primary_mappings")
    async def _batch_ensure_primary_mappings(
        self,
        primaries: list[tuple[UUID, str, UUID]],
    ) -> int:
        """Fill a vacant primary slot for multiple track-connector pairs in bulk.

        Replaces per-track ensure_primary_mapping() loops with fewer queries.
        Each tuple is (track_id, connector_name, connector_track_id) — the
        connector track's internal database id, already known to the caller
        (``_restore_superseded_primaries`` reads it straight off
        ``PrimacyRestoration`` — no need to look it up).
        ``_batch_ensure_primary_mappings_by_external_id`` is the sibling entry
        point for the one caller that only holds the external string id.

        **Vacancy-fill only.** This used to clear ``is_primary`` across every
        live mapping for the pair before promoting, which made a supersession
        restoration into a deposition: a mapping re-asserted onto a track that
        already had a primary — including a user-pinned ``manual_override`` —
        demoted it and took the slot. Deposing a live primary is the explicit
        ``set_primary`` path's business, exactly as
        ``merge_mappings_to_track`` decided for merges ("deposing an existing
        primary is not something a merge gets to decide"). Re-scoring a track's
        own primary still works: the upsert cleared the predecessor's
        ``is_primary`` when it retired it, so the successor arrives to an empty
        slot rather than having to take one.

        Args:
            primaries: List of (track_id, connector_name, connector_track_id).

        Returns:
            Number of mappings promoted — pairs that already had a live
            primary are left alone and are not counted.
        """
        if not primaries:
            return 0
        return await self._promote_primaries_by_uuid(primaries)

    @db_operation("batch_ensure_primary_mappings_by_external_id")
    async def _batch_ensure_primary_mappings_by_external_id(
        self,
        primaries: list[tuple[UUID, str, str]],
        *,
        connector_id_map: Mapping[tuple[str, str], UUID] | None = None,
    ) -> int:
        """``_batch_ensure_primary_mappings``, keyed by external connector id.

        For the callers that genuinely only hold the string: the specs
        ``map_tracks_to_connectors`` marked ``primary``, and
        ``_create_mappings_and_set_primaries``, which reads it straight off
        ``domain_tracks[].connector_track_identifiers``. Neither ever sees the
        connector track's internal id. A caller that already holds the UUID
        should call ``_batch_ensure_primary_mappings`` directly instead of
        routing through here just to convert it back.

        Unlike its sibling this one still resets first, and so still re-elects
        rather than merely filling a vacancy: both callers have just named
        which connector id each track's primary should be, which is the same
        assertion the single-mapping ``auto_set_primary`` used to make through
        its own reset-then-set chain. Its reset is also what keeps the shared
        promotion step's vacancy guard a no-op here.

        Args:
            primaries: List of (track_id, connector_name, connector_track_identifier).
            connector_id_map: The ``(name, external_id) -> id`` mapping the
                caller already holds, if any. ``map_tracks_to_connectors`` has
                just built it upserting the same rows, so passing it skips a
                lookup of ids that were resolved moments earlier.

        Returns:
            Number of mappings successfully promoted to primary.
        """
        if not primaries:
            return 0

        await self._reset_primaries(primaries)

        ct_id_map = (
            connector_id_map
            if connector_id_map is not None
            else await self._resolve_connector_track_ids(primaries)
        )
        external_by_ct_id = {ct_id: cid for (_, cid), ct_id in ct_id_map.items()}

        by_uuid = [
            (track_id, connector_name, ct_db_id)
            for track_id, connector_name, connector_id in primaries
            if (ct_db_id := ct_id_map.get((connector_name, connector_id))) is not None
        ]
        return await self._promote_primaries_by_uuid(
            by_uuid, external_by_ct_id=external_by_ct_id
        )

    async def _resolve_connector_track_ids(
        self, primaries: list[tuple[UUID, str, str]]
    ) -> dict[tuple[str, str], UUID]:
        """Look up ``(name, external_id) -> id`` for callers that hold no map."""
        connectors = list({cn for _, cn, _ in primaries})
        connector_ids = list({cid for _, _, cid in primaries})
        ct_records = await self.connector_repo.find_by([
            self.connector_repo.model_class.connector_name.in_(connectors),
            self.connector_repo.model_class.connector_track_identifier.in_(
                connector_ids
            ),
        ])
        return {
            (ct.connector_name, ct.connector_track_identifier): ct.id
            for ct in ct_records
        }

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
        """Find tracks with live mappings for a connector but none marked primary."""
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
            .where(has_primary.c.track_id.is_(None), live_only(DBTrackMapping))
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
                & (DBTrackMapping.user_id == user_id)
                # In the JOIN, not the WHERE: the second failure mode counts
                # tracks with no matching mapping row at all.
                & live_only(DBTrackMapping),
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
            live_only(DBTrackMapping),
            text("(confidence_evidence->>'final_score')::numeric < 100"),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()
