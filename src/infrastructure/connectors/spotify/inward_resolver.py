"""Spotify-specific inward track resolver.

Spotify Track ID Resolution Strategy
=====================================
Spotify tracks can change IDs when relinked (label transfers, catalogue cleanup).
IDs are asked about in chunks via ``GET /tracks?ids=``, which yields four
outcomes for historical data:

1. DIRECT: the array entry carries the same ID -> 100% confidence, primary mapping
2. REDIRECT: the entry carries a DIFFERENT .id -> 100% confidence,
   dual mapping (new ID primary, old ID secondary for cache)
3. SEARCH FALLBACK: the entry is ``null`` (true dead) -> artist+title search ->
   70% confidence, dual mapping (found ID primary, dead ID secondary)
4. UNANSWERED: the chunk's request failed after retries, so the ID has no entry
   at all -> nothing is concluded and nothing is written; the next import asks
   again. This is deliberately NOT scenario 3: a single failed request covers
   ~50 IDs, and calling them dead would back them all off and cache a
   search-picked stand-in for every hinted one.

Scenario 2 is the most reliable - Spotify explicitly confirms the identity link.
Scenario 3 is approximate - title similarity may match a different recording (live, remix).
The secondary mapping in both cases ensures future imports with the old ID resolve
instantly via the bulk lookup fast path (no API call needed).
"""

import asyncio
from collections.abc import Mapping, Sequence, Set as AbstractSet
from typing import ClassVar, Final, override

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
from src.domain.repositories.connector import ConnectorMappingSpec
from src.domain.repositories.resolution import ResolutionDecision
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.inward_track_resolver import (
    InwardTrackResolver,
    ReuseMetadata,
    TrackResolutionMetrics,
    persist_bulk_with_item_fallback,
)
from src.infrastructure.connectors._shared.isrc import normalize_isrc
from src.infrastructure.connectors.spotify import SpotifyConnector
from src.infrastructure.connectors.spotify.models import SpotifyTrack

from .utilities import create_track_from_spotify_data, search_and_evaluate_attempt

logger = get_logger(__name__)


# How far under the estimate a candidate may sit before it reads as a different
# version. Generous on purpose: the estimate is assembled from listening
# behaviour, not from a length field, so it absorbs crossfade trim at the tail
# of a completed play and the minor seek noise the median does not remove. The
# gap it still catches is the one that matters — a radio edit or a cover
# standing in for an album cut differs by far more than fifteen seconds.
VERSION_MISMATCH_TOLERANCE_MS: Final[int] = 15_000


@define(frozen=True, slots=True)
class FallbackHint:
    """What is known about a dead Spotify ID from the play rows that carry it.

    The GDPR export has no track-length field, so ``completed_play_ms_estimate``
    stands in for one: the median ``ms_played`` across the id's plays that ended
    ``trackdone``, i.e. ran to the end of the track. It is ``None`` when the
    batch holds no completed play for the id, and no length is then asserted.
    """

    artist_name: str
    track_name: str
    completed_play_ms_estimate: int | None = None


def _shortest_plausible_ms(completed_play_ms_estimate: int | None) -> int | None:
    """The shortest a candidate may be and still be the recording that played.

    One-directional, and applied before any candidate is ranked. A candidate
    LONGER than the estimate is unremarkable — the listener skipped, or the
    album cut runs past the single. A candidate materially SHORTER than a play
    that ran to completion is strong evidence of a different version, and no
    title or artist score can outweigh it: a 3:38 cover cannot be the track
    somebody once played to the end for 4:30.

    ``None`` when the batch held no completed play — nothing is asserted about
    a length that was never observed.
    """
    if completed_play_ms_estimate is None:
        return None
    return max(completed_play_ms_estimate - VERSION_MISMATCH_TOLERANCE_MS, 0)


@define(frozen=True, slots=True)
class _FallbackSearchResult:
    """Intermediate result from API search before DB persistence."""

    candidate: SpotifyTrack
    confidence: int
    similarity: float
    hint: FallbackHint


@define(frozen=True, slots=True)
class _IsrcCollisionReview:
    """A suspect ISRC collision to queue against the canonical that owns it."""

    owner: Track
    service_data: dict[str, JsonValue]


@define(frozen=True, slots=True)
class _ResolvedWrite:
    """One requested id's persist, decided before anything is written.

    Deciding is pure and happens once per id: ISRC reuse, suspect deferral,
    relink. The persist step then writes *this* and builds no payload of its
    own — once for the whole chunk, or one savepoint at a time over the same
    code when the chunk has to be isolated.
    """

    requested_id: str
    spotify_track: SpotifyTrack
    match_method: str
    confidence: int
    # ISRC dedup: an existing canonical already holds this recording, so
    # nothing new is created — only a mapping onto it.
    reuse_track: Track | None = None
    # Suspect collision: the ISRC is claimed by an owner whose duration
    # disagrees, so a review is queued and the contested ISRC withheld.
    review: _IsrcCollisionReview | None = None

    @property
    def current_id(self) -> str:
        """The id Spotify considers current — differs from the requested one
        on a relink, and on a search that stood a different recording in."""
        return self.spotify_track.id or self.requested_id

    @property
    def requested_id_is_stale(self) -> bool:
        """Does the requested id need its own secondary (cache) mapping?"""
        return self.current_id != self.requested_id

    @property
    def creates_canonical(self) -> bool:
        return self.reuse_track is None

    @property
    def is_redirect(self) -> bool:
        """A relink Spotify itself asserted — never a search stand-in.

        The fallback path also returns a track whose id differs from the one
        asked about, but that is *our* substitution, not Spotify's assertion
        of identity, and counting it as a redirect would report a rescue as a
        relink in both the metrics and the play context.
        """
        return (
            self.match_method == MatchMethod.DIRECT_IMPORT
            and self.requested_id_is_stale
        )


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

    @staticmethod
    def _canonical_payload(write: _ResolvedWrite) -> Track:
        """The canonical this write creates, built from the Spotify payload.

        Keyed on the *current* id: that is the identifier the new canonical's
        denormalized fast-path column has to carry, even when the id asked
        about was a stale one. A suspect ISRC is stripped here — the owner
        keeps it, and the review decides later whether the two are one
        recording.
        """
        track_data = create_track_from_spotify_data(
            write.current_id, write.spotify_track
        )
        if write.review is not None and track_data.isrc:
            track_data = evolve(track_data, isrc=None)
        return track_data

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
        Dead IDs (returned as null by the API) are resolved via artist+title
        search if fallback hints are available. Ids whose *request* failed are
        excluded from every conclusion below — see ``answered_ids``.
        """
        fetch = await self._spotify_connector.get_tracks_by_ids(missing_ids)
        spotify_metadata = fetch.tracks

        # An id whose chunk request failed has told us nothing, and every
        # conclusion this method draws from absence — no-match backoff, and a
        # SEARCH_FALLBACK stand-in cached permanently against a hinted id —
        # would be drawn from a request that never landed. One 5xx covers ~50
        # ids, so these are dropped from the classification entirely: they fall
        # out of ``result``, count as ``failed`` in the metrics the base class
        # derives, and the next import asks about them again at full strength.
        answered_ids = [
            spotify_id
            for spotify_id in missing_ids
            if spotify_id not in fetch.unanswered
        ]
        if fetch.unanswered:
            logger.warning(
                f"Spotify answered for {len(answered_ids)}/{len(missing_ids)} ids — "
                f"{len(fetch.unanswered)} left unanswered by a failed batch request; "
                f"no backoff or fallback recorded for them",
                unanswered=len(fetch.unanswered),
                requested=len(missing_ids),
            )

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

        writes = [
            self._plan_direct_write(
                spotify_id, spotify_metadata[spotify_id], existing_by_isrc
            )
            for spotify_id in answered_ids
            if spotify_id in spotify_metadata
        ]
        result, failed_ids = await self._persist_writes(writes, uow, user_id=user_id)

        absent_ids = self._absent_from(answered_ids, spotify_metadata)
        if absent_ids:
            await self._note_absent_ids(absent_ids, uow, user_id=user_id)

        dead_ids = self._substitutable_from(
            answered_ids, spotify_metadata, result, failed_ids
        )
        if dead_ids and self._fallback_hints:
            fallback_tracks = await self._fallback_resolve_by_search(
                dead_ids, uow, user_id=user_id
            )
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

        ``requested`` must already have the unanswered ids taken out of it: an
        id whose chunk request never landed is absent from ``returned`` for a
        reason that has nothing to do with the id. The caller does that
        filtering (``answered_ids``) so this stays a pure membership rule.
        """
        return [spotify_id for spotify_id in requested if spotify_id not in returned]

    @staticmethod
    def _substitutable_from(
        requested: list[str],
        returned: Mapping[str, SpotifyTrack],
        resolved: Mapping[str, Track],
        write_failed: AbstractSet[str] = frozenset(),
    ) -> list[str]:
        """Unresolved ids a search may stand in for — never a restricted one.

        Substitution answers "the provider cannot account for this id". Spotify
        has accounted for a restricted track and said it exists but is
        unplayable here, so searching up a stand-in would answer a question
        nobody asked: the user would find their track quietly replaced by a
        different master because they were travelling.

        ``write_failed`` carries the ids Spotify answered for whose write its
        savepoint then rolled back. They are unresolved only because the
        database refused this once — the provider vouched for them just as
        plainly as for a restricted one. A failed write means "retry next
        import", never "substitute": treating it as death would commit a
        SEARCH_FALLBACK mapping onto a search-picked recording for a track
        whose real id is alive.

        Ids the provider never answered for are excluded upstream, for the same
        reason and more forcefully: a failed chunk request would otherwise hand
        a search-picked stand-in a durable SEARCH_FALLBACK mapping for every id
        in the chunk.
        """
        return [
            spotify_id
            for spotify_id in requested
            if spotify_id not in resolved
            and spotify_id not in write_failed
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

    @staticmethod
    def _plan_direct_write(
        spotify_id: str,
        spotify_track: SpotifyTrack,
        existing_by_isrc: dict[str, Track],
    ) -> _ResolvedWrite:
        """Decide what one API-answered id should persist as. Pure — no I/O.

        Three outcomes, exactly as the per-item resolver used to take them
        inline: reuse the canonical that already owns this ISRC, defer a
        suspect collision to review and create a distinct canonical without
        the contested ISRC, or create a plain new canonical.
        """
        isrc = None
        if spotify_track.external_ids and spotify_track.external_ids.isrc:
            isrc = normalize_isrc(spotify_track.external_ids.isrc)

        existing_track = existing_by_isrc.get(isrc) if isrc else None
        if existing_track is not None and isrc is not None:
            duration_diff_ms = compute_duration_diff_ms(
                spotify_track.duration_ms, existing_track.duration_ms
            )
            if not assess_isrc_match_reliability(duration_diff_ms).suspect:
                return _ResolvedWrite(
                    requested_id=spotify_id,
                    spotify_track=spotify_track,
                    match_method=MatchMethod.ISRC_MATCH,
                    confidence=MatchMethod.ISRC_MATCH_CONFIDENCE,
                    reuse_track=existing_track,
                )

            # Suspect collision — don't silently merge. Queue a review against
            # the ISRC owner and create a distinct canonical without the ISRC.
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
            logger.info(
                f"ISRC suspect: queueing review for spotify:{spotify_id} vs canonical "
                f"{existing_track.id} (ISRC={isrc}, duration_diff_ms={duration_diff_ms})"
            )
            return _ResolvedWrite(
                requested_id=spotify_id,
                spotify_track=spotify_track,
                match_method=MatchMethod.DIRECT_IMPORT,
                confidence=100,
                review=_IsrcCollisionReview(
                    owner=existing_track, service_data=service_data
                ),
            )

        return _ResolvedWrite(
            requested_id=spotify_id,
            spotify_track=spotify_track,
            match_method=MatchMethod.DIRECT_IMPORT,
            confidence=100,
        )

    async def _persist_writes(
        self,
        writes: list[_ResolvedWrite],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> tuple[dict[str, Track], set[str]]:
        """Write a chunk's resolutions — one savepoint for all, per id on failure.

        The bulk attempt collapses what a created track used to cost — 25
        statements for a plain creation and 42 for a relink, counted against a
        real database — into ~21-26 for the whole chunk, regardless of how
        many ids are in it. At the measured 26ms Fly→Neon round trip that is
        the difference between the ~50s a 50-play chunk was spending in the
        database and well under a second.

        When it fails, the chunk is rewritten one savepoint per id — the same
        bulk write, on a one-element chunk. That is the slow shape, and
        deliberately so: the bulk statements are all-or-nothing, so a single
        poisoned row would otherwise cost the chunk, while the per-id retry
        costs it only the id that is actually bad.

        Returns the resolved tracks and the ids whose write was rolled back —
        never absent ids, which the caller classifies separately.
        """

        def _log_failed_write(write: _ResolvedWrite, e: Exception) -> None:
            logger.error(
                f"Failed to create track for {write.requested_id}: {e}",
                exc_info=e,
            )

        # ``on_persisted`` fires only after a savepoint releases: the tracker
        # sets describe rows, and until then the rows they describe may still
        # be discarded — a surviving entry would inflate ``metrics.redirects``
        # and make ``get_resolution_method`` report SPOTIFY_REDIRECT for a
        # track that ended up search-fallback-resolved.
        return await persist_bulk_with_item_fallback(
            writes,
            uow,
            persist=lambda chunk: self._persist_writes_bulk(
                chunk, uow, user_id=user_id
            ),
            write_key=lambda write: write.requested_id,
            describe="resolved Spotify tracks",
            on_persisted=self._remember_provenance,
            on_item_failure=_log_failed_write,
        )

    async def _persist_writes_bulk(
        self,
        writes: Sequence[_ResolvedWrite],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> dict[str, Track]:
        """Write every resolution in the chunk through the batch primitives.

        Four steps, each one statement group for the whole chunk: the suspect
        reviews, one INSERT for the new canonicals, one mapping assertion
        carrying primary, stale-id and ISRC-reuse mappings together, and one
        pair of statements for the relink events.
        """
        connector_repo = uow.get_connector_repository()

        # Suspect ISRC collisions stay per-item. Each queues a review against
        # a *different* owner and runs the matcher over it, so there is no
        # shared statement to batch into — and one only arises when an ISRC is
        # already claimed by a recording more than ten seconds different.
        for write in writes:
            if write.review is not None:
                _ = await connector_repo.queue_isrc_collision_review(
                    write.review.owner,
                    "spotify",
                    write.requested_id,
                    write.review.service_data,
                    user_id=user_id,
                )

        created = [write for write in writes if write.creates_canonical]
        saved = await uow.get_track_repository().save_tracks([
            self._canonical_payload(write) for write in created
        ])
        canonicals: dict[str, Track] = {
            write.requested_id: track
            for write, track in zip(created, saved, strict=True)
        }
        canonicals.update({
            write.requested_id: write.reuse_track
            for write in writes
            if write.reuse_track is not None
        })

        _ = await connector_repo.map_tracks_to_connectors(
            self._mapping_batch(writes, canonicals)
        )

        await self._record_substitutions(
            [write for write in writes if write.is_redirect],
            canonicals,
            uow,
            user_id=user_id,
        )
        return canonicals

    @staticmethod
    def _mapping_batch(
        writes: Sequence[_ResolvedWrite],
        canonicals: Mapping[str, Track],
    ) -> list[ConnectorMappingSpec]:
        """Every mapping the chunk owes, each saying whether it holds primacy.

        Primary mappings and ISRC-reuse mappings take primacy; a relink's
        stale-id mapping deliberately does not — it exists to make the old id
        resolve from cache, not to describe the track.
        """
        specs: list[ConnectorMappingSpec] = []
        for write in writes:
            track = canonicals[write.requested_id]
            specs.append(
                ConnectorMappingSpec(
                    track=track,
                    connector="spotify",
                    connector_id=(
                        write.requested_id
                        if write.reuse_track is not None
                        else write.current_id
                    ),
                    match_method=write.match_method,
                    confidence=write.confidence,
                    metadata=write.spotify_track.model_dump(),
                    primary=True,
                )
            )

            if write.reuse_track is None and write.requested_id_is_stale:
                specs.append(
                    ConnectorMappingSpec(
                        track=track,
                        connector="spotify",
                        connector_id=write.requested_id,
                        match_method=SpotifyInwardResolver._STALE_ID_METHODS[
                            write.match_method
                        ],
                        confidence=write.confidence,
                    )
                )
        return specs

    def _remember_provenance(self, writes: Sequence[_ResolvedWrite]) -> None:
        """Record which ids were relinked or ISRC-deferred, after the write landed.

        Called once per savepoint that released: over the whole chunk on the
        bulk path, over the single write on the isolating one. Never before —
        an unreleased savepoint's rows can still be discarded.
        """
        for write in writes:
            if write.is_redirect:
                self._redirect_resolved_ids.add(write.requested_id)
            if write.review is not None:
                self._isrc_suspect_deferred_ids.add(write.requested_id)

    async def _record_substitutions(
        self,
        writes: list[_ResolvedWrite],
        canonicals: Mapping[str, Track],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> None:
        """Record relinks as ``substituted`` events — never supersessions.

        Batched over the chunk: one connector-track lookup and one event
        insert however many ids relinked, because both seams already take
        collections and a relink is common enough for the per-id form to have
        cost two round trips each.

        Spotify's own documentation says mutations must operate on the
        *original* id, so the requested id stays valid and retiring it would
        break writes and write-flap under a multi-market user. The dual mapping
        already caches both ids; this records the assertion that produced it.

        ``market`` records the one the batch fetch actually sent (``get_tracks_batched``
        passes ``settings.api.spotify_market`` on every request). Relinking only
        fires when a market is supplied, so the market in force is what makes a
        substitution interpretable later — the same requested id can relink to
        different returned ids in different markets (memo §10.2). The pair is
        detected by request/response correlation — ``linked_from`` was removed
        in Feb 2026 and the label never came back.

        The event names the *requested* id's connector track, not the returned
        one. ``substituted`` is a streak-resetting event, and the streak it has
        to reset belongs to the id that kept coming back absent — recording it
        against nothing (or against the substitute) left a relinked id
        accumulating suspicion it had already disproved.
        """
        if not writes:
            return
        recorder = uow.get_resolution_recorder()
        requested_ct = await recorder.connector_track_ids(
            [write.requested_id for write in writes], connector_name="spotify"
        )
        _ = await recorder.record(
            [
                ResolutionDecision(
                    event_type="substituted",
                    connector_name="spotify",
                    connector_track_id=requested_ct.get(write.requested_id),
                    track_id=canonicals[write.requested_id].id,
                    payload={
                        "requested_id": write.requested_id,
                        "returned_id": write.spotify_track.id,
                        "market": settings.api.spotify_market,
                        "detection": "id_mismatch",
                    },
                )
                for write in writes
            ],
            user_id=user_id,
        )

    async def _fallback_resolve_by_search(
        self,
        dead_ids: list[str],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> dict[str, Track]:
        """Resolve dead Spotify IDs via artist+title search.

        For each dead ID with a hint, searches Spotify, picks the best
        candidate by title similarity, and creates the track with reduced
        confidence. Creates both a primary mapping for the new ID and a
        secondary mapping for the dead ID (cache for future imports).

        API searches run concurrently (I/O-bound); the writes then go through
        the same chunk-bulk-then-per-item persist the direct path uses, so a
        rescued id costs the same handful of statements as a resolved one.
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

        # Step 2: DB writes (the session is not concurrency-safe, so these are
        # sequential regardless — batching them is what makes them cheap).
        result, _failed_ids = await self._persist_writes(
            [
                _ResolvedWrite(
                    requested_id=dead_id,
                    spotify_track=search_result.candidate,
                    match_method=MatchMethod.SEARCH_FALLBACK,
                    confidence=search_result.confidence,
                )
                for dead_id, search_result in search_results.items()
            ],
            uow,
            user_id=user_id,
        )
        for dead_id in result:
            search_result = search_results[dead_id]
            logger.info(
                f"Fallback resolved: {search_result.hint.artist_name} - {search_result.hint.track_name} "
                f"→ {search_result.candidate.name} (id: {search_result.candidate.id or dead_id}, "
                f"similarity: {search_result.similarity:.2f}, confidence: {search_result.confidence})"
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
            # The estimate is the only length evidence a dead id has, and it
            # enters the match twice: as the probe's duration, which the
            # evaluation service already knows how to score against a
            # candidate's, and as the floor below.
            hint_track = Track(
                title=hint.track_name,
                artists=[Artist(name=hint.artist_name)],
                duration_ms=hint.completed_play_ms_estimate,
            )
            attempt = await search_and_evaluate_attempt(
                self._spotify_connector,
                self._match_evaluation_service,
                hint_track,
                hint.artist_name,
                hint.track_name,
                # A durable rescue of one dead id, not a per-play search, so
                # one extra /search buys back the recall the quoted filters
                # cost wherever Spotify's spelling differs from the export's.
                widen=True,
                # Title similarity says nothing about who performed the track,
                # and the widened query does not constrain the artist at all —
                # "Johnny Cash - Hurt" ranks Nine Inch Nails' "Hurt" at
                # similarity 1.0. Accepting on similarity alone writes that as
                # this dead id's durable primary SEARCH_FALLBACK mapping and
                # counts it a rescue. The full evaluation rules on both passes.
                require_success=True,
                min_candidate_duration_ms=_shortest_plausible_ms(
                    hint.completed_play_ms_estimate
                ),
                min_similarity=SpotifyConstants.FALLBACK_SIMILARITY_THRESHOLD,
                # require_success is not artist-proof (see the constant's
                # rationale) — ~7% of dead ids carry no duration evidence, and
                # for them this floor is the only thing standing between a
                # same-title wrong-artist recording and a durable mapping.
                min_artist_similarity=(
                    SpotifyConstants.FALLBACK_ARTIST_SIMILARITY_THRESHOLD
                ),
                fallback_connector_id=dead_id,
                limit=SpotifyConstants.SEARCH_MAX_LIMIT,
            )
            if attempt.match is None:
                # WARNING, not debug, and carrying the queries verbatim: a
                # track that is genuinely gone and a query malformed by bad
                # export data produce the same empty result, and only the
                # strings that were actually sent tell the two apart.
                logger.warning(
                    f"Fallback search found no viable match for "
                    f"{hint.artist_name} - {hint.track_name} (spotify:{dead_id})",
                    artist=hint.artist_name,
                    title=hint.track_name,
                    dead_id=dead_id,
                    queries=list(attempt.queries),
                )
                return None

            return _FallbackSearchResult(
                candidate=attempt.match.candidate,
                confidence=attempt.match.match_result.confidence,
                similarity=attempt.match.similarity,
                hint=hint,
            )
        except Exception as e:
            logger.error(
                f"Fallback search failed for {dead_id} ({hint.artist_name} - {hint.track_name}): {e}",
                exc_info=True,
            )
            return None
