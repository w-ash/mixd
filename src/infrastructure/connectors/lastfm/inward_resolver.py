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
)
from src.infrastructure.connectors.lastfm.client import LastFMAPIClient
from src.infrastructure.connectors.lastfm.identifiers import parse_lastfm_identifier

logger = get_logger(__name__)


class LastfmInwardResolver(InwardTrackResolver):
    """Resolves Last.fm artist::title identifiers → canonical tracks.

    Sequential per-track processing (inherent to Last.fm's API).
    Optionally attempts cross-service discovery (e.g. Spotify) for each
    new track via the ``CrossDiscoveryProvider`` protocol.
    """

    _lastfm_client: LastFMAPIClient
    _cross_discovery: CrossDiscoveryProvider | None
    _match_evaluation_service: TrackMatchEvaluationService

    def __init__(
        self,
        lastfm_client: LastFMAPIClient,
        cross_discovery: CrossDiscoveryProvider | None = None,
        match_evaluation_service: TrackMatchEvaluationService | None = None,
    ):
        self._lastfm_client = lastfm_client
        self._cross_discovery = cross_discovery
        if match_evaluation_service is None:
            from src.config import create_matching_config

            match_evaluation_service = TrackMatchEvaluationService(
                config=create_matching_config()
            )
        self._match_evaluation_service = match_evaluation_service

    @property
    @override
    def connector_name(self) -> str:
        return "lastfm"

    @override
    def _normalize_id(self, raw_id: str) -> str:
        """Normalize to lowercase stripped format for dedup."""
        return raw_id.strip().lower()

    @override
    async def _reuse_existing_canonical_tracks(
        self,
        missing_ids: list[str],
        uow: UnitOfWorkProtocol,
    ) -> dict[str, Track]:
        """Phase 1.5: Find existing canonical tracks by title+artist.

        When a second service imports, the first service's tracks already exist
        in the DB but have no connector mapping for the new service. This method
        searches by normalized title+artist for candidates, then evaluates each
        via ``TrackMatchEvaluationService`` to prevent false positives (e.g. live
        versions, remixes with identical titles). Only accepted matches get a
        connector mapping created.
        """
        # Parse identifiers → (title, artist) pairs for batch lookup
        pairs: list[tuple[str, str]] = []
        id_to_pair: dict[str, tuple[str, str]] = {}
        for identifier in missing_ids:
            artist_name, track_name = parse_lastfm_identifier(identifier)
            pair = (track_name.strip().lower(), artist_name.strip().lower())
            pairs.append(pair)
            id_to_pair[identifier] = pair

        candidates = await uow.get_track_repository().find_tracks_by_title_artist(pairs)
        if not candidates:
            return {}

        # Evaluate each candidate through the matching system
        result: dict[str, Track] = {}
        for identifier in missing_ids:
            pair = id_to_pair[identifier]
            candidate = candidates.get(pair)
            if not candidate:
                continue

            artist_name, track_name = parse_lastfm_identifier(identifier)
            connector_id = f"{artist_name.strip()}::{track_name.strip()}"

            # Evaluate match quality — same pipeline as _create_lastfm_mapping
            raw_match = RawProviderMatch(
                connector_id=connector_id,
                match_method=MatchMethod.CANONICAL_REUSE,
                service_data={
                    "title": track_name,
                    "artist": artist_name,
                    "duration_ms": None,
                    "artist_name": artist_name,
                    "track_name": track_name,
                },
            )
            match_result = self._match_evaluation_service.evaluate_single_match(
                candidate, raw_match, "lastfm"
            )

            # Require both high overall confidence AND high title similarity.
            # The Fellegi-Sunter model can produce high confidence from artist
            # match alone — insufficient for candidate discovery where we don't
            # have a priori belief the tracks are the same.
            title_sim = match_result.evidence.title_similarity if match_result.evidence else 0.0
            title_threshold = self._match_evaluation_service.config.high_similarity_threshold

            if not match_result.success or title_sim < title_threshold:
                logger.debug(
                    f"Phase 1.5 rejected candidate {candidate.id} for {identifier} "
                    f"(confidence: {match_result.confidence}, title_sim: {title_sim:.2f})"
                )
                continue

            try:
                await uow.get_connector_repository().map_track_to_connector(
                    candidate,
                    "lastfm",
                    connector_id,
                    MatchMethod.CANONICAL_REUSE,
                    confidence=match_result.confidence,
                    metadata={"artist_name": artist_name, "track_name": track_name},
                    confidence_evidence=match_result.evidence.as_dict()
                    if match_result.evidence
                    else None,
                )
                result[identifier] = candidate
                logger.info(
                    f"Reused canonical track {candidate.id} for {identifier} "
                    f"(confidence: {match_result.confidence})"
                )
            except Exception as e:
                logger.debug(
                    f"Failed to create reuse mapping for {identifier}: {e}"
                )

        return result

    @override
    async def _create_tracks_batch(
        self,
        missing_ids: list[str],
        uow: UnitOfWorkProtocol,
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
                connector_id = (
                    lastfm_url or f"{artist_name.strip()}::{track_name.strip()}"
                )
                await self._create_lastfm_mapping(
                    track, artist_name, track_name, connector_id, uow
                )
                # Secondary artist::title mapping for fast dedup lookups
                fallback_id = f"{artist_name.strip()}::{track_name.strip()}"
                if connector_id != fallback_id:
                    await self._create_lastfm_mapping(
                        track, artist_name, track_name, fallback_id, uow
                    )

                result[identifier] = track

                # Step 4: Attempt cross-service discovery (e.g. Spotify)
                if self._cross_discovery:
                    await self._cross_discovery.attempt_discovery(
                        track, artist_name, track_name, uow
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
            match_method="artist_title",
            service_data={
                "title": track_name,
                "artist": artist_name,
                "duration_ms": None,
                "artist_name": artist_name,
                "track_name": track_name,
            },
        )

        match_result = self._match_evaluation_service.evaluate_single_match(
            track, raw_match, "lastfm"
        )

        _ = await uow.get_connector_repository().map_track_to_connector(
            track,
            "lastfm",
            connector_id,
            "lastfm_import",
            confidence=match_result.confidence,
            metadata={"artist_name": artist_name, "track_name": track_name},
            confidence_evidence=match_result.evidence.as_dict()
            if match_result.evidence
            else None,
        )
