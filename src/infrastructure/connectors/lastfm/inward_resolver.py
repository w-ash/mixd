"""Last.fm-specific inward track resolver.

Resolves Last.fm track identifiers (artist::title) to canonical tracks using
the shared InwardTrackResolver pattern. Each missing track is processed
sequentially because Last.fm's track.getInfo API is per-track.

Flow per missing track (reuse-before-create):
1. Enrich via track.getInfo (duration, album, MBID, URL) into an in-memory
   probe Track — NOTHING is saved yet.
2. Ask the ``CrossDiscoveryProvider`` whether an existing canonical should
   absorb this recording. On reuse, map the Last.fm identifier(s) onto that
   canonical and stop — no skeletal canonical is ever created.
3. Otherwise build the canonical ONCE, fully enriched, save it, create the
   Last.fm connector mapping(s), then apply any new Spotify mapping + backfill.
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
        probe, lastfm_url = await self._build_enriched_probe(
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
                outcome.track, artist_name, track_name, lastfm_url, uow
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
            canonical, artist_name, track_name, lastfm_url, uow
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
    ) -> tuple[Track, str | None]:
        """Build an UNSAVED probe Track enriched from track.getInfo.

        Returns ``(probe, lastfm_url)``. The probe carries title/artist plus any
        album, duration, and MBID (as a musicbrainz connector identifier) from
        Last.fm's track.getInfo response. No database write happens here — the
        probe is only saved by the caller if cross-discovery does not reuse an
        existing canonical. The MBID enables cross-service identity bridging.
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
            return probe, None

        if not info:
            return probe, None

        connector_ids = dict(probe.connector_track_identifiers)
        if info.lastfm_mbid and "musicbrainz" not in connector_ids:
            connector_ids["musicbrainz"] = info.lastfm_mbid

        probe = evolve(
            probe,
            album=info.lastfm_album_name,
            duration_ms=info.lastfm_duration,
            connector_track_identifiers=connector_ids,
        )
        logger.debug(
            f"Enriched probe from track.getInfo: {artist_name} - {track_name} "
            f"(duration={info.lastfm_duration}, album={info.lastfm_album_name}, "
            f"mbid={info.lastfm_mbid})"
        )
        return probe, info.lastfm_url

    async def _map_lastfm_identifiers(
        self,
        track: Track,
        artist_name: str,
        track_name: str,
        lastfm_url: str | None,
        uow: UnitOfWorkProtocol,
    ) -> None:
        """Map the Last.fm identifier(s) onto ``track``.

        Keeps the URL-primary + composite-secondary scheme: the canonical URL is
        the primary connector id (or the normalized artist::title composite when
        no URL is available), with the composite added as a secondary for fast
        dedup lookups. (Task 4a later collapses this to a single key.)
        """
        fallback_id = make_lastfm_identifier(artist_name, track_name)
        connector_id = lastfm_url or fallback_id
        await self._create_lastfm_mapping(
            track, artist_name, track_name, connector_id, uow
        )
        # Secondary artist::title mapping for fast dedup lookups
        if connector_id != fallback_id:
            await self._create_lastfm_mapping(
                track, artist_name, track_name, fallback_id, uow
            )

    async def _create_lastfm_mapping(
        self,
        track: Track,
        artist_name: str,
        track_name: str,
        connector_id: str,
        uow: UnitOfWorkProtocol,
    ) -> None:
        """Create a Last.fm connector mapping with domain-calculated confidence."""
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
            MatchMethod.LASTFM_IMPORT,
            confidence=match_result.confidence,
            metadata={"artist_name": artist_name, "track_name": track_name},
            confidence_evidence=match_result.evidence_dict,
        )
