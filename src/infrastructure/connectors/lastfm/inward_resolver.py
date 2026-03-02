"""Last.fm-specific inward track resolver.

Resolves Last.fm track identifiers (artist::title) to canonical tracks using
the shared InwardTrackResolver pattern. Each missing track is processed
sequentially because Last.fm's track.getInfo API is per-track.

Flow per missing track:
1. Create skeletal Track (title + artist only)
2. Enrich via track.getInfo (duration, album, URL)
3. Create connector mapping using URL (or artist::title fallback)
4. Attempt Spotify cross-discovery for dual connector mapping
"""

from typing import override

from src.config import get_logger
from src.domain.entities import Artist, Track
from src.domain.matching.algorithms import calculate_title_similarity
from src.domain.matching.evaluation_service import TrackMatchEvaluationService
from src.domain.matching.types import RawProviderMatch
from src.domain.repositories import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.inward_track_resolver import (
    InwardTrackResolver,
)
from src.infrastructure.connectors.lastfm.client import LastFMAPIClient
from src.infrastructure.connectors.lastfm.identifiers import parse_lastfm_identifier
from src.infrastructure.connectors.spotify import SpotifyConnector

logger = get_logger(__name__)


class LastfmInwardResolver(InwardTrackResolver):
    """Resolves Last.fm artist::title identifiers → canonical tracks.

    Sequential per-track processing (inherent to Last.fm's API).
    Also attempts Spotify cross-discovery for each new track.
    """

    _lastfm_client: LastFMAPIClient
    _spotify_connector: SpotifyConnector
    _match_evaluation_service: TrackMatchEvaluationService

    def __init__(
        self,
        lastfm_client: LastFMAPIClient,
        spotify_connector: SpotifyConnector,
        match_evaluation_service: TrackMatchEvaluationService | None = None,
    ):
        self._lastfm_client = lastfm_client
        self._spotify_connector = spotify_connector
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

                # Step 4: Attempt Spotify cross-discovery
                await self._attempt_spotify_discovery(
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
        """Enrich track with track.getInfo data. Returns (track, lastfm_url)."""
        try:
            info = await self._lastfm_client.get_track_info_comprehensive(
                artist_name, track_name
            )
            if not info:
                return track, None

            lastfm_url = info.lastfm_url
            new_album = track.album or info.lastfm_album_name
            new_duration = track.duration_ms or info.lastfm_duration

            if new_album != track.album or new_duration != track.duration_ms:
                enriched = Track(
                    id=track.id,
                    title=track.title,
                    artists=track.artists,
                    album=new_album,
                    duration_ms=new_duration,
                    isrc=track.isrc,
                )
                track = await uow.get_track_repository().save_track(enriched)
                logger.debug(
                    f"Enriched from track.getInfo: {artist_name} - {track_name} (duration={new_duration}, album={new_album})"
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

    async def _attempt_spotify_discovery(
        self,
        track: Track,
        artist_name: str,
        track_name: str,
        uow: UnitOfWorkProtocol,
    ) -> bool:
        """Attempt Spotify cross-discovery using domain matching."""
        try:
            candidates = await self._spotify_connector.search_track(
                artist_name, track_name
            )
            if not candidates:
                return False

            matching_config = self._match_evaluation_service.config
            best = max(
                candidates,
                key=lambda c: calculate_title_similarity(
                    track_name, c.name, matching_config
                ),
            )

            spotify_id = best.id
            if not spotify_id:
                return False

            best_dict = best.model_dump()
            primary_artist = best.artists[0].name if best.artists else ""

            raw_match = RawProviderMatch(
                connector_id=spotify_id,
                match_method="artist_title",
                service_data={
                    "title": best.name,
                    "artist": primary_artist,
                    "duration_ms": best.duration_ms,
                    "id": spotify_id,
                    **best_dict,
                },
            )

            match_result = self._match_evaluation_service.evaluate_single_match(
                track, raw_match, "spotify"
            )

            if not match_result.success:
                logger.debug(
                    f"Spotify discovery rejected: {artist_name} - {track_name} (confidence: {match_result.confidence})"
                )
                return False

            _ = await uow.get_connector_repository().map_track_to_connector(
                track,
                "spotify",
                spotify_id,
                "lastfm_discovery",
                confidence=match_result.confidence,
                metadata=best_dict,
                confidence_evidence=match_result.evidence.as_dict()
                if match_result.evidence
                else None,
            )

            # Backfill canonical track with Spotify metadata if still skeletal
            if not track.duration_ms or not track.isrc or not track.album:
                enriched = Track(
                    id=track.id,
                    title=track.title,
                    artists=track.artists,
                    album=track.album or (best.album.name if best.album else None),
                    duration_ms=track.duration_ms or best.duration_ms,
                    isrc=track.isrc
                    or (best.external_ids.isrc if best.external_ids else None),
                )
                await uow.get_track_repository().save_track(enriched)

            logger.debug(
                f"Spotify discovery success: {artist_name} - {track_name} -> {spotify_id} (confidence: {match_result.confidence})"
            )
        except Exception as e:
            logger.debug(
                f"Spotify discovery failed for {artist_name} - {track_name}: {e}"
            )
            return False
        else:
            return True
