"""Spotify cross-discovery provider for other connectors.

Implements ``CrossDiscoveryProvider`` so that non-Spotify connectors (e.g.
Last.fm) can discover Spotify mappings without directly importing
``SpotifyConnector``. This keeps cross-service orchestration behind a
domain protocol, satisfying DDD's dependency rule.
"""

from src.config import create_evaluation_service, get_logger
from src.config.constants import MatchMethod
from src.domain.entities import Track
from src.domain.entities.shared import JsonValue
from src.domain.matching.evaluation_service import TrackMatchEvaluationService
from src.domain.matching.isrc_validation import (
    assess_isrc_match_reliability,
    compute_duration_diff_ms,
)
from src.domain.matching.protocols import (
    DiscoveryOutcome,
    NewMapping,
    Nothing,
    ReuseExisting,
)
from src.domain.matching.types import MatchResult
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.isrc import normalize_isrc
from src.infrastructure.connectors.listenbrainz.lookup import ListenBrainzLookup
from src.infrastructure.connectors.spotify import SpotifyConnector
from src.infrastructure.connectors.spotify.models import SpotifyTrack
from src.infrastructure.connectors.spotify.utilities import search_and_evaluate_match

logger = get_logger(__name__)


class SpotifyCrossDiscoveryProvider:
    """Searches Spotify for a track and reports how it should be mapped.

    Satisfies ``CrossDiscoveryProvider`` protocol structurally. Returns a
    :data:`DiscoveryOutcome` describing the mapping decision rather than mutating
    the caller's canonical — the caller (which owns the canonical) applies it.
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
            match_evaluation_service = create_evaluation_service()
        self._match_evaluation_service = match_evaluation_service
        self._listenbrainz_lookup = listenbrainz_lookup

    async def discover(
        self,
        probe_track: Track,
        artist_name: str,
        track_name: str,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> DiscoveryOutcome:
        """Decide how an unsaved probe track should be mapped to Spotify.

        - ListenBrainz hit on an existing canonical → :class:`ReuseExisting`.
        - ISRC collision on an existing canonical → :class:`ReuseExisting`
          (non-suspect: reuse the owner) or :class:`NewMapping` with the ISRC
          stripped + a review queued (suspect: durations diverge).
        - Normal search success → :class:`NewMapping`.
        - Anything else (no match, low confidence, API error) → :class:`Nothing`.

        Optionally tries ListenBrainz Labs API first for exact Spotify ID
        resolution. Failures are swallowed into :class:`Nothing`.
        """
        try:
            return await self._discover(
                probe_track, artist_name, track_name, uow, user_id=user_id
            )
        except Exception as e:
            logger.debug(
                f"Spotify discovery failed for {artist_name} - {track_name}: {e}"
            )
            return Nothing()

    async def _discover(
        self,
        probe_track: Track,
        artist_name: str,
        track_name: str,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> DiscoveryOutcome:
        """Run the discovery pipeline and return the mapping decision."""
        # ListenBrainz first — may resolve to an existing canonical directly.
        lb_outcome = await self._try_listenbrainz(
            probe_track, artist_name, track_name, uow, user_id=user_id
        )
        if lb_outcome is not None:
            return lb_outcome

        search_match = await search_and_evaluate_match(
            self._spotify_connector,
            self._match_evaluation_service,
            probe_track,
            artist_name,
            track_name,
        )
        if search_match is None:
            return Nothing()

        best = search_match.candidate
        spotify_id = best.id
        if not spotify_id:
            return Nothing()

        match_result = search_match.match_result
        if not match_result.success:
            logger.debug(
                f"Spotify discovery rejected: {artist_name} - {track_name} "
                f"(confidence: {match_result.confidence})"
            )
            return Nothing()

        best_dict = best.model_dump()
        spotify_isrc = (
            normalize_isrc(best.external_ids.isrc)
            if best.external_ids and best.external_ids.isrc
            else None
        )

        # ISRC collision check: does another canonical already own this ISRC?
        if spotify_isrc:
            collision = await self._resolve_isrc_collision(
                probe_track,
                best,
                spotify_id,
                spotify_isrc,
                best_dict,
                match_result,
                uow,
                user_id=user_id,
            )
            if collision is not None:
                return collision

        logger.debug(
            f"Spotify discovery success: {artist_name} - {track_name} -> "
            f"{spotify_id} (confidence: {match_result.confidence})"
        )
        return NewMapping(
            spotify_id=spotify_id,
            confidence=match_result.confidence,
            match_method=MatchMethod.LASTFM_DISCOVERY,
            metadata=best_dict,
            confidence_evidence=match_result.evidence_dict,
            album=best.album.name if best.album else None,
            duration_ms=best.duration_ms,
            isrc=spotify_isrc,
        )

    async def _try_listenbrainz(
        self,
        probe_track: Track,
        artist_name: str,
        track_name: str,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> DiscoveryOutcome | None:
        """Resolve via ListenBrainz Spotify-id lookup, if configured.

        Returns :class:`ReuseExisting` when the looked-up Spotify id already has
        a canonical (which therefore already carries the Spotify mapping — no new
        Spotify mapping is needed), else ``None`` to fall through to search.
        """
        if not self._listenbrainz_lookup:
            return None

        lb_spotify_id = await self._listenbrainz_lookup.spotify_id_from_metadata(
            artist_name, track_name
        )
        if not lb_spotify_id:
            return None

        existing = await uow.get_connector_repository().find_tracks_by_connectors(
            [("spotify", lb_spotify_id)], user_id=user_id
        )
        if not existing:
            return None

        existing_track = existing["spotify", lb_spotify_id]
        if existing_track.id == probe_track.id:
            return None

        logger.info(
            f"ListenBrainz resolved: {artist_name} - {track_name} → existing "
            f"canonical {existing_track.id} via spotify:{lb_spotify_id}"
        )
        return ReuseExisting(track=existing_track)

    async def _resolve_isrc_collision(
        self,
        probe_track: Track,
        best: SpotifyTrack,
        spotify_id: str,
        spotify_isrc: str,
        best_dict: dict[str, object],
        match_result: MatchResult,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> DiscoveryOutcome | None:
        """Handle an ISRC that may already belong to another canonical.

        - Non-suspect (durations agree) → :class:`ReuseExisting` with the owner
          plus the Spotify mapping to create on it.
        - Suspect (durations diverge past the threshold) → queue a review and
          return :class:`NewMapping` with the contested ISRC stripped, so the
          caller's new canonical never claims it.
        - No other canonical owns the ISRC → ``None`` (caller proceeds to a
          normal new mapping).
        """
        existing = await uow.get_track_repository().find_tracks_by_isrcs(
            [spotify_isrc], user_id=user_id
        )
        if not existing:
            return None

        isrc_track = next(iter(existing.values()))
        if isrc_track.id == probe_track.id:
            return None

        duration_diff_ms = compute_duration_diff_ms(
            best.duration_ms, isrc_track.duration_ms
        )

        if assess_isrc_match_reliability(duration_diff_ms).suspect:
            # Suspect collision — don't merge. Queue a review against the ISRC
            # owner and mint a distinct canonical WITHOUT the contested ISRC.
            primary_artist = best.artists[0].name if best.artists else ""
            service_data: dict[str, JsonValue] = {
                "title": best.name,
                "artist": primary_artist,
                "artists": [a.name for a in best.artists],
                "duration_ms": best.duration_ms,
                "isrc": spotify_isrc,
            }
            _ = await uow.get_connector_repository().queue_isrc_collision_review(
                isrc_track,
                self._spotify_connector.connector_name,
                spotify_id,
                service_data,
                user_id=user_id,
            )
            logger.info(
                f"ISRC suspect: queued review for spotify:{spotify_id} vs canonical "
                f"{isrc_track.id} (ISRC={spotify_isrc}, duration_diff_ms={duration_diff_ms})"
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

        logger.info(
            f"ISRC collision: reusing canonical {isrc_track.id} for spotify:{spotify_id} "
            f"instead of a new canonical (ISRC={spotify_isrc})"
        )
        return ReuseExisting(
            track=isrc_track,
            spotify_id=spotify_id,
            confidence=MatchMethod.ISRC_MATCH_CONFIDENCE,
            match_method=MatchMethod.ISRC_MATCH,
            metadata=best_dict,
        )
