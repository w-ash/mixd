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
from collections.abc import Mapping
from typing import ClassVar, override

from attrs import define, evolve

from src.config import get_logger, settings
from src.config.constants import MatchMethod, SpotifyConstants
from src.domain.entities import Artist, Track
from src.domain.entities.shared import JsonValue
from src.domain.matching.content_digest import DigestSide
from src.domain.matching.evaluation_service import TrackMatchEvaluationService
from src.domain.matching.isrc_validation import (
    assess_isrc_match_reliability,
    compute_duration_diff_ms,
)
from src.domain.repositories.resolution import ResolutionDecision
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.inward_track_resolver import (
    InwardTrackResolver,
    ReuseMetadata,
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
    _fallback_hints: dict[str, FallbackHint]
    _fallback_resolved_ids: set[str]
    _redirect_resolved_ids: set[str]
    _isrc_suspect_deferred_ids: set[str]

    def __init__(
        self,
        spotify_connector: SpotifyConnector,
        match_evaluation_service: TrackMatchEvaluationService | None = None,
    ):
        super().__init__(match_evaluation_service)
        self._spotify_connector = spotify_connector
        self._fallback_hints = {}
        self._fallback_resolved_ids = set()
        self._redirect_resolved_ids = set()
        self._isrc_suspect_deferred_ids = set()

    @property
    def fallback_resolved_ids(self) -> set[str]:
        """IDs that were resolved via search fallback (for downstream tagging)."""
        return self._fallback_resolved_ids

    @property
    def redirect_resolved_ids(self) -> set[str]:
        """IDs that were resolved via Spotify redirect (returned different .id)."""
        return self._redirect_resolved_ids

    @property
    def isrc_suspect_deferred_ids(self) -> set[str]:
        """IDs whose ISRC collision was suspect and deferred to review.

        Populated when ``_resolve_one_missing_track`` detects a duration
        mismatch large enough to distrust the ISRC match — the incoming ID
        gets its own canonical (ISRC withheld) and a review is queued
        against the existing owner, rather than silently merging.
        """
        return self._isrc_suspect_deferred_ids

    def get_resolution_method(self, spotify_id: str) -> str:
        """How was this ID resolved? For downstream context tagging."""
        if spotify_id in self.redirect_resolved_ids:
            return MatchMethod.SPOTIFY_REDIRECT
        if spotify_id in self.fallback_resolved_ids:
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
        user_id: str,
        fallback_hints: dict[str, FallbackHint] | None = None,
    ) -> tuple[dict[str, Track], TrackResolutionMetrics]:
        """Override to accept and stash fallback hints before delegating."""
        self._fallback_hints = fallback_hints or {}
        self._fallback_resolved_ids = set()
        self._redirect_resolved_ids = set()
        self._isrc_suspect_deferred_ids = set()
        tracks, metrics = await super().resolve_to_canonical_tracks(
            connector_ids, uow, user_id=user_id
        )
        # The base class builds TrackResolutionMetrics without knowledge of
        # Spotify's redirect/fallback tracking (populated during
        # _create_tracks_batch, above) — decorate the result with them here.
        metrics = evolve(
            metrics,
            redirects=len(self._redirect_resolved_ids),
            fallbacks=len(self._fallback_resolved_ids),
        )
        return tracks, metrics

    @override
    def _extract_reuse_metadata(self, identifier: str) -> ReuseMetadata | None:
        """Extract artist+title from fallback hints for canonical reuse."""
        hint = self._fallback_hints.get(identifier)
        if not hint:
            return None
        return ReuseMetadata(
            artist=hint.artist_name,
            title=hint.track_name,
            connector_id=identifier,
            lookup_pair=(
                hint.track_name.strip().lower(),
                hint.artist_name.strip().lower(),
            ),
        )

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
        suppress_isrc: bool = False,
    ) -> Track:
        """Save a track and create connector mappings. Creates dual mappings when IDs differ.

        Spotify can return a track with a different ID than requested (relinking).
        When this happens:
        - The RETURNED ID is the current canonical Spotify ID -> primary mapping
        - The REQUESTED ID is stale but must be cached -> secondary mapping
        Both mappings point to the same canonical track, ensuring future lookups
        for either ID resolve instantly via the Mapping Lookup fast path.

        ``suppress_isrc`` strips the ISRC before saving — used when the ISRC is
        already claimed by a suspect-collision owner, so this new canonical
        must not silently take it over.
        """
        current_id = spotify_track.id or requested_id
        track_data = create_track_from_spotify_data(current_id, spotify_track)
        if suppress_isrc and track_data.isrc:
            track_data = evolve(track_data, isrc=None)
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
                self._STALE_ID_METHODS[match_method],
                confidence=confidence,
                auto_set_primary=False,
            )

        return canonical_track

    @override
    async def _create_tracks_batch(
        self,
        missing_ids: list[str],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
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
                list(isrc_to_spotify_id.keys()), user_id=user_id
            )

        result: dict[str, Track] = {}
        for spotify_id in missing_ids:
            if spotify_id not in spotify_metadata:
                continue

            try:
                result[spotify_id] = await self._resolve_one_missing_track(
                    spotify_id,
                    spotify_metadata[spotify_id],
                    existing_by_isrc,
                    uow,
                    user_id=user_id,
                )
            except Exception as e:
                logger.error(f"Failed to create track for {spotify_id}: {e}")

        absent_ids = self._absent_from(missing_ids, spotify_metadata)
        if absent_ids:
            await self._note_absent_ids(absent_ids, uow, user_id=user_id)

        dead_ids = self._substitutable_from(missing_ids, spotify_metadata, result)
        if dead_ids and self._fallback_hints:
            fallback_tracks = await self._fallback_resolve_by_search(dead_ids, uow)
            result.update(fallback_tracks)
            self._fallback_resolved_ids.update(fallback_tracks.keys())

        # Any success clears the backoff, on either clock. The suspect streak
        # needs no clearing at all — it is re-derived from the events, bounded
        # by the most recent success, so recording one truncates the window.
        #
        # A *fallback* resolution is not a success for the id that was asked
        # about: the search found some other recording and mapped it as a
        # stand-in, while the requested id is still absent from the provider.
        # Clearing its backoff would re-ask for it on the next import forever,
        # which is precisely the amnesia the clock exists to end.
        directly_resolved = [
            spotify_id
            for spotify_id in result
            if spotify_id not in self._fallback_resolved_ids
        ]
        if directly_resolved:
            _ = await uow.get_resolution_recorder().clear_negatives(
                directly_resolved, user_id=user_id, connector_name="spotify"
            )

        return result

    async def _note_absent_ids(
        self, absent_ids: list[str], uow: UnitOfWorkProtocol, *, user_id: str
    ) -> None:
        """Start or extend the backoff clock for ids the API would not return.

        One counter, not two. The backoff row already records how many times in
        a row an id has missed and when the run of misses began, so a separate
        streak scanned out of the event log was a second count of the same
        failures — written in the same call, over the same ids. "Does this look
        dead" is now a question asked of that row (``looks_dead``), and asked by
        the drift report rather than by an importer: providers relink and
        redirect rather than delete, so an id that stops answering is far more
        often a transient or a market restriction than a death.

        Only ids the API *declined to return* reach here. One that comes back
        unplayable has answered — see ``_absent_from``.
        """
        _ = await uow.get_resolution_recorder().remember_no_match(
            [self._absent_side(spotify_id) for spotify_id in absent_ids],
            user_id=user_id,
            connector_name="spotify",
        )

    @staticmethod
    def _absent_from(
        requested: list[str], returned: Mapping[str, SpotifyTrack]
    ) -> list[str]:
        """The ids Spotify declined to answer for — and only those.

        Absence means *no answer*, which is why the rule is membership in the
        response rather than anything about the track in it. A restricted
        track — ``is_playable: false`` with a ``restrictions.reason`` of
        market, product or explicit — is returned, so it can never land here:
        Spotify is saying "this id is fine, you just cannot play it *there*",
        which is availability, not existence. Counting one as a miss would back
        off an identifier that answered correctly every time, and a listener
        who moved country would watch their library decay.

        Absence proper is still a weak signal — the measured transient band for
        a spurious Spotify 404 is seconds to minutes — which is why it only
        advances a counter rather than deciding anything.
        """
        return [spotify_id for spotify_id in requested if spotify_id not in returned]

    @staticmethod
    def _substitutable_from(
        requested: list[str],
        returned: Mapping[str, SpotifyTrack],
        resolved: Mapping[str, Track],
    ) -> list[str]:
        """Unresolved ids a search may stand in for — never a restricted one.

        Substitution answers "the provider cannot account for this id". Spotify
        has accounted for a restricted track and said it exists but is
        unplayable here, so searching up a stand-in would answer a question
        nobody asked: the user would find their track quietly replaced by a
        different master because they were travelling.
        """
        return [
            spotify_id
            for spotify_id in requested
            if spotify_id not in resolved
            and not (
                (track := returned.get(spotify_id)) is not None
                and track.is_market_restricted
            )
        ]

    def _absent_side(self, spotify_id: str) -> DigestSide:
        """The little that is known about an id the API would not return."""
        hint = self._fallback_hints.get(spotify_id)
        return DigestSide(
            identifier=spotify_id,
            title=hint.track_name if hint else "",
            artists=(hint.artist_name,) if hint else (),
        )

    async def _resolve_one_missing_track(
        self,
        spotify_id: str,
        spotify_track: SpotifyTrack,
        existing_by_isrc: dict[str, Track],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> Track:
        """Resolve one missing Spotify id to a canonical track (ISRC dedup or new)."""
        # Check if an existing canonical already owns this ISRC
        isrc = None
        if spotify_track.external_ids and spotify_track.external_ids.isrc:
            isrc = normalize_isrc(spotify_track.external_ids.isrc)

        if isrc and isrc in existing_by_isrc:
            existing_track = existing_by_isrc[isrc]

            duration_diff_ms = compute_duration_diff_ms(
                spotify_track.duration_ms, existing_track.duration_ms
            )

            if assess_isrc_match_reliability(duration_diff_ms).suspect:
                # Suspect collision — don't silently merge. Queue a review
                # against the ISRC owner and create a distinct canonical
                # without the contested ISRC.
                primary_artist = (
                    spotify_track.artists[0].name if spotify_track.artists else ""
                )
                service_data: dict[str, JsonValue] = {
                    "title": spotify_track.name,
                    "artist": primary_artist,
                    "artists": [a.name for a in spotify_track.artists],
                    "duration_ms": spotify_track.duration_ms,
                    "isrc": isrc,
                }
                _ = await uow.get_connector_repository().queue_isrc_collision_review(
                    existing_track,
                    "spotify",
                    spotify_id,
                    service_data,
                    user_id=user_id,
                )
                logger.info(
                    f"ISRC suspect: queued review for spotify:{spotify_id} vs canonical "
                    f"{existing_track.id} (ISRC={isrc}, duration_diff_ms={duration_diff_ms})"
                )
                self._isrc_suspect_deferred_ids.add(spotify_id)
                return await self._save_with_connector_mappings(
                    spotify_id,
                    spotify_track,
                    uow,
                    match_method=MatchMethod.DIRECT_IMPORT,
                    confidence=100,
                    suppress_isrc=True,
                )

            # Reuse existing canonical — just create the Spotify mapping
            await uow.get_connector_repository().map_track_to_connector(
                existing_track,
                "spotify",
                spotify_id,
                MatchMethod.ISRC_MATCH,
                confidence=MatchMethod.ISRC_MATCH_CONFIDENCE,
                metadata=spotify_track.model_dump(),
            )
            logger.info(
                f"ISRC dedup: reused canonical {existing_track.id} for spotify:{spotify_id} (ISRC={isrc})"
            )
            return existing_track

        canonical_track = await self._save_with_connector_mappings(
            spotify_id,
            spotify_track,
            uow,
            match_method=MatchMethod.DIRECT_IMPORT,
            confidence=100,
        )

        if spotify_track.id != spotify_id:
            self._redirect_resolved_ids.add(spotify_id)
            await self._record_substitution(
                spotify_id, spotify_track.id, canonical_track, uow, user_id=user_id
            )

        return canonical_track

    async def _record_substitution(
        self,
        requested_id: str,
        returned_id: str | None,
        canonical_track: Track,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> None:
        """Record a relink as a ``substituted`` event — never a supersession.

        Spotify's own documentation says mutations must operate on the
        *original* id, so the requested id stays valid and retiring it would
        break writes and write-flap under a multi-market user. The dual mapping
        already caches both ids; this records the assertion that produced it.

        ``market`` is null because the batch fetch sends none: relinking only
        fires when a market is supplied, so a substitution observed without one
        is worth being able to distinguish later (memo §10.2). The pair is
        detected by request/response correlation — ``linked_from`` was removed
        in Feb 2026 and the label never came back.

        The event names the *requested* id's connector track, not the returned
        one. ``substituted`` is a streak-resetting event, and the streak it has
        to reset belongs to the id that kept coming back absent — recording it
        against nothing (or against the substitute) left a relinked id
        accumulating suspicion it had already disproved.
        """
        recorder = uow.get_resolution_recorder()
        requested_ct = await recorder.connector_track_ids(
            [requested_id], connector_name="spotify"
        )
        _ = await recorder.record(
            [
                ResolutionDecision(
                    event_type="substituted",
                    connector_name="spotify",
                    connector_track_id=requested_ct.get(requested_id),
                    track_id=canonical_track.id,
                    payload={
                        "requested_id": requested_id,
                        "returned_id": returned_id,
                        "market": None,
                        "detection": "id_mismatch",
                    },
                )
            ],
            user_id=user_id,
        )

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

        # Step 1: Concurrent API searches (I/O-bound, safe to parallelize)
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

        # Step 2: Sequential DB writes (session is not concurrency-safe)
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
                logger.error(
                    f"Fallback save failed for {dead_id}: {e}",
                    exc_info=True,
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
            logger.error(
                f"Fallback search failed for {dead_id} ({hint.artist_name} - {hint.track_name}): {e}",
                exc_info=True,
            )
            return None
