"""Last.fm-specific inward track resolver.

Resolves Last.fm track identifiers (artist::title) to canonical tracks using
the shared InwardTrackResolver pattern. Each missing track is processed
sequentially because Last.fm's track.getInfo API is per-track.

Flow per missing track:
1. Create skeletal Track (title + artist only)
2. Enrich via track.getInfo (duration, album, URL)
3. Create connector mapping using URL (or artist::title fallback)
4. Attempt cross-service discovery via ``CrossDiscoveryProvider`` protocol
"""

from __future__ import annotations

from typing import override

from src.config import get_logger
from src.config.constants import MatchMethod
from src.domain.entities import Artist, Track
from src.domain.matching.evaluation_service import TrackMatchEvaluationService
from src.domain.matching.protocols import CrossDiscoveryProvider
from src.domain.matching.types import RawProviderMatch
from src.domain.repositories import UnitOfWorkProtocol
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
                artist_name, track_name = parse_lastfm_identifier(identifier)

                # Step 1: Create skeletal track
                track = await self._create_skeletal_track(artist_name, track_name, uow)
                if not track:
                    continue

                # Step 2: Enrich via track.getInfo to get URL + metadata
                track, lastfm_url = await self._enrich_from_track_info(
                    track, artist_name, track_name, uow
                )

                # Step 3: Create connector mapping(s)
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

                result[identifier] = track

                # Step 4: Attempt cross-service discovery (e.g. Spotify)
                if self._cross_discovery:
                    await self._cross_discovery.attempt_discovery(
                        track, artist_name, track_name, uow, user_id=user_id
                    )

            except Exception as e:
                logger.error(f"Failed to create canonical track for {identifier}: {e}")

        return result

    async def _create_skeletal_track(
        self, artist_name: str, track_name: str, uow: UnitOfWorkProtocol
    ) -> Track | None:
        """Create a skeletal track with just title and artist."""
        try:
            track_data = Track(
                title=track_name,
                artists=[Artist(name=artist_name)],
            )
            return await uow.get_track_repository().save_track(track_data)
        except Exception as e:
            logger.error(
                f"Failed to create skeletal track for {artist_name} - {track_name}: {e}"
            )
            return None

    async def _enrich_from_track_info(
        self,
        track: Track,
        artist_name: str,
        track_name: str,
        uow: UnitOfWorkProtocol,
    ) -> tuple[Track, str | None]:
        """Enrich track with track.getInfo data. Returns (track, lastfm_url).

        Populates album, duration, and MBID (MusicBrainz Recording ID) from
        Last.fm's track.getInfo response. MBID enables cross-service identity
        bridging — tracks with the same MBID are the same recording.
        """
        try:
            info = await self._lastfm_client.get_track_info_comprehensive(
                artist_name, track_name
            )
            if not info:
                return track, None

            lastfm_url = info.lastfm_url
            new_album = track.album or info.lastfm_album_name
            new_duration = track.duration_ms or info.lastfm_duration
            new_mbid = info.lastfm_mbid

            # Build connector identifiers with MBID if available
            new_connector_ids = dict(track.connector_track_identifiers)
            if new_mbid and "musicbrainz" not in new_connector_ids:
                new_connector_ids["musicbrainz"] = new_mbid

            has_changes = (
                new_album != track.album
                or new_duration != track.duration_ms
                or new_connector_ids != track.connector_track_identifiers
            )

            if has_changes:
                enriched = Track(
                    id=track.id,
                    title=track.title,
                    artists=track.artists,
                    album=new_album,
                    duration_ms=new_duration,
                    isrc=track.isrc,
                    connector_track_identifiers=new_connector_ids,
                )
                track = await uow.get_track_repository().save_track(enriched)
                logger.debug(
                    f"Enriched from track.getInfo: {artist_name} - {track_name} "
                    f"(duration={new_duration}, album={new_album}, mbid={new_mbid})"
                )
        except Exception as e:
            logger.debug(
                f"track.getInfo enrichment failed for {artist_name} - {track_name}: {e}"
            )
            return track, None
        else:
            return track, lastfm_url

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
