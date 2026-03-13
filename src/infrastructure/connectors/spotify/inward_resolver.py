"""Spotify-specific inward track resolver.

Spotify Track ID Resolution Strategy
=====================================
Spotify tracks can change IDs when relinked (label transfers, catalogue cleanup).
This creates three resolution scenarios for historical data:

1. DIRECT: GET /tracks/{id} returns the same ID -> 100% confidence, primary mapping
2. REDIRECT: GET /tracks/{old_id} returns a track with a DIFFERENT .id -> 100% confidence,
   dual mapping (new ID primary, old ID secondary for cache)
3. SEARCH FALLBACK: GET /tracks/{id} returns 404 (true dead) -> artist+title search ->
   70% confidence, dual mapping (found ID primary, dead ID secondary)

Scenario 2 is the most reliable - Spotify explicitly confirms the identity link.
Scenario 3 is approximate - title similarity may match a different recording (live, remix).
The secondary mapping in both cases ensures future imports with the old ID resolve
instantly via the bulk lookup fast path (no API call needed).
"""

import asyncio
from typing import ClassVar, override

from attrs import define

from src.config import create_matching_config, get_logger, settings
from src.config.constants import MatchMethod, SpotifyConstants
from src.domain.entities import Artist, Track
from src.domain.matching.evaluation_service import TrackMatchEvaluationService
from src.domain.matching.types import RawProviderMatch
from src.domain.repositories import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.inward_track_resolver import (
    InwardTrackResolver,
    TrackResolutionMetrics,
)
from src.infrastructure.connectors._shared.isrc import normalize_isrc
from src.infrastructure.connectors.spotify import SpotifyConnector
from src.infrastructure.connectors.spotify.models import SpotifyTrack

from .utilities import create_track_from_spotify_data, search_and_evaluate_match

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class FallbackHint:
    """Artist+title metadata for resolving dead Spotify track IDs via search."""

    artist_name: str
    track_name: str


@define(frozen=True, slots=True)
class _FallbackSearchResult:
    """Intermediate result from API search before DB persistence."""

    candidate: SpotifyTrack
    confidence: int
    similarity: float
    hint: FallbackHint


class SpotifyInwardResolver(InwardTrackResolver):
    """Resolves Spotify track IDs → canonical tracks.

    Uses Spotify's batch API (get_tracks_by_ids, up to 50 at once)
    for efficient creation of missing tracks. Detects redirects (where
    Spotify returns a different .id than requested) and creates dual
    mappings. Falls back to artist+title search for true dead IDs
    when FallbackHints are provided.
    """

    _spotify_connector: SpotifyConnector
    _match_evaluation_service: TrackMatchEvaluationService
    _fallback_hints: dict[str, FallbackHint]
    _fallback_resolved_ids: set[str]
    _redirect_resolved_ids: set[str]

    def __init__(
        self,
        spotify_connector: SpotifyConnector,
        match_evaluation_service: TrackMatchEvaluationService | None = None,
    ):
        self._spotify_connector = spotify_connector
        if match_evaluation_service is None:
            match_evaluation_service = TrackMatchEvaluationService(
                config=create_matching_config()
            )
        self._match_evaluation_service = match_evaluation_service
        self._fallback_hints = {}
        self._fallback_resolved_ids = set()
        self._redirect_resolved_ids = set()

    @property
    def fallback_resolved_ids(self) -> set[str]:
        """IDs that were resolved via search fallback (for downstream tagging)."""
        return self._fallback_resolved_ids

    @property
    def redirect_resolved_ids(self) -> set[str]:
        """IDs that were resolved via Spotify redirect (returned different .id)."""
        return self._redirect_resolved_ids

    def get_resolution_method(self, spotify_id: str) -> str:
        """How was this ID resolved? For downstream context tagging."""
        if spotify_id in self._redirect_resolved_ids:
            return MatchMethod.SPOTIFY_REDIRECT
        if spotify_id in self._fallback_resolved_ids:
            return MatchMethod.SEARCH_FALLBACK
        return MatchMethod.PLAY_RESOLVER

    @property
    @override
    def connector_name(self) -> str:
        return "spotify"

    @override
    def _normalize_id(self, raw_id: str) -> str:
        return raw_id

    @override
    async def resolve_to_canonical_tracks(
        self,
        connector_ids: list[str],
        uow: UnitOfWorkProtocol,
        *,
        fallback_hints: dict[str, FallbackHint] | None = None,
    ) -> tuple[dict[str, Track], TrackResolutionMetrics]:
        """Override to accept and stash fallback hints before delegating."""
        self._fallback_hints = fallback_hints or {}
        self._fallback_resolved_ids = set()
        self._redirect_resolved_ids = set()
        return await super().resolve_to_canonical_tracks(connector_ids, uow)

    @override
    async def _reuse_existing_canonical_tracks(
        self,
        missing_ids: list[str],
        uow: UnitOfWorkProtocol,
    ) -> dict[str, Track]:
        """Phase 1.5: Reuse existing canonical tracks by title+artist from fallback hints.

        When Last.fm has already created a canonical track but no Spotify mapping exists,
        this searches by normalized title+artist and creates a Spotify mapping to the
        existing canonical. Mirrors LastfmInwardResolver._reuse_existing_canonical_tracks.
        """
        # Build (title, artist) pairs from fallback hints
        pairs: list[tuple[str, str]] = []
        id_to_pair: dict[str, tuple[tuple[str, str], FallbackHint]] = {}
        for spotify_id in missing_ids:
            hint = self._fallback_hints.get(spotify_id)
            if not hint:
                continue
            pair = (hint.track_name.strip().lower(), hint.artist_name.strip().lower())
            pairs.append(pair)
            id_to_pair[spotify_id] = (pair, hint)

        if not pairs:
            return {}

        candidates = await uow.get_track_repository().find_tracks_by_title_artist(pairs)
        if not candidates:
            return {}

        result: dict[str, Track] = {}
        for spotify_id in missing_ids:
            entry = id_to_pair.get(spotify_id)
            if not entry:
                continue
            pair, hint = entry
            candidate = candidates.get(pair)
            if not candidate:
                continue

            # Evaluate via same confidence pipeline
            raw_match = RawProviderMatch(
                connector_id=spotify_id,
                match_method=MatchMethod.CANONICAL_REUSE,
                service_data={
                    "title": hint.track_name,
                    "artist": hint.artist_name,
                    "duration_ms": None,
                },
            )
            match_result = self._match_evaluation_service.evaluate_single_match(
                candidate, raw_match, "spotify"
            )
            title_sim = (
                match_result.evidence.title_similarity if match_result.evidence else 0.0
            )
            title_threshold = (
                self._match_evaluation_service.config.high_similarity_threshold
            )

            if not match_result.success or title_sim < title_threshold:
                logger.debug(
                    f"Phase 1.5 rejected candidate {candidate.id} for {spotify_id} "
                    f"(confidence: {match_result.confidence}, title_sim: {title_sim:.2f})"
                )
                continue

            try:
                await uow.get_connector_repository().map_track_to_connector(
                    candidate,
                    "spotify",
                    spotify_id,
                    MatchMethod.CANONICAL_REUSE,
                    confidence=match_result.confidence,
                    metadata={
                        "artist_name": hint.artist_name,
                        "track_name": hint.track_name,
                    },
                    confidence_evidence=(
                        match_result.evidence.as_dict() if match_result.evidence else None
                    ),
                )
                result[spotify_id] = candidate
                logger.info(
                    f"Reused canonical track {candidate.id} for spotify:{spotify_id} "
                    f"(confidence: {match_result.confidence})"
                )
            except Exception as e:
                logger.debug(
                    f"Failed to create reuse mapping for {spotify_id}: {e}"
                )

        return result

    # Map from primary match method to its stale-ID variant
    _STALE_ID_METHODS: ClassVar[dict[str, str]] = {
        MatchMethod.DIRECT_IMPORT: MatchMethod.DIRECT_IMPORT_STALE_ID,
        MatchMethod.SEARCH_FALLBACK: MatchMethod.SEARCH_FALLBACK_STALE_ID,
    }

    async def _save_with_connector_mappings(
        self,
        requested_id: str,
        spotify_track: SpotifyTrack,
        uow: UnitOfWorkProtocol,
        *,
        match_method: str,
        confidence: int,
    ) -> Track:
        """Save a track and create connector mappings. Creates dual mappings when IDs differ.

        Spotify can return a track with a different ID than requested (relinking).
        When this happens:
        - The RETURNED ID is the current canonical Spotify ID -> primary mapping
        - The REQUESTED ID is stale but must be cached -> secondary mapping
        Both mappings point to the same canonical track, ensuring future lookups
        for either ID resolve instantly via bulk lookup (Phase 1 fast path).
        """
        current_id = spotify_track.id or requested_id
        track_data = create_track_from_spotify_data(current_id, spotify_track)
        canonical_track = await uow.get_track_repository().save_track(track_data)

        # Primary mapping — always the current Spotify ID
        _ = await uow.get_connector_repository().map_track_to_connector(
            canonical_track,
            "spotify",
            current_id,
            match_method,
            confidence=confidence,
            metadata=spotify_track.model_dump(),
            auto_set_primary=True,
        )

        # Secondary mapping if IDs differ (redirect or search found a different ID)
        if current_id != requested_id:
            _ = await uow.get_connector_repository().map_track_to_connector(
                canonical_track,
                "spotify",
                requested_id,
                self._STALE_ID_METHODS.get(match_method, f"{match_method}_stale_id"),
                confidence=confidence,
                auto_set_primary=False,
            )

        return canonical_track

    @override
    async def _create_tracks_batch(
        self,
        missing_ids: list[str],
        uow: UnitOfWorkProtocol,
    ) -> dict[str, Track]:
        """Fetch metadata from Spotify API in batch, create tracks + mappings.

        Detects redirects (track.id != requested_id) and creates dual mappings.
        Before creating new tracks, checks if an existing canonical already owns
        the same ISRC — reuses it instead of creating a duplicate.
        Dead IDs (not returned by API) are resolved via artist+title search
        if fallback hints are available.
        """
        spotify_metadata = await self._spotify_connector.get_tracks_by_ids(missing_ids)

        # ISRC dedup: collect ISRCs from API results and check for existing canonicals
        isrc_to_spotify_id: dict[str, str] = {}
        for spotify_id, spotify_track in spotify_metadata.items():
            if spotify_track.external_ids and spotify_track.external_ids.isrc:
                isrc = normalize_isrc(spotify_track.external_ids.isrc)
                if isrc:
                    isrc_to_spotify_id[isrc] = spotify_id

        existing_by_isrc: dict[str, Track] = {}
        if isrc_to_spotify_id:
            existing_by_isrc = await uow.get_track_repository().find_tracks_by_isrcs(
                list(isrc_to_spotify_id.keys())
            )

        result: dict[str, Track] = {}
        for spotify_id in missing_ids:
            if spotify_id not in spotify_metadata:
                continue

            try:
                spotify_track = spotify_metadata[spotify_id]

                # Check if an existing canonical already owns this ISRC
                isrc = None
                if spotify_track.external_ids and spotify_track.external_ids.isrc:
                    isrc = normalize_isrc(spotify_track.external_ids.isrc)

                if isrc and isrc in existing_by_isrc:
                    existing_track = existing_by_isrc[isrc]
                    # Reuse existing canonical — just create the Spotify mapping
                    await uow.get_connector_repository().map_track_to_connector(
                        existing_track,
                        "spotify",
                        spotify_id,
                        MatchMethod.ISRC_MATCH,
                        confidence=MatchMethod.ISRC_MATCH_CONFIDENCE,
                        metadata=spotify_track.model_dump(),
                    )
                    result[spotify_id] = existing_track
                    logger.info(
                        f"ISRC dedup: reused canonical {existing_track.id} for spotify:{spotify_id} (ISRC={isrc})"
                    )
                    continue

                canonical_track = await self._save_with_connector_mappings(
                    spotify_id,
                    spotify_track,
                    uow,
                    match_method=MatchMethod.DIRECT_IMPORT,
                    confidence=100,
                )

                if spotify_track.id != spotify_id:
                    self._redirect_resolved_ids.add(spotify_id)

                result[spotify_id] = canonical_track

            except Exception as e:
                logger.error(f"Failed to create track for {spotify_id}: {e}")

        # Fallback: resolve dead IDs via artist+title search
        dead_ids = [sid for sid in missing_ids if sid not in result]
        if dead_ids and self._fallback_hints:
            fallback_tracks = await self._fallback_resolve_by_search(dead_ids, uow)
            result.update(fallback_tracks)
            self._fallback_resolved_ids.update(fallback_tracks.keys())

        return result

    async def _fallback_resolve_by_search(
        self,
        dead_ids: list[str],
        uow: UnitOfWorkProtocol,
    ) -> dict[str, Track]:
        """Resolve dead Spotify IDs via artist+title search.

        For each dead ID with a hint, searches Spotify, picks the best
        candidate by title similarity, and creates the track with reduced
        confidence. Creates both a primary mapping for the new ID and a
        secondary mapping for the dead ID (cache for future imports).

        API searches run concurrently (I/O-bound), but DB writes run
        sequentially because SQLAlchemy async sessions are not concurrency-safe.
        """
        hinted_ids = [sid for sid in dead_ids if sid in self._fallback_hints]
        if not hinted_ids:
            return {}

        logger.info(
            f"Attempting fallback search for {len(hinted_ids)} dead Spotify IDs"
        )

        # Phase 1: Concurrent API searches (I/O-bound, safe to parallelize)
        semaphore = asyncio.Semaphore(settings.api.spotify.concurrency)
        search_results: dict[str, _FallbackSearchResult] = {}

        async def _search_one(dead_id: str) -> None:
            async with semaphore:
                found = await self._fallback_search_api(dead_id)
                if found:
                    search_results[dead_id] = found

        async with asyncio.TaskGroup() as tg:
            for did in hinted_ids:
                _ = tg.create_task(_search_one(did))

        # Phase 2: Sequential DB writes (session is not concurrency-safe)
        result: dict[str, Track] = {}
        for dead_id, search_result in search_results.items():
            try:
                canonical_track = await self._save_with_connector_mappings(
                    dead_id,
                    search_result.candidate,
                    uow,
                    match_method=MatchMethod.SEARCH_FALLBACK,
                    confidence=search_result.confidence,
                )
                result[dead_id] = canonical_track
                logger.info(
                    f"Fallback resolved: {search_result.hint.artist_name} - {search_result.hint.track_name} "
                    f"→ {search_result.candidate.name} (id: {search_result.candidate.id or dead_id}, "
                    f"similarity: {search_result.similarity:.2f}, confidence: {search_result.confidence})"
                )
            except Exception as e:
                logger.opt(exception=True).error(
                    f"Fallback save failed for {dead_id}: {e}"
                )

        resolved = len(result)
        failed = len(hinted_ids) - resolved
        logger.info(f"Fallback search: {resolved} resolved, {failed} unresolvable")

        return result

    async def _fallback_search_api(
        self,
        dead_id: str,
    ) -> _FallbackSearchResult | None:
        """Search Spotify for a dead ID. Pure API + domain evaluation, no DB writes."""
        hint = self._fallback_hints[dead_id]
        try:
            hint_track = Track(
                title=hint.track_name,
                artists=[Artist(name=hint.artist_name)],
            )
            search_match = await search_and_evaluate_match(
                self._spotify_connector,
                self._match_evaluation_service,
                hint_track,
                hint.artist_name,
                hint.track_name,
                min_similarity=SpotifyConstants.FALLBACK_SIMILARITY_THRESHOLD,
                fallback_connector_id=dead_id,
            )
            if search_match is None:
                logger.debug(
                    f"Fallback search found no viable match for {hint.artist_name} - {hint.track_name}"
                )
                return None

            return _FallbackSearchResult(
                candidate=search_match.candidate,
                confidence=search_match.match_result.confidence,
                similarity=search_match.similarity,
                hint=hint,
            )
        except Exception as e:
            logger.opt(exception=True).error(
                f"Fallback search failed for {dead_id} ({hint.artist_name} - {hint.track_name}): {e}"
            )
            return None
