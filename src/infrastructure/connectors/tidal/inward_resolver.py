"""Tidal-specific inward track resolver — conservative, ISRC-only.

Tidal Track ID Resolution Strategy
==================================
Ids are asked about one at a time via ``GET /tracks/{id}?include=artists,
replacement`` (Tidal's v2 API has no multi-id batch read), which yields four
outcomes:

1. ANSWERED with ISRC: the track is minted or deduped through the ISRC arms
   (reuse the canonical holding the ISRC, defer a suspect collision to
   review, or create a plain new canonical).
2. ANSWERED without ISRC: deliberately NOT minted. The conservative contract
   is ISRC-or-nothing — an ISRC-less track gets a no-match backoff entry
   (present-but-unresolvable; the same clock the v0.13.0 re-resolution
   drain reads) and counts as failed.
3. ANSWERED with a ``replacement`` relationship: Tidal's platform-asserted
   successor pointer, consulted through the shared ``SuccessorHook`` seam
   (this resolver is its first implementor — Spotify and Apple detect
   successors by live-fetch correlation and never take the consult path).
   The successor is resolved through the normal per-id path; the requested
   id gets a non-primary stale-id mapping and a ``substituted`` event. One
   hop only: a successor that is itself replaced (or unmintable) leaves the
   requested id unresolved on the backoff clock.
4. ABSENT (404): the same no-match backoff as (2). NOTE a deliberate
   deviation from Apple's unanswered-vs-absent split: ``get_track``
   suppresses transport failures into the same ``None`` a 404 produces, so
   a transient outage can put live ids on the backoff clock — the entry
   delays their retry to the next backoff expiry rather than preventing it.
   Revisit if per-id transport failures show up in the field.

Unlike Spotify there is no artist/title search fallback of any kind: an id
Tidal cannot account for stays unresolved until a later import or the
re-resolution drain retries it.
"""

from collections.abc import Mapping, Sequence
from typing import ClassVar, override

from attrs import define, evolve

from src.config import get_logger
from src.config.constants import MatchMethod
from src.config.telemetry import phase
from src.domain.entities import Track
from src.domain.entities.shared import JsonValue
from src.domain.matching.content_digest import DigestSide
from src.domain.matching.evaluation_service import TrackMatchEvaluationService
from src.domain.matching.isrc_validation import (
    assess_isrc_match_reliability,
    compute_duration_diff_ms,
)
from src.domain.repositories.connector import ConnectorMappingSpec, IsrcCollisionSpec
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.inward_track_resolver import (
    InwardTrackResolver,
    persist_bulk_with_item_fallback,
)
from src.infrastructure.connectors._shared.successor_resolution import (
    SuccessorAssertion,
    record_substitutions,
    stale_id_mapping_spec,
)
from src.infrastructure.connectors.tidal.client import (
    TIDAL_COUNTRY_CODE,
    TidalAPIClient,
)
from src.infrastructure.connectors.tidal.conversions import (
    create_track_from_tidal_detail,
    normalized_tidal_isrc,
    tidal_duration_ms,
)
from src.infrastructure.connectors.tidal.models import (
    TidalTrackDetail,
    tidal_track_detail_from_document,
)

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class _IsrcCollisionReview:
    """A suspect ISRC collision to queue against the canonical that owns it."""

    owner: Track
    service_data: dict[str, JsonValue]


@define(frozen=True, slots=True)
class _PlannedWrite:
    """One requested id's persist, decided before anything is written.

    ``current_id`` is the id the primary mapping names — the requested id
    itself, or the ``replacement`` successor when the requested id is dead.
    Unlike Apple, a reuse can also be a substitution here: the successor's
    ISRC may already be held, and the dead requested id still owes its stale
    secondary mapping and ``substituted`` event.
    """

    requested_id: str
    current_id: str
    detail: TidalTrackDetail
    match_method: str
    confidence: int
    # An existing canonical already holds this recording's ISRC — map onto it.
    reuse_track: Track | None = None
    # Suspect collision: the ISRC is claimed by an owner whose duration
    # disagrees, so a review is queued and the contested ISRC withheld.
    review: _IsrcCollisionReview | None = None

    @property
    def requested_id_is_stale(self) -> bool:
        return self.current_id != self.requested_id

    @property
    def creates_canonical(self) -> bool:
        return self.reuse_track is None


class TidalInwardResolver(InwardTrackResolver):
    """Resolves Tidal track ids → canonical tracks (ISRC-only).

    Also the first implementor of the shared ``SuccessorHook`` protocol
    (structural — ``resolve_successors`` consults the ``replacement``
    relationship for dead ids).
    """

    _STALE_ID_METHODS: ClassVar[dict[str, str]] = {
        MatchMethod.DIRECT_IMPORT: MatchMethod.DIRECT_IMPORT_STALE_ID,
        MatchMethod.ISRC_MATCH: MatchMethod.ISRC_MATCH_STALE_ID,
    }

    _client: TidalAPIClient
    _detail_cache: dict[str, TidalTrackDetail | None]

    def __init__(
        self,
        client: TidalAPIClient,
        match_evaluation_service: TrackMatchEvaluationService | None = None,
    ):
        super().__init__(match_evaluation_service)
        self._client = client
        self._detail_cache = {}

    @property
    @override
    def connector_name(self) -> str:
        # Both planes are "tidal" — no Apple-style package/data-plane split.
        return "tidal"

    @override
    def _normalize_id(self, raw_id: str) -> str:
        return str(raw_id).strip()

    async def _fetch_detail(self, tidal_id: str) -> TidalTrackDetail | None:
        """One id's fetched detail, memoized for the current resolution pass.

        The memo keeps the ``resolve_successors`` consult from re-fetching
        ids ``_create_tracks_batch`` already asked about (and vice versa).
        ``None`` covers 404 and suppressed transport failures alike — see the
        module docstring's deviation note.
        """
        if tidal_id in self._detail_cache:
            return self._detail_cache[tidal_id]
        async with phase("api"):
            document = await self._client.get_track(tidal_id, TIDAL_COUNTRY_CODE)
        detail = (
            tidal_track_detail_from_document(document) if document is not None else None
        )
        self._detail_cache[tidal_id] = detail
        return detail

    async def resolve_successors(
        self, dead_ids: Sequence[str]
    ) -> Mapping[str, SuccessorAssertion]:
        """The ``SuccessorHook`` consult: which dead ids have successors?

        Tidal answers through the ``replacement`` relationship on the (still
        answering) dead id's resource. Assertions carry
        ``detection="replacement_pointer"``; ids Tidal cannot account for —
        or accounts for without a successor — are simply absent from the
        result. ``track_id`` is stamped later, once the successor has
        resolved to a canonical.
        """
        assertions: dict[str, SuccessorAssertion] = {}
        for dead_id in dead_ids:
            detail = await self._fetch_detail(dead_id)
            if detail is not None and detail.replacement_id:
                assertions[dead_id] = SuccessorAssertion(
                    requested_id=dead_id,
                    returned_id=detail.replacement_id,
                    detection="replacement_pointer",
                )
        return assertions

    @override
    async def _create_tracks_batch(
        self,
        missing_ids: list[str],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> dict[str, Track]:
        """Fetch tracks per id, mint tracks + mappings; consult successors.

        ISRC-or-nothing: an answered track without a usable ISRC creates
        nothing and joins the absent ids on the no-match backoff clock.
        """
        self._detail_cache = {}
        details: dict[str, TidalTrackDetail | None] = {}
        for tidal_id in missing_ids:
            details[tidal_id] = await self._fetch_detail(tidal_id)

        alive = {
            tidal_id: detail
            for tidal_id, detail in details.items()
            if detail is not None and detail.replacement_id is None
        }
        assertions = await self.resolve_successors([
            tid for tid in missing_ids if tid not in alive
        ])

        # (requested id, current id, detail of the current id) — the
        # populations the ISRC arms decide over. A successor that is itself
        # replaced or absent drops its requested id into the unresolvable
        # bucket (one hop only).
        targets: list[tuple[str, str, TidalTrackDetail]] = []
        for tidal_id in missing_ids:
            if tidal_id in alive:
                targets.append((tidal_id, tidal_id, alive[tidal_id]))
                continue
            assertion = assertions.get(tidal_id)
            if assertion is None:
                continue
            successor = await self._fetch_detail(assertion.returned_id)
            if successor is not None and successor.replacement_id is None:
                targets.append((tidal_id, assertion.returned_id, successor))

        with_isrc = {
            requested_id: (current_id, detail, isrc)
            for requested_id, current_id, detail in targets
            if (isrc := normalized_tidal_isrc(detail.track)) is not None
        }

        existing_by_isrc: dict[str, Track] = {}
        if with_isrc:
            existing_by_isrc = await uow.get_track_repository().find_tracks_by_isrcs(
                list(dict.fromkeys(isrc for _, _, isrc in with_isrc.values())),
                user_id=user_id,
            )

        writes = [
            self._plan_write(requested_id, current_id, detail, isrc, existing_by_isrc)
            for requested_id, (current_id, detail, isrc) in with_isrc.items()
        ]
        result, failed_ids = await self._persist_writes(writes, uow, user_id=user_id)
        if failed_ids:
            logger.warning(
                f"{len(failed_ids)} Tidal ids answered but failed to "
                f"persist — retried next import"
            )

        # One backoff clock for every unresolvable shape: answered but
        # ISRC-less, absent (404 or suppressed transport failure — see the
        # module docstring), dead without a successor, and dead whose
        # successor could not be minted. Persist failures stay off the clock
        # — those ids were answered and are retried next import.
        planned = set(with_isrc)
        unresolvable = [tid for tid in missing_ids if tid not in planned]
        if unresolvable:
            _ = await uow.get_resolution_recorder().remember_no_match(
                [self._no_match_side(tid, details.get(tid)) for tid in unresolvable],
                user_id=user_id,
                connector_name=self.connector_name,
            )

        if result:
            _ = await uow.get_resolution_recorder().clear_negatives(
                list(result), user_id=user_id, connector_name=self.connector_name
            )

        return result

    def _no_match_side(
        self, tidal_id: str, detail: TidalTrackDetail | None
    ) -> DigestSide:
        """What is known about an unresolvable id — track metadata if answered."""
        if detail is None:
            return DigestSide(identifier=tidal_id)
        return DigestSide(
            identifier=tidal_id,
            title=detail.track.title,
            artists=detail.artist_names,
            duration_ms=tidal_duration_ms(detail.track),
        )

    @staticmethod
    def _plan_write(
        requested_id: str,
        current_id: str,
        detail: TidalTrackDetail,
        isrc: str,
        existing_by_isrc: Mapping[str, Track],
    ) -> _PlannedWrite:
        """Decide what one answered, ISRC-carrying id persists as. Pure.

        Three outcomes (Apple's shape): reuse the canonical that owns this
        ISRC, defer a suspect collision to review and create a distinct
        canonical without the contested ISRC, or create a plain new
        canonical. ``detail`` (and the primary mapping) always describe the
        *current* id — the successor when the requested id is dead.
        """
        existing = existing_by_isrc.get(isrc)
        if existing is not None:
            duration_diff_ms = compute_duration_diff_ms(
                tidal_duration_ms(detail.track), existing.duration_ms
            )
            if not assess_isrc_match_reliability(duration_diff_ms).suspect:
                return _PlannedWrite(
                    requested_id=requested_id,
                    current_id=current_id,
                    detail=detail,
                    match_method=MatchMethod.ISRC_MATCH,
                    confidence=MatchMethod.ISRC_MATCH_CONFIDENCE,
                    reuse_track=existing,
                )

            service_data: dict[str, JsonValue] = {
                "title": detail.track.title,
                "artists": list(detail.artist_names),
                "duration_ms": tidal_duration_ms(detail.track),
                "isrc": isrc,
            }
            logger.info(
                f"ISRC suspect: queueing review for tidal:{current_id} vs canonical "
                f"{existing.id} (ISRC={isrc}, duration_diff_ms={duration_diff_ms})"
            )
            return _PlannedWrite(
                requested_id=requested_id,
                current_id=current_id,
                detail=detail,
                match_method=MatchMethod.DIRECT_IMPORT,
                confidence=100,
                review=_IsrcCollisionReview(owner=existing, service_data=service_data),
            )

        return _PlannedWrite(
            requested_id=requested_id,
            current_id=current_id,
            detail=detail,
            match_method=MatchMethod.DIRECT_IMPORT,
            confidence=100,
        )

    async def _persist_writes(
        self,
        writes: list[_PlannedWrite],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> tuple[dict[str, Track], set[str]]:
        """Write a chunk's resolutions — one savepoint for all, per id on failure."""

        def _log_failed_write(write: _PlannedWrite, e: Exception) -> None:
            logger.error(
                f"Failed to create track for tidal:{write.requested_id}: {e}",
                exc_info=e,
            )

        return await persist_bulk_with_item_fallback(
            writes,
            uow,
            persist=lambda chunk: self._persist_writes_bulk(
                chunk, uow, user_id=user_id
            ),
            write_key=lambda write: write.requested_id,
            describe="resolved Tidal tracks",
            on_item_failure=_log_failed_write,
        )

    def _canonical_payload(self, write: _PlannedWrite, *, user_id: str) -> Track:
        """The canonical this write creates, keyed on the *current* id.

        A suspect ISRC is stripped — the owner keeps it, and the queued
        review decides later whether the two are one recording.
        """
        track = create_track_from_tidal_detail(
            write.current_id, write.detail, user_id=user_id
        )
        if write.review is not None and track.isrc:
            track = evolve(track, isrc=None)
        return track

    @staticmethod
    def _service_metadata(detail: TidalTrackDetail) -> dict[str, object]:
        """JSON-able mapping metadata from the domain-facing detail.

        Typed ``dict[str, object]`` to match ``ConnectorMappingSpec.metadata``
        (invariant dict value type); every value is a plain JSON scalar/list.
        """
        return {
            "title": detail.track.title,
            "isrc": detail.track.isrc,
            "duration_seconds": detail.track.duration_seconds,
            "artist_names": list(detail.artist_names),
        }

    async def _persist_writes_bulk(
        self,
        writes: Sequence[_PlannedWrite],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> dict[str, Track]:
        """Write every resolution in the chunk through the batch primitives."""
        connector_repo = uow.get_connector_repository()

        # Two dead ids can share one replacement, so writes are deduped by
        # their identity key (the current id) before anything persists:
        # one collision review, one canonical create — every requested id
        # fans in to the same canonical (each still owes its own stale
        # mapping and substituted event). Without this, duplicate payloads
        # would mint duplicate canonicals and trip ``save_tracks``'
        # duplicate-identity caller-bug warning.
        collisions_by_id: dict[str, IsrcCollisionSpec] = {}
        for write in writes:
            if write.review is not None:
                _ = collisions_by_id.setdefault(
                    write.current_id,
                    IsrcCollisionSpec(
                        owner=write.review.owner,
                        connector_id=write.current_id,
                        service_data=write.review.service_data,
                    ),
                )
        if collisions_by_id:
            _ = await connector_repo.queue_isrc_collision_reviews(
                list(collisions_by_id.values()), self.connector_name, user_id=user_id
            )

        created = [write for write in writes if write.creates_canonical]
        creates_by_id: dict[str, _PlannedWrite] = {}
        for write in created:
            _ = creates_by_id.setdefault(write.current_id, write)
        saved = await uow.get_track_repository().save_tracks([
            self._canonical_payload(write, user_id=user_id)
            for write in creates_by_id.values()
        ])
        track_by_current_id = dict(zip(creates_by_id, saved, strict=True))
        canonicals: dict[str, Track] = {
            write.requested_id: track_by_current_id[write.current_id]
            for write in created
        }
        canonicals.update({
            write.requested_id: write.reuse_track
            for write in writes
            if write.reuse_track is not None
        })

        _ = await connector_repo.map_tracks_to_connectors(
            self._mapping_batch(writes, canonicals)
        )

        await self._record_substitutions(
            [write for write in writes if write.requested_id_is_stale],
            canonicals,
            uow,
            user_id=user_id,
        )
        return canonicals

    def _mapping_batch(
        self,
        writes: Sequence[_PlannedWrite],
        canonicals: Mapping[str, Track],
    ) -> list[ConnectorMappingSpec]:
        """Every mapping the chunk owes, each saying whether it holds primacy.

        The primary mapping always names the *current* id — for a live
        requested id they are the same; for a substitution it is the
        successor, whether the canonical was created or reused (the requested
        id is dead either way, unlike Apple where a reuse answered under the
        requested id itself). A substitution adds a non-primary stale-id
        mapping on the requested id (cache for future imports). One spec per
        connector id — two requested ids can share a successor.
        """
        specs: list[ConnectorMappingSpec] = []
        claimed: set[str] = set()

        def claim(spec: ConnectorMappingSpec) -> None:
            if spec.connector_id in claimed:
                return
            claimed.add(spec.connector_id)
            specs.append(spec)

        for write in writes:
            track = canonicals[write.requested_id]
            claim(
                ConnectorMappingSpec(
                    track=track,
                    connector=self.connector_name,
                    connector_id=write.current_id,
                    match_method=write.match_method,
                    confidence=write.confidence,
                    metadata=self._service_metadata(write.detail),
                    primary=True,
                )
            )
            if write.requested_id_is_stale:
                claim(
                    stale_id_mapping_spec(
                        track=track,
                        connector=self.connector_name,
                        requested_id=write.requested_id,
                        primary_method=write.match_method,
                        stale_method_map=TidalInwardResolver._STALE_ID_METHODS,
                        confidence=write.confidence,
                    )
                )
        return specs

    async def _record_substitutions(
        self,
        writes: list[_PlannedWrite],
        canonicals: Mapping[str, Track],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> None:
        """Record successor-id detections as ``substituted`` events.

        THE single substitution-recording seam for this connector, delegating
        to the shared successor seam
        (``_shared/successor_resolution.record_substitutions``), which owns
        the batching and the streak-reset rationale for keying each event to
        the *requested* id's connector track. Detection stays here: the
        successor arrives via the ``replacement`` relationship consult,
        hence ``"replacement_pointer"``.
        """
        if not writes:
            return
        await record_substitutions(
            uow.get_resolution_recorder(),
            connector_name=self.connector_name,
            assertions=[
                SuccessorAssertion(
                    requested_id=write.requested_id,
                    returned_id=write.current_id,
                    detection="replacement_pointer",
                    track_id=canonicals[write.requested_id].id,
                )
                for write in writes
            ],
            user_id=user_id,
        )
