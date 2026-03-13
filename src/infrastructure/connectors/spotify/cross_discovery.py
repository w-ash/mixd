"""Spotify cross-discovery provider for other connectors.

Implements ``CrossDiscoveryProvider`` so that non-Spotify connectors (e.g.
Last.fm) can discover Spotify mappings without directly importing
``SpotifyConnector``. This keeps cross-service orchestration behind a
domain protocol, satisfying DDD's dependency rule.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.config import get_logger
from src.config.constants import MatchMethod
from src.domain.entities import Track
from src.domain.matching.evaluation_service import TrackMatchEvaluationService
from src.domain.repositories import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.isrc import normalize_isrc
from src.infrastructure.connectors.spotify import SpotifyConnector
from src.infrastructure.connectors.spotify.utilities import search_and_evaluate_match

if TYPE_CHECKING:
    from src.infrastructure.connectors.listenbrainz.lookup import ListenBrainzLookup

logger = get_logger(__name__)


class SpotifyCrossDiscoveryProvider:
    """Searches Spotify for a track and creates a connector mapping if matched.

    Satisfies ``CrossDiscoveryProvider`` protocol structurally.
    Optionally uses ListenBrainz Labs API for Spotify ID pre-resolution.
    """

    _spotify_connector: SpotifyConnector
    _match_evaluation_service: TrackMatchEvaluationService
    _listenbrainz_lookup: ListenBrainzLookup | None

    def __init__(
        self,
        spotify_connector: SpotifyConnector,
        match_evaluation_service: TrackMatchEvaluationService | None = None,
        listenbrainz_lookup: ListenBrainzLookup | None = None,
    ):
        self._spotify_connector = spotify_connector
        if match_evaluation_service is None:
            from src.config import create_matching_config

            match_evaluation_service = TrackMatchEvaluationService(
                config=create_matching_config()
            )
        self._match_evaluation_service = match_evaluation_service
        self._listenbrainz_lookup = listenbrainz_lookup

    async def attempt_discovery(
        self,
        track: Track,
        artist_name: str,
        track_name: str,
        uow: UnitOfWorkProtocol,
    ) -> bool:
        """Search Spotify, evaluate match quality, create mapping if accepted.

        Includes ISRC collision check: if the Spotify match's ISRC already belongs
        to another canonical track, maps to that existing canonical instead of
        enriching the current one (prevents duplicate canonicals for the same recording).

        Optionally tries ListenBrainz Labs API first for exact Spotify ID resolution.
        """
        try:
            # Try ListenBrainz first — may resolve to an existing canonical directly
            if self._listenbrainz_lookup:
                lb_spotify_id = (
                    await self._listenbrainz_lookup.spotify_id_from_metadata(
                        artist_name, track_name
                    )
                )
                if lb_spotify_id:
                    existing = (
                        await uow.get_connector_repository().find_tracks_by_connectors(
                            [("spotify", lb_spotify_id)]
                        )
                    )
                    if existing:
                        existing_track = existing["spotify", lb_spotify_id]
                        if existing_track.id != track.id:
                            # ListenBrainz found a Spotify ID that already has a canonical
                            await uow.get_connector_repository().map_track_to_connector(
                                existing_track,
                                "lastfm",
                                f"{artist_name.strip()}::{track_name.strip()}",
                                MatchMethod.CANONICAL_REUSE,
                                confidence=MatchMethod.LISTENBRAINZ_REUSE_CONFIDENCE,
                                metadata={
                                    "artist_name": artist_name,
                                    "track_name": track_name,
                                    "source": "listenbrainz_lookup",
                                },
                            )
                            logger.info(
                                f"ListenBrainz resolved: {artist_name} - {track_name} "
                                f"→ existing canonical {existing_track.id} via spotify:{lb_spotify_id}"
                            )
                            return True

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

            # ISRC collision check: does another canonical already own this ISRC?
            spotify_isrc = (
                normalize_isrc(best.external_ids.isrc)
                if best.external_ids and best.external_ids.isrc
                else None
            )
            if spotify_isrc:
                existing = await uow.get_track_repository().find_tracks_by_isrcs(
                    [spotify_isrc]
                )
                if existing:
                    isrc_track = next(iter(existing.values()))
                    if isrc_track.id != track.id:
                        # Another canonical already owns this ISRC — map there instead
                        await uow.get_connector_repository().map_track_to_connector(
                            isrc_track,
                            self._spotify_connector.connector_name,
                            spotify_id,
                            MatchMethod.ISRC_MATCH,
                            confidence=MatchMethod.ISRC_MATCH_CONFIDENCE,
                            metadata=best_dict,
                        )
                        logger.info(
                            f"ISRC collision: mapped spotify:{spotify_id} to existing canonical "
                            f"{isrc_track.id} instead of {track.id} (ISRC={spotify_isrc})"
                        )
                        return True

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
