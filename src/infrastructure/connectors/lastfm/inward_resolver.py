"""Last.fm-specific inward track resolver.

Resolves Last.fm track identifiers (artist::title) to canonical tracks using
the shared InwardTrackResolver pattern. Creation runs in three phases per
chunk of missing identifiers:

A. **Concurrent API enrichment** — track.getInfo (with track.getCorrection as
   fallback) per identifier, concurrently under
   ``settings.api.lastfm.concurrency``; the shared ConnectorRateLimiter on
   ``_api_call`` paces the actual request starts. Pure API — nothing is
   written. A CONTENT miss (the API answered without usable enrichment)
   degrades that identifier to a raw-name probe; a TRANSPORT failure (the
   call itself failed after the retry policy gave up) fails the identifier
   instead — minting a canonical from an outage would be permanent junk the
   next import can never heal (v0.10.2.9 F5).
B. **Sequential cross-discovery** — ``CrossDiscoveryProvider.discover`` per
   probe, sequential because it takes the (non-concurrency-safe) uow and may
   write ISRC-collision reviews. Each call runs under its own savepoint so a
   swallowed mid-transaction SQL failure rolls back alone; a failed discovery
   degrades to ``Nothing()`` and the identifier proceeds to creation.
C. **Pure plan + chunk persist** — every identifier's writes are decided into
   a frozen ``_LastfmResolvedWrite`` (mappings evaluated, enrichment folded),
   then the whole chunk persists through one ``save_tracks`` and one
   ``map_tracks_to_connectors``, savepoint-bulk with per-item fallback.

Reuse-before-create still holds: when discovery resolves to an existing
canonical, the plan maps the Last.fm identifier(s) onto it and no skeletal
canonical is ever created.

Identifier invariant: every Last.fm connector_track_identifier is
``make_lastfm_identifier(artist, title)``, minted PRIMARILY from the
CORRECTED names (``MatchMethod.LASTFM_IMPORT``). When the corrected composite
differs from the raw one, a SECONDARY mapping is also minted on the raw
composite (``MatchMethod.LASTFM_RAW_ALIAS``) so a future raw-spelled import
still hits the fast connector-mapping lookup. Correction only ever runs when
creating a NEW connector track — never per-play.
"""

import asyncio
from collections.abc import Mapping, Sequence
from typing import override

from attrs import define, evolve
from pydantic import ValidationError

from src.config import get_logger, settings
from src.config.constants import MatchMethod
from src.domain.entities import Artist, Track
from src.domain.matching.evaluation_service import TrackMatchEvaluationService
from src.domain.matching.protocols import (
    CrossDiscoveryProvider,
    DiscoveryOutcome,
    NewMapping,
    Nothing,
    ReuseExisting,
)
from src.domain.matching.types import RawProviderMatch
from src.domain.repositories.connector import ConnectorMappingSpec
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.inward_track_resolver import (
    InwardTrackResolver,
    ReuseMetadata,
    persist_bulk_with_item_fallback,
)
from src.infrastructure.connectors.lastfm.client import LastFMAPIClient
from src.infrastructure.connectors.lastfm.identifiers import (
    make_lastfm_identifier,
    parse_lastfm_identifier,
)

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class _EnrichedProbe:
    """Phase A's output for one identifier: names resolved, probe built.

    ``probe`` is an UNSAVED in-memory Track — nothing is persisted until
    Phase C decides the whole chunk. The corrected pair mints the PRIMARY
    connector identifier; when the API answers with no correction it degrades
    to the raw parsed pair (accepted residual — see ``_build_enriched_probe``;
    a transport failure never reaches this type — the identifier fails).
    """

    identifier: str
    raw_artist: str
    raw_title: str
    corrected_artist: str
    corrected_title: str
    probe: Track


@define(frozen=True, slots=True)
class _PlannedLastfmMapping:
    """One Last.fm connector mapping, evaluated before anything is written.

    Confidence and evidence come from ``evaluate_single_match`` over the probe
    (or the reused canonical) at plan time — pure, so the persist step builds
    no payload of its own.
    """

    connector_id: str
    match_method: str
    primary: bool
    confidence: int
    confidence_evidence: dict[str, object] | None
    artist_name: str
    track_name: str


@define(frozen=True, slots=True)
class _PlannedSpotifyMapping:
    """The cross-discovered Spotify mapping an identifier owes, if any.

    One shape for both discovery outcomes: ``NewMapping`` (a match for the
    brand-new canonical) and ``ReuseExisting`` with a ``spotify_id`` (the
    ISRC-collision path found a Spotify id that belongs on the owner).
    """

    spotify_id: str
    match_method: str
    confidence: int
    metadata: dict[str, object]
    confidence_evidence: dict[str, object] | None


@define(frozen=True, slots=True)
class _LastfmResolvedWrite:
    """One identifier's persist, decided before anything is written.

    ``probe`` carries any ``NewMapping`` enrichment folded in via evolve, so
    the canonical is inserted whole — there is no version-0 re-save that would
    INSERT a duplicate (the enrichment-orphan defect). ``reuse_track`` set
    means no canonical is created: the mappings land on the existing one.
    """

    identifier: str
    probe: Track
    reuse_track: Track | None
    lastfm_mappings: tuple[_PlannedLastfmMapping, ...]
    spotify_mapping: _PlannedSpotifyMapping | None

    @property
    def creates_canonical(self) -> bool:
        return self.reuse_track is None


class LastfmInwardResolver(InwardTrackResolver):
    """Resolves Last.fm artist::title identifiers → canonical tracks.

    API enrichment runs concurrently under the configured limiter; database
    writes are planned per chunk and persisted through the batch primitives.
    Optionally attempts cross-service discovery (e.g. Spotify) for each new
    track via the ``CrossDiscoveryProvider`` protocol.
    """

    _lastfm_client: LastFMAPIClient
    _cross_discovery: CrossDiscoveryProvider | None

    def __init__(
        self,
        lastfm_client: LastFMAPIClient,
        cross_discovery: CrossDiscoveryProvider | None = None,
        match_evaluation_service: TrackMatchEvaluationService | None = None,
    ):
        super().__init__(match_evaluation_service)
        self._lastfm_client = lastfm_client
        self._cross_discovery = cross_discovery

    @property
    @override
    def connector_name(self) -> str:
        return "lastfm"

    @override
    def _normalize_id(self, raw_id: str) -> str:
        """Normalize to lowercase stripped format for dedup."""
        return raw_id.strip().lower()

    @override
    def _extract_reuse_metadata(self, identifier: str) -> ReuseMetadata | None:
        """Extract artist+title from Last.fm identifier for canonical reuse."""
        artist_name, track_name = parse_lastfm_identifier(identifier)
        connector_id = make_lastfm_identifier(artist_name, track_name)
        return ReuseMetadata(
            artist=artist_name,
            title=track_name,
            connector_id=connector_id,
            lookup_pair=(track_name.strip().lower(), artist_name.strip().lower()),
        )

    @override
    async def _create_tracks_batch(
        self,
        missing_ids: list[str],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> dict[str, Track]:
        """Create canonical tracks for missing Last.fm identifiers.

        Phase A enriches every identifier concurrently (pure API), Phase B
        runs cross-discovery sequentially (it holds the uow), Phase C plans
        each identifier's writes pure and persists the chunk through the
        batch primitives. Chunks arrive at most ~50 identifiers wide (the
        orchestrator's resolution commit chunking), so no further chunking
        happens here.
        """
        probes = await self._enrich_probes(missing_ids, user_id=user_id)

        writes: list[_LastfmResolvedWrite] = []
        for enriched in probes:
            outcome = await self._discover_outcome(enriched, uow, user_id=user_id)
            writes.append(self._plan_write(enriched, outcome))

        def _log_failed_write(write: _LastfmResolvedWrite, e: Exception) -> None:
            logger.error(
                f"Failed to create canonical track for {write.identifier}: {e}",
                exc_info=e,
            )

        resolved, _failed_ids = await persist_bulk_with_item_fallback(
            writes,
            uow,
            persist=lambda chunk: self._persist_writes_bulk(chunk, uow),
            write_key=lambda write: write.identifier,
            describe="resolved Last.fm tracks",
            on_item_failure=_log_failed_write,
        )
        return resolved

    async def _enrich_probes(
        self, missing_ids: Sequence[str], *, user_id: str
    ) -> list[_EnrichedProbe]:
        """Phase A: enrich every identifier concurrently under the limiter.

        The semaphore bounds in-flight coroutines to the configured
        concurrency; actual request pacing stays with the shared
        ConnectorRateLimiter on ``_api_call``. Task bodies catch every
        exception — a TaskGroup cancels siblings on escape, and one bad
        identifier must not cost the chunk — but what the catch DOES differs:
        an unparseable identifier or a transport failure marks that identifier
        failed (absent from the returned list, so it skips Phases B/C and the
        base class counts it in the failed metric — the play stays unresolved
        and the next import retries enrichment), while content misses never
        raise at all (``_build_enriched_probe`` degrades them to a raw-name
        probe). Results are returned in ``missing_ids`` order, not completion
        order: downstream planning and persistence must be deterministic.
        """
        semaphore = asyncio.Semaphore(settings.api.lastfm.concurrency)
        enriched_by_id: dict[str, _EnrichedProbe] = {}

        async def _enrich_one(identifier: str) -> None:
            try:
                artist_name, track_name = parse_lastfm_identifier(identifier)
            except ValueError as e:
                # Unmintable identifier: no probe can be built, so the
                # identifier is skipped (counted failed by the base class)
                # instead of cancelling the TaskGroup's sibling enrichments.
                logger.error(f"Skipping unparseable Last.fm identifier: {e}")
                return
            async with semaphore:
                try:
                    (
                        probe,
                        corrected_artist,
                        corrected_title,
                    ) = await self._build_enriched_probe(
                        artist_name, track_name, user_id=user_id
                    )
                except Exception as e:
                    # Transport failure: the Last.fm client raises
                    # httpx2.RequestError / httpx2.HTTPStatusError /
                    # LastFMAPIError (non-"not found" codes) after its retry
                    # policy gives up — a content miss comes back as None,
                    # never an exception. Minting a canonical from the raw
                    # names here is what turned an outage into permanent junk
                    # canonicals: mark the identifier failed instead (absent
                    # from the results, counted failed by the base class) so
                    # the play stays unresolved and the next import retries.
                    # Caught in the task body so one failure cannot cancel
                    # the TaskGroup's sibling enrichments.
                    logger.warning(
                        f"Enrichment transport failure for {artist_name} - "
                        f"{track_name}; identifier fails and will be retried "
                        f"on the next import: {e}",
                        exc_info=True,
                    )
                    return
            enriched_by_id[identifier] = _EnrichedProbe(
                identifier=identifier,
                raw_artist=artist_name,
                raw_title=track_name,
                corrected_artist=corrected_artist,
                corrected_title=corrected_title,
                probe=probe,
            )

        async with asyncio.TaskGroup() as tg:
            for identifier in missing_ids:
                _ = tg.create_task(_enrich_one(identifier))

        return [
            enriched_by_id[identifier]
            for identifier in missing_ids
            if identifier in enriched_by_id
        ]

    async def _discover_outcome(
        self,
        enriched: _EnrichedProbe,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> DiscoveryOutcome:
        """Phase B: one sequential cross-discovery call, savepoint-isolated.

        Sequential because ``discover`` takes the uow (not concurrency-safe)
        and may write ISRC-collision reviews. The savepoint preserves the
        isolation the old per-identifier savepoint gave those writes: a SQL
        failure swallowed mid-discovery rolls back alone instead of aborting
        the chunk's transaction. A failed discovery is not a failed
        identifier — it degrades to ``Nothing()`` and creation proceeds.
        """
        if not self._cross_discovery:
            return Nothing()
        try:
            async with uow.savepoint():
                return await self._cross_discovery.discover(
                    enriched.probe,
                    enriched.raw_artist,
                    enriched.raw_title,
                    uow,
                    user_id=user_id,
                )
        except Exception as e:
            logger.warning(
                f"Cross-discovery failed for {enriched.raw_artist} - "
                f"{enriched.raw_title}; proceeding without it: {e}",
                exc_info=True,
            )
            return Nothing()

    def _plan_write(
        self, enriched: _EnrichedProbe, outcome: DiscoveryOutcome
    ) -> _LastfmResolvedWrite:
        """Phase C's decide step: everything this identifier persists. Pure.

        Folds ``NewMapping`` enrichment into the probe BEFORE the single save
        so the enriched row is inserted whole, and evaluates every Last.fm
        mapping's confidence here so the persist step only writes.
        """
        probe = enriched.probe
        reuse_track: Track | None = None
        spotify_mapping: _PlannedSpotifyMapping | None = None

        if isinstance(outcome, ReuseExisting):
            reuse_track = outcome.track
            if outcome.spotify_id:
                spotify_mapping = _PlannedSpotifyMapping(
                    spotify_id=outcome.spotify_id,
                    match_method=outcome.match_method,
                    confidence=outcome.confidence,
                    metadata=outcome.metadata,
                    confidence_evidence=outcome.confidence_evidence,
                )
            logger.info(
                f"Cross-discovery reused canonical {outcome.track.id} for "
                f"lastfm:{enriched.raw_artist} - {enriched.raw_title}"
            )
        elif isinstance(outcome, NewMapping):
            probe = evolve(
                probe,
                album=probe.album or outcome.album,
                duration_ms=probe.duration_ms or outcome.duration_ms,
                isrc=probe.isrc or outcome.isrc,
            )
            spotify_mapping = _PlannedSpotifyMapping(
                spotify_id=outcome.spotify_id,
                match_method=outcome.match_method,
                confidence=outcome.confidence,
                metadata=outcome.metadata,
                confidence_evidence=outcome.confidence_evidence,
            )

        # Evaluate against what the mappings will actually attach to: the
        # reused canonical when there is one, the (enriched) probe otherwise.
        evaluation_target = reuse_track if reuse_track is not None else probe

        primary = self._plan_lastfm_mapping(
            evaluation_target,
            enriched.corrected_artist,
            enriched.corrected_title,
            MatchMethod.LASTFM_IMPORT,
            primary=True,
        )
        mappings = [primary]
        raw_id = make_lastfm_identifier(enriched.raw_artist, enriched.raw_title)
        if raw_id != primary.connector_id:
            # The raw alias is a lookup convenience, not the canonical
            # provenance: ``primary=False`` so it never demotes the corrected
            # LASTFM_IMPORT primary (v0.8.18 FM1b provenance).
            mappings.append(
                self._plan_lastfm_mapping(
                    evaluation_target,
                    enriched.raw_artist,
                    enriched.raw_title,
                    MatchMethod.LASTFM_RAW_ALIAS,
                    primary=False,
                )
            )

        return _LastfmResolvedWrite(
            identifier=enriched.identifier,
            probe=probe,
            reuse_track=reuse_track,
            lastfm_mappings=tuple(mappings),
            spotify_mapping=spotify_mapping,
        )

    def _plan_lastfm_mapping(
        self,
        track: Track,
        artist_name: str,
        track_name: str,
        match_method: str,
        *,
        primary: bool,
    ) -> _PlannedLastfmMapping:
        """Evaluate one Last.fm mapping's confidence. Pure — no I/O."""
        connector_id = make_lastfm_identifier(artist_name, track_name)
        raw_match = RawProviderMatch(
            connector_id=connector_id,
            match_method=MatchMethod.ARTIST_TITLE,
            service_data={
                "title": track_name,
                "artist": artist_name,
                "duration_ms": None,
            },
        )
        match_result = self._match_evaluation_service.evaluate_single_match(
            track, raw_match, self.connector_name
        )
        return _PlannedLastfmMapping(
            connector_id=connector_id,
            match_method=match_method,
            primary=primary,
            confidence=match_result.confidence,
            confidence_evidence=match_result.evidence_dict,
            artist_name=artist_name,
            track_name=track_name,
        )

    async def _persist_writes_bulk(
        self,
        writes: Sequence[_LastfmResolvedWrite],
        uow: UnitOfWorkProtocol,
    ) -> dict[str, Track]:
        """Write every resolution in the chunk through the batch primitives.

        Two statement groups for the whole chunk: one INSERT for the new
        canonicals (``save_tracks``, input order preserved) and one mapping
        assertion carrying corrected-primary, raw-alias and cross-discovered
        Spotify mappings together.
        """
        created = [write for write in writes if write.creates_canonical]
        saved = await uow.get_track_repository().save_tracks([
            write.probe for write in created
        ])
        canonicals: dict[str, Track] = {
            write.identifier: track for write, track in zip(created, saved, strict=True)
        }
        canonicals.update({
            write.identifier: write.reuse_track
            for write in writes
            if write.reuse_track is not None
        })

        _ = await uow.get_connector_repository().map_tracks_to_connectors(
            self._mapping_batch(writes, canonicals)
        )
        return canonicals

    @staticmethod
    def _mapping_batch(
        writes: Sequence[_LastfmResolvedWrite],
        canonicals: Mapping[str, Track],
    ) -> list[ConnectorMappingSpec]:
        """Every mapping the chunk owes, each saying whether it holds primacy."""
        specs: list[ConnectorMappingSpec] = []
        for write in writes:
            track = canonicals[write.identifier]
            specs.extend(
                ConnectorMappingSpec(
                    track=track,
                    connector="lastfm",
                    connector_id=planned.connector_id,
                    match_method=planned.match_method,
                    confidence=planned.confidence,
                    metadata={
                        "artist_name": planned.artist_name,
                        "track_name": planned.track_name,
                    },
                    confidence_evidence=planned.confidence_evidence,
                    primary=planned.primary,
                )
                for planned in write.lastfm_mappings
            )
            if write.spotify_mapping is not None:
                spotify = write.spotify_mapping
                specs.append(
                    ConnectorMappingSpec(
                        track=track,
                        connector="spotify",
                        connector_id=spotify.spotify_id,
                        match_method=spotify.match_method,
                        confidence=spotify.confidence,
                        metadata=spotify.metadata,
                        confidence_evidence=spotify.confidence_evidence,
                        primary=True,
                    )
                )
        return specs

    async def _build_enriched_probe(
        self,
        artist_name: str,
        track_name: str,
        *,
        user_id: str,
    ) -> tuple[Track, str, str]:
        """Build an UNSAVED probe Track enriched from track.getInfo.

        Returns ``(probe, corrected_artist, corrected_title)``. The probe
        carries title/artist plus any album and duration from Last.fm's
        track.getInfo response. No database write happens here — the probe is
        only saved if cross-discovery does not reuse an existing canonical.

        Last.fm's getInfo MBID is intentionally NOT attached as a musicbrainz
        identity key — Last.fm returns an untrusted *track* MBID from its own
        matching (not a recording MBID), so identity is resolved via ISRC /
        ListenBrainz / fuzzy instead (FM1d). See the inline note below.

        ``corrected_artist``/``corrected_title`` mint the PRIMARY connector
        identifier. track.getInfo runs with ``autocorrect=1``, so a successful
        response's ``lastfm_artist_name``/``lastfm_title`` already ARE the
        corrected pair — free. When getInfo ANSWERS with nothing usable
        (not-found, or an unvalidatable body), ``track.getCorrection`` is
        tried as a fallback; if that also answers with nothing, the corrected
        pair degrades to the raw ``(artist_name, track_name)`` — an accepted
        residual (the mint still proceeds; at worst a future correctly-spelled
        import creates a second, dedup-eligible mapping). Transport failures
        from either call RAISE instead of degrading — see ``_enrich_one``.

        The probe's ``title``/``artists`` are built from the CORRECTED pair,
        not the parsed identifier parts: identifiers are lowercased
        (``make_lastfm_identifier``), and minting canonicals from them created
        lowercase twins of Spotify-created canonicals ("striptease" vs
        "Striptease") — 143 duplicate-canonical pairs traced to this
        (convergence findings §5b). Display casing on the canonical is the
        cure; the correction degrade above is the accepted residual.
        """
        try:
            info = await self._lastfm_client.get_track_info_comprehensive(
                artist_name, track_name
            )
        except ValidationError as e:
            # The API answered 200 with a body that failed model validation —
            # a content problem, so the degrade path below still applies.
            # Transport failures (network / HTTP / retry-exhausted service
            # errors) propagate to ``_enrich_one``, which fails the
            # identifier instead of minting from the degraded path.
            logger.debug(
                f"track.getInfo returned an unvalidatable body for "
                f"{artist_name} - {track_name}: {e}"
            )
            info = None

        if not info:
            corrected_artist, corrected_title = await self._resolve_corrected_names(
                artist_name, track_name
            )
        else:
            corrected_artist = info.lastfm_artist_name or artist_name
            corrected_title = info.lastfm_title or track_name

        probe = Track(
            title=corrected_title,
            artists=[Artist(name=corrected_artist)],
            user_id=user_id,
        )
        if not info:
            return probe, corrected_artist, corrected_title

        # Last.fm's getInfo MBID is deliberately NOT written into the
        # musicbrainz identity slot. Last.fm returns a *track* MBID from its own
        # matching (not a recording MBID), and MetaBrainz guidance is to never
        # trust Last.fm MBIDs (LB-431). Feeding one to save_track's mbid merge
        # key (uq_tracks_user_mbid) would collapse distinct recordings that
        # happen to share a stale/type-confused MBID. It is kept in the log for
        # provenance until a MusicBrainz WS/2 verification path exists (backlog:
        # MBID verification). FM1d: the matching layer already refuses these
        # MBIDs ISRC-grade weight; the write path must match.
        probe = evolve(
            probe,
            album=info.lastfm_album_name,
            duration_ms=info.lastfm_duration,
        )
        logger.debug(
            f"Enriched probe from track.getInfo: {artist_name} - {track_name} "
            f"(duration={info.lastfm_duration}, album={info.lastfm_album_name}, "
            f"unverified_lastfm_mbid={info.lastfm_mbid})"
        )
        return probe, corrected_artist, corrected_title

    async def _resolve_corrected_names(
        self, artist_name: str, track_name: str
    ) -> tuple[str, str]:
        """Fallback correction lookup, used only when track.getInfo yields nothing.

        track.getCorrection returns Last.fm's autocorrected (title, artist)
        pair. When the API answers with no correction on file, degrades to
        the raw names, an accepted residual (see ``_build_enriched_probe``).
        A transport failure raises — an outage mid-fallback must fail the
        identifier (``_enrich_one`` catches it inside the task body, so the
        TaskGroup's siblings are unaffected), never mint from raw names.
        """
        correction = await self._lastfm_client.get_track_correction(
            artist_name, track_name
        )

        if correction is None:
            logger.debug(
                f"No track.getCorrection result for {artist_name} - {track_name}; "
                "minting from raw normalized names"
            )
            return artist_name, track_name

        corrected_title, corrected_artist = correction
        return corrected_artist, corrected_title
