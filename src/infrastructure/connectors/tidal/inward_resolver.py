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

Persistence runs through the shared planned-write pipeline
(``WritePlanningResolver``); this module owns payload extraction, the
``replacement_pointer`` detection label, and the two Tidal-specific mapping
policies (the primary mapping always names the current id, and a reuse can
substitute).
"""

from collections.abc import Mapping, Sequence
from typing import override

from attrs import define, evolve

from src.config import get_logger, settings
from src.config.telemetry import phase
from src.domain.entities import Track
from src.domain.entities.shared import JsonValue
from src.domain.exceptions import TidalAuthRequiredError
from src.domain.matching.content_digest import DigestSide
from src.domain.matching.evaluation_service import TrackMatchEvaluationService
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.fan_out import bounded_fan_out
from src.infrastructure.connectors._shared.inward_track_resolver import (
    PlannedWrite,
    WritePlanningResolver,
    plan_isrc_write,
)
from src.infrastructure.connectors._shared.successor_resolution import (
    SuccessorAssertion,
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
class _Target:
    """One requested id's resolution target — itself, or its live successor.

    ``detail`` (and everything minted from it) always describes the
    *current* id. ``isrc`` is ``None`` for a target the conservative
    contract cannot mint.
    """

    requested_id: str
    current_id: str
    detail: TidalTrackDetail
    isrc: str | None


class TidalInwardResolver(WritePlanningResolver[TidalTrackDetail]):
    """Resolves Tidal track ids → canonical tracks (ISRC-only).

    Also the first implementor of the shared ``SuccessorHook`` protocol
    (structural — ``resolve_successors`` consults the ``replacement``
    relationship for dead ids).
    """

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

    async def _fetch_details(self, tidal_ids: Sequence[str]) -> None:
        """Warm the memo for ``tidal_ids`` with a bounded concurrent fan-out.

        Tidal has no batch read, so one id is one request; serialising them made
        a hundred-id pass a hundred round trips. Only cache misses are fetched,
        so a repeated id costs nothing. No ``phase("api")`` here — phases are
        additive, so wrapping the fan-out would count the wall time on top of
        every request ``_fetch_detail`` already times. ``TidalAuthRequiredError``
        re-raises bare so every catch site (the middleware's 409, the CLI's
        reconnect prompt) sees the exception type it knows.
        """
        missing = [
            tid for tid in dict.fromkeys(tidal_ids) if tid not in self._detail_cache
        ]
        if not missing:
            return
        _ = await bounded_fan_out(
            missing,
            self._fetch_detail,
            concurrency=settings.api.tidal.concurrency,
            unwrap=(TidalAuthRequiredError,),
        )

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
        await self._fetch_details(dead_ids)
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
        await self._fetch_details(missing_ids)
        details: dict[str, TidalTrackDetail | None] = {
            tidal_id: await self._fetch_detail(tidal_id) for tidal_id in missing_ids
        }

        dead_ids = [
            tidal_id
            for tidal_id, detail in details.items()
            if detail is None or detail.replacement_id is not None
        ]
        assertions = await self.resolve_successors(dead_ids)
        # Prefetch every asserted successor concurrently so the per-id
        # fetch in the loop below is a memo hit.
        await self._fetch_details([a.returned_id for a in assertions.values()])

        # One pass over the requested ids builds the population the ISRC
        # arms decide over. A successor that is itself replaced or absent
        # drops its requested id into the unresolvable bucket (one hop only).
        targets: list[_Target] = []
        for tidal_id in missing_ids:
            detail = details[tidal_id]
            if detail is not None and detail.replacement_id is None:
                targets.append(
                    _Target(
                        requested_id=tidal_id,
                        current_id=tidal_id,
                        detail=detail,
                        isrc=normalized_tidal_isrc(detail.track),
                    )
                )
                continue
            assertion = assertions.get(tidal_id)
            if assertion is None:
                continue
            successor = await self._fetch_detail(assertion.returned_id)
            if successor is not None and successor.replacement_id is None:
                targets.append(
                    _Target(
                        requested_id=tidal_id,
                        current_id=assertion.returned_id,
                        detail=successor,
                        isrc=normalized_tidal_isrc(successor.track),
                    )
                )

        minted = [
            (target, target.isrc) for target in targets if target.isrc is not None
        ]

        existing_by_isrc: dict[str, Track] = {}
        if minted:
            existing_by_isrc = await uow.get_track_repository().find_tracks_by_isrcs(
                list(dict.fromkeys(isrc for _, isrc in minted)), user_id=user_id
            )

        writes = [
            self._plan_write(target, isrc, existing_by_isrc) for target, isrc in minted
        ]
        result, failed_ids = await self._persist_planned_writes(
            writes, uow, user_id=user_id
        )
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
        planned = {target.requested_id for target, _ in minted}
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

    def _plan_write(
        self,
        target: _Target,
        isrc: str,
        existing_by_isrc: Mapping[str, Track],
    ) -> PlannedWrite[TidalTrackDetail]:
        """One answered, ISRC-carrying id's persist — the shared ISRC arms."""
        detail = target.detail
        service_data: dict[str, JsonValue] = {
            "title": detail.track.title,
            "artists": list(detail.artist_names),
            "duration_ms": tidal_duration_ms(detail.track),
            "isrc": isrc,
        }
        return plan_isrc_write(
            connector=self.connector_name,
            requested_id=target.requested_id,
            current_id=target.current_id,
            payload=detail,
            duration_ms=tidal_duration_ms(detail.track),
            isrc=isrc,
            existing_by_isrc=existing_by_isrc,
            service_data=service_data,
        )

    @override
    def _canonical_payload(
        self, write: PlannedWrite[TidalTrackDetail], *, user_id: str
    ) -> Track:
        """The canonical this write creates, keyed on the *current* id.

        A suspect ISRC is stripped — the owner keeps it, and the queued
        review decides later whether the two are one recording.
        """
        track = create_track_from_tidal_detail(
            write.current_id, write.payload, user_id=user_id
        )
        if write.review is not None and track.isrc:
            track = evolve(track, isrc=None)
        return track

    @override
    def _mapping_metadata(
        self, write: PlannedWrite[TidalTrackDetail]
    ) -> dict[str, object]:
        """JSON-able mapping metadata from the domain-facing detail."""
        detail = write.payload
        return {
            "title": detail.track.title,
            "isrc": detail.track.isrc,
            "duration_seconds": detail.track.duration_seconds,
            "artist_names": list(detail.artist_names),
        }

    @override
    def _primary_mapping_id(self, write: PlannedWrite[TidalTrackDetail]) -> str:
        """The primary mapping always names the *current* id.

        For a live requested id they are the same; for a substitution it is
        the successor, whether the canonical was created or reused — the
        requested id is dead either way, unlike Apple where a reuse answered
        under the requested id itself.
        """
        return write.current_id

    @override
    def _owes_stale_mapping(self, write: PlannedWrite[TidalTrackDetail]) -> bool:
        """Every stale requested id gets its cache mapping — reuses included.

        The successor's ISRC may already be held, and the dead requested id
        still owes its stale secondary mapping.
        """
        return write.requested_id_is_stale

    @override
    def _successor_assertion(
        self, write: PlannedWrite[TidalTrackDetail], track: Track
    ) -> SuccessorAssertion | None:
        """Tidal's successor assertion — any write whose requested id is stale.

        The successor arrives via the ``replacement`` relationship consult,
        hence ``"replacement_pointer"``; the shared seam owns the batching
        and the streak-reset rationale for keying each event to the
        *requested* id's connector track.
        """
        if not write.requested_id_is_stale:
            return None
        return SuccessorAssertion(
            requested_id=write.requested_id,
            returned_id=write.current_id,
            detection="replacement_pointer",
            track_id=track.id,
        )
