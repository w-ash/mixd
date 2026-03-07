"""Spotify cross-discovery provider for other connectors.

Implements ``CrossDiscoveryProvider`` so that non-Spotify connectors (e.g.
Last.fm) can discover Spotify mappings without directly importing
``SpotifyConnector``. This keeps cross-service orchestration behind a
domain protocol, satisfying DDD's dependency rule.
"""

from src.config import get_logger
from src.config.constants import MatchMethod
from src.domain.entities import Track
from src.domain.matching.evaluation_service import TrackMatchEvaluationService
from src.domain.repositories import UnitOfWorkProtocol
from src.infrastructure.connectors.spotify import SpotifyConnector
from src.infrastructure.connectors.spotify.utilities import search_and_evaluate_match

logger = get_logger(__name__)


class SpotifyCrossDiscoveryProvider:
    """Searches Spotify for a track and creates a connector mapping if matched.

    Satisfies ``CrossDiscoveryProvider`` protocol structurally.
    """

    _spotify_connector: SpotifyConnector
    _match_evaluation_service: TrackMatchEvaluationService

    def __init__(
        self,
        spotify_connector: SpotifyConnector,
        match_evaluation_service: TrackMatchEvaluationService | None = None,
    ):
        self._spotify_connector = spotify_connector
        if match_evaluation_service is None:
            from src.config import create_matching_config

            match_evaluation_service = TrackMatchEvaluationService(
                config=create_matching_config()
            )
        self._match_evaluation_service = match_evaluation_service

    async def attempt_discovery(
        self,
        track: Track,
        artist_name: str,
        track_name: str,
        uow: UnitOfWorkProtocol,
    ) -> bool:
        """Search Spotify, evaluate match quality, create mapping if accepted."""
        try:
            search_match = await search_and_evaluate_match(
                self._spotify_connector,
                self._match_evaluation_service,
                track,
                artist_name,
                track_name,
            )
            if search_match is None:
                return False

            best = search_match.candidate
            spotify_id = best.id
            if not spotify_id:
                return False
            match_result = search_match.match_result
            best_dict = best.model_dump()

            if not match_result.success:
                logger.debug(
                    f"Spotify discovery rejected: {artist_name} - {track_name} (confidence: {match_result.confidence})"
                )
                return False

            _ = await uow.get_connector_repository().map_track_to_connector(
                track,
                self._spotify_connector.connector_name,
                spotify_id,
                MatchMethod.LASTFM_DISCOVERY,
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
