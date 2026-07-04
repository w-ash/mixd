"""Last.fm-specific inward track resolver.

Resolves Last.fm track identifiers (artist::title) to canonical tracks using
the shared InwardTrackResolver pattern. Each missing track is processed
sequentially because Last.fm's track.getInfo API is per-track.

Flow per missing track (reuse-before-create):
1. Enrich via track.getInfo (duration, album, MBID) into an in-memory
   probe Track — NOTHING is saved yet. track.getInfo runs with
   ``autocorrect=1``, so a successful response also carries Last.fm's
   CORRECTED artist/title names for free. When getInfo fails or returns
   nothing, track.getCorrection is tried as a one-shot fallback.
2. Ask the ``CrossDiscoveryProvider`` whether an existing canonical should
   absorb this recording. On reuse, map the Last.fm identifier(s) onto that
   canonical and stop — no skeletal canonical is ever created.
3. Otherwise build the canonical ONCE, fully enriched, save it, create the
   Last.fm connector mapping(s), then apply any new Spotify mapping + backfill.

Identifier invariant: every Last.fm connector_track_identifier is
``make_lastfm_identifier(artist, title)``, minted PRIMARILY from the
CORRECTED names (``MatchMethod.LASTFM_IMPORT``). When the corrected composite
differs from the raw one, a SECONDARY mapping is also minted on the raw
composite (``MatchMethod.LASTFM_RAW_ALIAS``) so a future raw-spelled import
still hits the fast connector-mapping lookup. Correction only ever runs when
creating a NEW connector track — never per-play.
"""

from typing import override

from attrs import evolve

from src.config import get_logger
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
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.inward_track_resolver import (
    InwardTrackResolver,
    ReuseMetadata,
)
from src.infrastructure.connectors.lastfm.client import LastFMAPIClient
from src.infrastructure.connectors.lastfm.identifiers import (
    make_lastfm_identifier,
    parse_lastfm_identifier,
)

logger = get_logger(__name__)


class LastfmInwardResolver(InwardTrackResolver):
    """Resolves Last.fm artist::title identifiers → canonical tracks.

    Sequential per-track processing (inherent to Last.fm's API).
    Optionally attempts cross-service discovery (e.g. Spotify) for each
    new track via the ``CrossDiscoveryProvider`` protocol.
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

        Sequential because track.getInfo and Spotify search are per-track.
        """
        result: dict[str, Track] = {}

        for identifier in missing_ids:
            try:
                await self._resolve_one_identifier(
                    identifier, result, uow, user_id=user_id
                )
            except Exception as e:
                logger.error(f"Failed to create canonical track for {identifier}: {e}")

        return result

    async def _resolve_one_identifier(
        self,
        identifier: str,
        result: dict[str, Track],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> None:
        """Resolve a single Last.fm identifier into a canonical track.

        Reuse-before-create: enrich into an in-memory probe, ask cross-discovery
        whether an existing canonical should absorb this recording, and only
        build + save a new canonical when it should not. On success the resolved
        track is stored in ``result`` keyed by ``identifier``.
        """
        artist_name, track_name = parse_lastfm_identifier(identifier)

        # Step 1: Enrich via track.getInfo — data in hand, NO track saved yet.
        probe, corrected_artist, corrected_title = await self._build_enriched_probe(
            artist_name, track_name, user_id=user_id
        )

        # Step 2: Reuse-before-create. Cross-discovery may resolve to an existing
        # canonical, in which case NO skeletal row is ever written.
        outcome: DiscoveryOutcome = Nothing()
        if self._cross_discovery:
            outcome = await self._cross_discovery.discover(
                probe, artist_name, track_name, uow, user_id=user_id
            )

        if isinstance(outcome, ReuseExisting):
            await self._map_lastfm_identifiers(
                outcome.track,
                artist_name,
                track_name,
                corrected_artist,
                corrected_title,
                uow,
            )
            if outcome.spotify_id:
                _ = await uow.get_connector_repository().map_track_to_connector(
                    outcome.track,
                    "spotify",
                    outcome.spotify_id,
                    outcome.match_method,
                    confidence=outcome.confidence,
                    metadata=outcome.metadata,
                    confidence_evidence=outcome.confidence_evidence,
                )
            result[identifier] = outcome.track
            logger.info(
                f"Cross-discovery reused canonical {outcome.track.id} for "
                f"lastfm:{artist_name} - {track_name}"
            )
            return

        # Step 3: No reuse — build the canonical ONCE, fully enriched. Folding the
        # Spotify backfill in BEFORE the single save means the enriched row is
        # inserted whole; there is no version-0 re-save that would INSERT a
        # duplicate (the enrichment-orphan defect).
        if isinstance(outcome, NewMapping):
            probe = evolve(
                probe,
                album=probe.album or outcome.album,
                duration_ms=probe.duration_ms or outcome.duration_ms,
                isrc=probe.isrc or outcome.isrc,
            )

        canonical = await uow.get_track_repository().save_track(probe)
        await self._map_lastfm_identifiers(
            canonical, artist_name, track_name, corrected_artist, corrected_title, uow
        )
        result[identifier] = canonical

        if isinstance(outcome, NewMapping):
            _ = await uow.get_connector_repository().map_track_to_connector(
                canonical,
                "spotify",
                outcome.spotify_id,
                outcome.match_method,
                confidence=outcome.confidence,
                metadata=outcome.metadata,
                confidence_evidence=outcome.confidence_evidence,
            )

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
        only saved by the caller if cross-discovery does not reuse an existing
        canonical.

        Last.fm's getInfo MBID is intentionally NOT attached as a musicbrainz
        identity key — Last.fm returns an untrusted *track* MBID from its own
        matching (not a recording MBID), so identity is resolved via ISRC /
        ListenBrainz / fuzzy instead (FM1d). See the inline note below.

        ``corrected_artist``/``corrected_title`` are the Last.fm-CORRECTED
        names used by the caller to mint the PRIMARY connector identifier
        (see ``_map_lastfm_identifiers``). track.getInfo runs with
        ``autocorrect=1``, so a successful response's ``lastfm_artist_name``/
        ``lastfm_title`` already ARE the corrected pair — free. When getInfo
        fails or returns nothing, ``track.getCorrection`` is tried as a
        fallback; if that ALSO fails/returns nothing, the corrected pair
        degrades to the raw ``(artist_name, track_name)`` — an accepted
        residual (the mint still proceeds; at worst a future correctly-spelled
        import creates a second, dedup-eligible mapping).
        """
        probe = Track(
            title=track_name,
            artists=[Artist(name=artist_name)],
            user_id=user_id,
        )
        try:
            info = await self._lastfm_client.get_track_info_comprehensive(
                artist_name, track_name
            )
        except Exception as e:
            logger.debug(
                f"track.getInfo enrichment failed for {artist_name} - {track_name}: {e}"
            )
            info = None

        if not info:
            corrected_artist, corrected_title = await self._resolve_corrected_names(
                artist_name, track_name
            )
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
        corrected_artist = info.lastfm_artist_name or artist_name
        corrected_title = info.lastfm_title or track_name
        return probe, corrected_artist, corrected_title

    async def _resolve_corrected_names(
        self, artist_name: str, track_name: str
    ) -> tuple[str, str]:
        """Fallback correction lookup, used only when track.getInfo yields nothing.

        track.getCorrection returns Last.fm's autocorrected (title, artist)
        pair. When it too is unavailable, degrades to the raw names — an
        accepted residual; see ``_build_enriched_probe``.
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

    async def _map_lastfm_identifiers(
        self,
        track: Track,
        raw_artist: str,
        raw_title: str,
        corrected_artist: str,
        corrected_title: str,
        uow: UnitOfWorkProtocol,
    ) -> None:
        """Map the Last.fm identifier(s) onto ``track``.

        Mints the PRIMARY mapping on the Last.fm-CORRECTED artist::title
        composite (``MatchMethod.LASTFM_IMPORT``) — the connector identifier
        invariant every Last.fm mint site now shares. When the corrected
        composite differs from the raw one (autocorrect fixed a typo or
        miscapitalization), ALSO mints a SECONDARY mapping on the raw
        composite (``MatchMethod.LASTFM_RAW_ALIAS``) so a future import
        carrying the same raw (uncorrected) spelling still hits the fast
        connector-mapping lookup instead of re-running getInfo/getCorrection.

        The raw alias is minted with ``auto_set_primary=False`` — it is a
        lookup convenience, not the canonical provenance. Without this it would
        be the *last* writer and its ``ensure_primary_mapping`` would demote the
        corrected ``LASTFM_IMPORT`` primary, so the fast-path provenance would
        report the raw alias ("Secondary Cache") instead of the real import.
        """
        primary_id = make_lastfm_identifier(corrected_artist, corrected_title)
        await self._create_lastfm_mapping(
            track,
            corrected_artist,
            corrected_title,
            primary_id,
            uow,
            match_method=MatchMethod.LASTFM_IMPORT,
        )

        raw_id = make_lastfm_identifier(raw_artist, raw_title)
        if raw_id != primary_id:
            await self._create_lastfm_mapping(
                track,
                raw_artist,
                raw_title,
                raw_id,
                uow,
                match_method=MatchMethod.LASTFM_RAW_ALIAS,
                auto_set_primary=False,
            )

    async def _create_lastfm_mapping(
        self,
        track: Track,
        artist_name: str,
        track_name: str,
        connector_id: str,
        uow: UnitOfWorkProtocol,
        *,
        match_method: str,
        auto_set_primary: bool = True,
    ) -> None:
        """Create a Last.fm connector mapping with domain-calculated confidence.

        ``auto_set_primary=False`` mints the mapping without promoting it to the
        connector's primary — used for the raw-alias mapping so it never demotes
        the corrected import primary (v0.8.18 FM1b provenance).
        """
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

        _ = await uow.get_connector_repository().map_track_to_connector(
            track,
            self.connector_name,
            connector_id,
            match_method,
            confidence=match_result.confidence,
            metadata={"artist_name": artist_name, "track_name": track_name},
            confidence_evidence=match_result.evidence_dict,
            auto_set_primary=auto_set_primary,
        )
