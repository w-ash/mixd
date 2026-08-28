"""Spotify cross-discovery provider for other connectors.

Implements ``CrossDiscoveryProvider`` so that non-Spotify connectors (e.g.
Last.fm) can discover Spotify mappings without directly importing
``SpotifyConnector``. This keeps cross-service orchestration behind a
domain protocol, satisfying DDD's dependency rule.

Discovery is split into two halves so a whole chunk can be decided with
batch-shaped I/O:

- **Probe** — the side-effect-free half: one ListenBrainz batch lookup for
  every album-carrying request, one canonical read for its hits, then
  Spotify ``/search``
  fanned out concurrently under ``settings.api.spotify.concurrency`` (pure
  API — the shared ConnectorRateLimiter paces request starts).
- **Decide** — the sequential half: one ``find_tracks_by_isrcs`` prefetch
  for every candidate ISRC in the batch, then a per-request decision loop
  over the prefetched state. The only write is queuing a suspect
  ISRC-collision review; every uow touchpoint runs under its own savepoint
  so a swallowed SQL failure rolls back alone.
"""

from collections.abc import Mapping, Sequence

from attrs import define, evolve

from src.config import create_evaluation_service, get_logger, settings
from src.config.constants import MatchMethod
from src.domain.entities import Track
from src.domain.entities.shared import JsonValue
from src.domain.matching.evaluation_service import TrackMatchEvaluationService
from src.domain.matching.protocols import (
    DiscoveryOutcome,
    DiscoveryRequest,
    NewMapping,
    Nothing,
    ReuseExisting,
)
from src.domain.matching.types import MatchResult
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.fan_out import bounded_fan_out
from src.infrastructure.connectors._shared.inward_track_resolver import plan_isrc_write
from src.infrastructure.connectors._shared.isrc import normalize_isrc
from src.infrastructure.connectors.listenbrainz.lookup import ListenBrainzLookup
from src.infrastructure.connectors.spotify import SpotifyConnector
from src.infrastructure.connectors.spotify.models import SpotifyTrack
from src.infrastructure.connectors.spotify.utilities import search_and_evaluate_attempt

logger = get_logger(__name__)


def _lb_triple(request: DiscoveryRequest) -> tuple[str, str, str] | None:
    """The (artist, release, track) lookup key, or None without an album."""
    album = request.probe_track.album
    if not album:
        return None
    return (request.artist_name, album, request.track_name)


def _lb_resolved_id(
    request: DiscoveryRequest, resolved: Mapping[tuple[str, str, str], str]
) -> str | None:
    """The request's resolved Spotify id, or None for skips and misses."""
    triple = _lb_triple(request)
    return resolved.get(triple) if triple else None


@define(frozen=True, slots=True)
class _ProbedRequest:
    """One request's probe result, before any mapping decision.

    ``failed`` marks a request whose probe raised — its decision degrades to
    ``Nothing``, exactly as a sequential per-request failure did. ``lb_track``
    set means ListenBrainz resolved the request to an existing canonical and
    no Spotify search ran. ``best``/``spotify_id``/``match_result`` carry an
    accepted search candidate; all ``None`` when the search found nothing
    usable.
    """

    request: DiscoveryRequest
    lb_track: Track | None = None
    best: SpotifyTrack | None = None
    spotify_id: str | None = None
    spotify_isrc: str | None = None
    match_result: MatchResult | None = None
    failed: bool = False


class SpotifyCrossDiscoveryProvider:
    """Searches Spotify for tracks and reports how they should be mapped.

    Satisfies ``CrossDiscoveryProvider`` protocol structurally. Returns
    :data:`DiscoveryOutcome` decisions rather than mutating the caller's
    canonicals — the caller (which owns the canonicals) applies them.
    Optionally uses ListenBrainz Labs API for Spotify ID pre-resolution.
    """

    _spotify_connector: SpotifyConnector
    _match_evaluation_service: TrackMatchEvaluationService
    _listenbrainz_lookup: ListenBrainzLookup | None
    _owns_connector: bool

    def __init__(
        self,
        spotify_connector: SpotifyConnector,
        match_evaluation_service: TrackMatchEvaluationService | None = None,
        listenbrainz_lookup: ListenBrainzLookup | None = None,
        *,
        owns_connector: bool = False,
    ):
        self._spotify_connector = spotify_connector
        if match_evaluation_service is None:
            match_evaluation_service = create_evaluation_service()
        self._match_evaluation_service = match_evaluation_service
        self._listenbrainz_lookup = listenbrainz_lookup
        self._owns_connector = owns_connector

    async def aclose(self) -> None:
        """Close the pools the provider owns.

        The ListenBrainz lookup is always provider-owned. The Spotify
        connector is closed only under ``owns_connector`` — an injected
        connector's lifecycle belongs to whoever constructed it.
        """
        if self._listenbrainz_lookup is not None:
            await self._listenbrainz_lookup.aclose()
        if self._owns_connector:
            await self._spotify_connector.aclose()

    async def discover_batch(
        self,
        requests: Sequence[DiscoveryRequest],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> list[DiscoveryOutcome]:
        """Decide every request in one batched pass. Input-order results.

        The I/O is batch-shaped, the decisions are per-request and identical
        to sequential :meth:`discover` calls: probe (see module docstring),
        one ``find_tracks_by_isrcs`` for every candidate ISRC, then a
        sequential decision loop over the prefetched state. One request's
        failure degrades that request to :class:`Nothing`.
        """
        if not requests:
            return []
        probed = await self._probe_batch(requests, uow, user_id=user_id)
        isrc_owners = await self._prefetch_isrc_owners(probed, uow, user_id=user_id)
        return [
            await self._decide_guarded(probe, isrc_owners, uow, user_id=user_id)
            for probe in probed
        ]

    # ── Probe half (side-effect-free API, batch-shaped reads) ─────────

    async def _probe_batch(
        self,
        requests: Sequence[DiscoveryRequest],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> list[_ProbedRequest]:
        """Probe every request: ListenBrainz first, then concurrent searches.

        The search fan-out is pure API — no uow use in flight. A worker
        catches its own exceptions and fails only its request, so one bad
        search never cancels the batch's siblings.
        """
        seeds = await self._seed_from_listenbrainz(requests, uow, user_id=user_id)

        async def _probe_one(seed: _ProbedRequest) -> _ProbedRequest:
            if seed.failed or seed.lb_track is not None:
                return seed
            try:
                return await self._probe_search(seed.request)
            except Exception as e:
                logger.debug(
                    f"Spotify discovery probe failed for "
                    f"{seed.request.artist_name} - {seed.request.track_name}: {e}"
                )
                return evolve(seed, failed=True)

        return await bounded_fan_out(
            seeds, _probe_one, concurrency=settings.api.spotify.concurrency
        )

    async def _seed_from_listenbrainz(
        self,
        requests: Sequence[DiscoveryRequest],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> list[_ProbedRequest]:
        """One ListenBrainz batch lookup + one canonical read for its hits.

        The Labs endpoint requires artist, release, AND track, so requests
        whose probe carries no album skip the ListenBrainz arm. It is an
        optional pre-filter: a skipped request — and every request in a
        failed lookup or canonical read — degrades to "no LB hit" and still
        runs the Spotify search ladder. A hit resolves to ``lb_track`` only
        when the looked-up Spotify id already has a canonical other than
        the request's own probe.
        """
        seeds = [_ProbedRequest(request=request) for request in requests]
        if not self._listenbrainz_lookup:
            return seeds

        # Order-preserving dedup: repeat plays of one track cost one lookup row.
        triples = list(
            dict.fromkeys(
                triple for request in requests if (triple := _lb_triple(request))
            )
        )
        if not triples:
            return seeds
        try:
            resolved = await self._listenbrainz_lookup.spotify_ids_from_metadata(
                triples
            )
        except Exception as e:
            logger.debug(f"ListenBrainz batch lookup failed: {e}")
            return seeds

        lb_ids = {
            spotify_id
            for request in requests
            if (spotify_id := _lb_resolved_id(request, resolved))
        }
        if not lb_ids:
            return seeds

        try:
            async with uow.savepoint():
                existing = (
                    await uow.get_connector_repository().find_tracks_by_connectors(
                        [("spotify", lb_id) for lb_id in sorted(lb_ids)],
                        user_id=user_id,
                    )
                )
        except Exception as e:
            logger.debug(f"ListenBrainz canonical prefetch failed: {e}")
            return seeds

        probed: list[_ProbedRequest] = []
        for seed in seeds:
            request = seed.request
            lb_id = _lb_resolved_id(request, resolved)
            existing_track = existing.get(("spotify", lb_id)) if lb_id else None
            if existing_track is None or existing_track.id == request.probe_track.id:
                probed.append(seed)
                continue
            logger.info(
                f"ListenBrainz resolved: {request.artist_name} - "
                f"{request.track_name} → existing canonical {existing_track.id} "
                f"via spotify:{lb_id}"
            )
            probed.append(evolve(seed, lb_track=existing_track))
        return probed

    async def _probe_search(self, request: DiscoveryRequest) -> _ProbedRequest:
        """Pure-API half of one discovery: a single Spotify search, evaluated.

        Single pass: discovery searches once per unmatched play, so a
        widening retry would double /search volume against the shared 5 rps
        limiter across a run whose misses are overwhelmingly genuine misses.
        Success is judged here rather than inside the pipeline, so the
        rejected match survives long enough to be logged with its confidence.
        """
        attempt = await search_and_evaluate_attempt(
            self._spotify_connector,
            self._match_evaluation_service,
            request.probe_track,
            request.artist_name,
            request.track_name,
            widen=False,
            require_success=False,
        )
        search_match = attempt.match
        if search_match is None:
            return _ProbedRequest(request=request)

        best = search_match.candidate
        spotify_id = best.id
        if not spotify_id:
            return _ProbedRequest(request=request)

        match_result = search_match.match_result
        if not match_result.success:
            logger.debug(
                f"Spotify discovery rejected: {request.artist_name} - "
                f"{request.track_name} (confidence: {match_result.confidence})"
            )
            return _ProbedRequest(request=request)

        spotify_isrc = (
            normalize_isrc(best.external_ids.isrc)
            if best.external_ids and best.external_ids.isrc
            else None
        )
        return _ProbedRequest(
            request=request,
            best=best,
            spotify_id=spotify_id,
            spotify_isrc=spotify_isrc,
            match_result=match_result,
        )

    # ── Decide half (sequential, works from prefetched state) ─────────

    async def _prefetch_isrc_owners(
        self,
        probed: Sequence[_ProbedRequest],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> Mapping[str, Track] | None:
        """One ``find_tracks_by_isrcs`` for every candidate ISRC in the batch.

        Returns ``None`` when the read fails: every ISRC-carrying request
        then degrades to ``Nothing`` in :meth:`_decide` (each would have
        issued the same failing read sequentially), while ISRC-less requests
        proceed untouched.
        """
        isrcs = sorted({probe.spotify_isrc for probe in probed if probe.spotify_isrc})
        if not isrcs:
            return {}
        try:
            async with uow.savepoint():
                return await uow.get_track_repository().find_tracks_by_isrcs(
                    isrcs, user_id=user_id
                )
        except Exception as e:
            logger.debug(f"ISRC owner prefetch failed for {len(isrcs)} ISRCs: {e}")
            return None

    async def _decide_guarded(
        self,
        probe: _ProbedRequest,
        isrc_owners: Mapping[str, Track] | None,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> DiscoveryOutcome:
        """One request's decision; failure degrades to Nothing.

        No savepoint here: every arm is pure from prefetched state, and the
        decision's only write (a queued suspect ISRC-collision review) runs
        under its own savepoint inside the collision arm — a SQL failure
        rolls back that write alone instead of aborting the caller's
        transaction, and this guard degrades just the one request.
        """
        try:
            return await self._decide(probe, isrc_owners, uow, user_id=user_id)
        except Exception as e:
            logger.debug(
                f"Spotify discovery failed for {probe.request.artist_name} - "
                f"{probe.request.track_name}: {e}"
            )
            return Nothing()

    async def _decide(
        self,
        probe: _ProbedRequest,
        isrc_owners: Mapping[str, Track] | None,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> DiscoveryOutcome:
        """Turn one probe result into a mapping decision.

        Works entirely from prefetched state; the only I/O is queuing a
        review for a suspect ISRC collision.
        """
        request = probe.request
        if probe.failed:
            return Nothing()
        if probe.lb_track is not None:
            return ReuseExisting(track=probe.lb_track)
        if probe.best is None or probe.spotify_id is None or probe.match_result is None:
            return Nothing()

        best_dict = probe.best.model_dump()
        if probe.spotify_isrc:
            if isrc_owners is None:
                # The batched ISRC read failed — the same failure a
                # sequential per-request read would have degraded on.
                return Nothing()
            owner = isrc_owners.get(probe.spotify_isrc)
            if owner is not None and owner.id != request.probe_track.id:
                return await self._resolve_isrc_collision(
                    owner,
                    probe.best,
                    probe.spotify_id,
                    probe.spotify_isrc,
                    best_dict,
                    probe.match_result,
                    uow,
                    user_id=user_id,
                )

        logger.debug(
            f"Spotify discovery success: {request.artist_name} - "
            f"{request.track_name} -> {probe.spotify_id} "
            f"(confidence: {probe.match_result.confidence})"
        )
        return NewMapping(
            spotify_id=probe.spotify_id,
            confidence=probe.match_result.confidence,
            match_method=MatchMethod.LASTFM_DISCOVERY,
            metadata=best_dict,
            confidence_evidence=probe.match_result.evidence_dict,
            album=probe.best.album.name if probe.best.album else None,
            duration_ms=probe.best.duration_ms,
            isrc=probe.spotify_isrc,
        )

    async def _resolve_isrc_collision(
        self,
        isrc_track: Track,
        best: SpotifyTrack,
        spotify_id: str,
        spotify_isrc: str,
        best_dict: dict[str, object],
        match_result: MatchResult,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> DiscoveryOutcome:
        """Handle an ISRC that already belongs to another canonical.

        :func:`plan_isrc_write` owns the reuse-vs-review policy; this method
        translates its planned write into discovery's outcomes:

        - Reuse (durations agree) → :class:`ReuseExisting` with the owner
          plus the Spotify mapping to create on it.
        - Review (durations diverge past the threshold) → queue the review
          and return :class:`NewMapping` with the contested ISRC stripped,
          so the caller's new canonical never claims it.
        """
        primary_artist = best.artists[0].name if best.artists else ""
        service_data: dict[str, JsonValue] = {
            "title": best.name,
            "artist": primary_artist,
            "artists": [a.name for a in best.artists],
            "duration_ms": best.duration_ms,
            "isrc": spotify_isrc,
        }
        write = plan_isrc_write(
            connector=self._spotify_connector.connector_name,
            requested_id=spotify_id,
            current_id=spotify_id,
            payload=best,
            duration_ms=best.duration_ms,
            isrc=spotify_isrc,
            existing_by_isrc={spotify_isrc: isrc_track},
            service_data=service_data,
        )

        if write.reuse_track is not None:
            logger.info(
                f"ISRC collision: reusing canonical {isrc_track.id} for "
                f"spotify:{spotify_id} instead of a new canonical "
                f"(ISRC={spotify_isrc})"
            )
            return ReuseExisting(
                track=write.reuse_track,
                spotify_id=spotify_id,
                confidence=write.confidence,
                match_method=write.match_method,
                metadata=best_dict,
            )

        if write.review is not None:
            # The decision's only write, under its own savepoint: a SQL
            # failure rolls back the review alone and degrades just this
            # request, never the chunk.
            async with uow.savepoint():
                _ = await uow.get_connector_repository().queue_isrc_collision_review(
                    write.review.owner,
                    self._spotify_connector.connector_name,
                    spotify_id,
                    write.review.service_data,
                    user_id=user_id,
                )
        return NewMapping(
            spotify_id=spotify_id,
            confidence=match_result.confidence,
            match_method=MatchMethod.LASTFM_DISCOVERY,
            metadata=best_dict,
            confidence_evidence=match_result.evidence_dict,
            album=best.album.name if best.album else None,
            duration_ms=best.duration_ms,
            isrc=None,  # contested ISRC stripped — the new canonical won't claim it
        )
