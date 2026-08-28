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

Before any of those outcomes creates a canonical, the provider's own metadata is
checked against the canonicals that already exist — see
``_plan_identity_reuse``. The shared reuse step upstream asks the same question
of the *export's* metadata, which is a different question: an id whose export
row spells the title differently (or not at all) reaches creation anyway, and
the track Spotify then describes can be the recording an existing canonical
already holds.

Persistence runs through the shared planned-write pipeline
(``WritePlanningResolver``); this module owns the relink/fallback/folding arms,
payload extraction, and the ``id_mismatch`` detection label.
"""

from collections import Counter
from collections.abc import Mapping, Sequence, Set as AbstractSet
from enum import StrEnum
from typing import Final, override

from attrs import define, evolve

from src.config import get_logger, settings
from src.config.constants import MatchMethod, SpotifyConstants
from src.config.telemetry import phase
from src.domain.entities import Artist, Track
from src.domain.entities.shared import JsonValue
from src.domain.matching.content_digest import DigestSide
from src.domain.matching.evaluation_service import TrackMatchEvaluationService
from src.domain.matching.recording_identity import (
    RecordingDescription,
    describe_track,
    describes_same_recording,
    identity_key,
)
from src.domain.matching.types import RawProviderMatch
from src.domain.repositories.resolution import ResolutionDecision
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.fan_out import bounded_fan_out
from src.infrastructure.connectors._shared.inward_track_resolver import (
    PlannedWrite,
    ReuseMetadata,
    TrackResolutionMetrics,
    WritePlanningResolver,
    plan_isrc_write,
)
from src.infrastructure.connectors._shared.successor_resolution import (
    SuccessorAssertion,
)
from src.infrastructure.connectors.spotify import SpotifyConnector
from src.infrastructure.connectors.spotify.models import SpotifyTrack

from .utilities import (
    create_track_from_spotify_data,
    normalized_spotify_isrc,
    search_and_evaluate_attempt,
)

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


class Provenance(StrEnum):
    """How an id resolved, beyond a plain direct import.

    One label per id — the shapes are mutually exclusive but for one corner:
    a relinked id whose ISRC collision is also suspect carries both a
    redirect and a deferral, and the redirect label wins there because it
    feeds the metrics and ``get_resolution_method``.
    """

    REDIRECT = "redirect"
    FALLBACK = "fallback"
    ISRC_DEFERRED = "isrc_deferred"
    WRITE_FAILED = "write_failed"


def _is_redirect(write: PlannedWrite[SpotifyTrack]) -> bool:
    """A relink Spotify itself asserted — never a search stand-in.

    The fallback path also returns a track whose id differs from the one
    asked about, but that is *our* substitution, not Spotify's assertion
    of identity, and counting it as a redirect would report a rescue as a
    relink in both the metrics and the play context. Whether the relink
    went on to create a canonical or reuse one is immaterial: the provider
    asserted the same thing either way.
    """
    return (
        write.match_method
        in (MatchMethod.DIRECT_IMPORT, MatchMethod.DIRECT_IMPORT_STALE_ID)
        and write.requested_id_is_stale
    )


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


def _describe_payload(spotify_track: SpotifyTrack) -> RecordingDescription:
    """A Spotify payload as the same-recording question sees it."""
    return RecordingDescription(
        title=spotify_track.name,
        artist=spotify_track.artists[0].name if spotify_track.artists else "",
        duration_ms=spotify_track.duration_ms,
    )


def _payload_as_candidate(spotify_track: SpotifyTrack) -> Track:
    """A Spotify payload seen as the canonical it would become.

    Comparison only — never saved, and deliberately not
    ``create_track_from_spotify_data``, whose job is to build the row and which
    raises on a payload it cannot. Planning must not be able to fail on a
    payload the savepointed persist would have isolated by itself.
    """
    return Track(
        title=spotify_track.name,
        artists=[Artist(name=a.name) for a in spotify_track.artists if a.name],
        duration_ms=spotify_track.duration_ms,
    )


@define(frozen=True, slots=True)
class _FallbackSearchResult:
    """Intermediate result from API search before DB persistence."""

    candidate: SpotifyTrack
    confidence: int
    similarity: float
    hint: FallbackHint


@define(frozen=True, slots=True)
class _FoldedWrite:
    """An id that reuses a canonical *another write in this chunk* creates.

    Two ids can describe one recording and both be unknown to the database —
    a reissue and its original arrive in the same chunk, and neither can find
    the other by lookup because neither exists yet. The follower's write cannot
    name its canonical until the leader's has been saved, so it is held back
    and persisted as a plain reuse once the leader's id has resolved.
    """

    write: PlannedWrite[SpotifyTrack]
    leader_id: str
    confidence: int


class SpotifyInwardResolver(WritePlanningResolver[SpotifyTrack, FallbackHint]):
    """Resolves Spotify track IDs → canonical tracks.

    Uses Spotify's batch API (get_tracks_by_ids, up to 50 at once)
    for efficient creation of missing tracks. Detects redirects (where
    Spotify returns a different .id than requested) and creates dual
    mappings. Falls back to artist+title search for true dead IDs
    when FallbackHints are provided.
    """

    _spotify_connector: SpotifyConnector
    _fallback_hints: dict[str, FallbackHint]
    _provenance: dict[str, Provenance]

    def __init__(
        self,
        spotify_connector: SpotifyConnector,
        match_evaluation_service: TrackMatchEvaluationService | None = None,
    ):
        super().__init__(match_evaluation_service)
        self._spotify_connector = spotify_connector
        self._fallback_hints = {}
        self._provenance = {}

    def _ids_with(self, provenance: Provenance) -> set[str]:
        """The ids this pass recorded under one provenance label."""
        return {
            spotify_id
            for spotify_id, label in self._provenance.items()
            if label is provenance
        }

    @property
    def fallback_resolved_ids(self) -> set[str]:
        """IDs that were resolved via search fallback (for downstream tagging)."""
        return self._ids_with(Provenance.FALLBACK)

    @property
    def redirect_resolved_ids(self) -> set[str]:
        """IDs that were resolved via Spotify redirect (returned different .id)."""
        return self._ids_with(Provenance.REDIRECT)

    @property
    def isrc_suspect_deferred_ids(self) -> set[str]:
        """IDs whose ISRC collision was suspect and deferred to review.

        Populated when the ISRC arm detects a duration mismatch large enough
        to distrust the ISRC match — the incoming ID gets its own canonical
        (ISRC withheld) and a review is queued against the existing owner,
        rather than silently merging.
        """
        return self._ids_with(Provenance.ISRC_DEFERRED)

    def get_resolution_method(self, spotify_id: str) -> str:
        """How was this ID resolved? For downstream context tagging."""
        match self._provenance.get(spotify_id):
            case Provenance.REDIRECT:
                return MatchMethod.SPOTIFY_REDIRECT
            case Provenance.FALLBACK:
                return MatchMethod.SEARCH_FALLBACK
            case _:
                return MatchMethod.PLAY_RESOLVER

    @property
    @override
    def connector_name(self) -> str:
        return "spotify"

    @override
    def _normalize_id(self, raw_id: str) -> str:
        return raw_id

    @override
    def _begin_resolution(self, hints: Mapping[str, FallbackHint]) -> None:
        """Stash the pass's fallback hints and reset provenance tracking."""
        super()._begin_resolution(hints)
        self._fallback_hints = dict(hints)
        self._provenance = {}

    @override
    def _decorate_metrics(
        self, metrics: TrackResolutionMetrics
    ) -> TrackResolutionMetrics:
        """Fold the redirect/fallback tracking the base cannot see into the metrics.

        ``write_failed`` deliberately replaces the base tally: an orphaned
        folded follower fails without a savepoint of its own, so only this
        count sees it.
        """
        counts = Counter(self._provenance.values())
        return evolve(
            metrics,
            redirects=counts[Provenance.REDIRECT],
            fallbacks=counts[Provenance.FALLBACK],
            write_failed=counts[Provenance.WRITE_FAILED],
        )

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

    @override
    def _canonical_payload(
        self, write: PlannedWrite[SpotifyTrack], *, user_id: str
    ) -> Track:
        """The canonical this write creates, built from the Spotify payload.

        Keyed on the *current* id: that is the identifier the new canonical's
        denormalized fast-path column has to carry, even when the id asked
        about was a stale one. A suspect ISRC is stripped here — the owner
        keeps it, and the review decides later whether the two are one
        recording. ``user_id`` is threaded through to the Track itself:
        every row this payload becomes is user-scoped, and a payload built
        without the tenant lands under ``"default"``.
        """
        track_data = create_track_from_spotify_data(
            write.current_id, write.payload, user_id=user_id
        )
        if write.review is not None and track_data.isrc:
            track_data = evolve(track_data, isrc=None)
        return track_data

    @override
    def _mapping_metadata(self, write: PlannedWrite[SpotifyTrack]) -> dict[str, object]:
        return write.payload.model_dump()

    @override
    def _successor_assertion(
        self, write: PlannedWrite[SpotifyTrack], track: Track
    ) -> SuccessorAssertion | None:
        """A relink's ``substituted`` event — never a supersession.

        Spotify's own documentation says mutations must operate on the
        *original* id, so the requested id stays valid and retiring it would
        break writes and write-flap under a multi-market user. The dual
        mapping already caches both ids; this records the assertion that
        produced it.

        ``market`` records the one the batch fetch actually sent
        (``get_tracks_batched`` passes ``settings.api.spotify_market`` on
        every request). Relinking only fires when a market is supplied, so
        the market in force is what makes a substitution interpretable later
        — the same requested id can relink to different returned ids in
        different markets (memo §10.2). The pair is detected by
        request/response correlation — ``linked_from`` was removed in Feb
        2026 and the label never came back — hence ``"id_mismatch"``.
        """
        if not _is_redirect(write):
            return None
        return SuccessorAssertion(
            requested_id=write.requested_id,
            returned_id=write.current_id,
            detection="id_mismatch",
            track_id=track.id,
            extra={"market": settings.api.spotify_market},
        )

    @override
    def _on_writes_persisted(
        self, writes: Sequence[PlannedWrite[SpotifyTrack]]
    ) -> None:
        """Record which ids were relinked or ISRC-deferred, after the write landed.

        Called once per savepoint that released: over the whole chunk on the
        bulk path, over the single write on the isolating one. Never before —
        an unreleased savepoint's rows can still be discarded, and a surviving
        entry would inflate ``metrics.redirects`` and make
        ``get_resolution_method`` report SPOTIFY_REDIRECT for a track that
        ended up search-fallback-resolved.
        """
        for write in writes:
            if _is_redirect(write):
                self._provenance[write.requested_id] = Provenance.REDIRECT
            elif write.review is not None:
                self._provenance[write.requested_id] = Provenance.ISRC_DEFERRED

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
        async with phase("api"):
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
            isrc = normalized_spotify_isrc(spotify_track)
            if isrc:
                isrc_to_spotify_id[isrc] = spotify_id

        existing_by_isrc: dict[str, Track] = {}
        if isrc_to_spotify_id:
            existing_by_isrc = await uow.get_track_repository().find_tracks_by_isrcs(
                list(isrc_to_spotify_id.keys()), user_id=user_id
            )

        existing_by_current_id = await self._canonicals_holding_current_ids(
            answered_ids, spotify_metadata, uow, user_id=user_id
        )

        writes = [
            self._plan_direct_write(
                spotify_id,
                spotify_metadata[spotify_id],
                existing_by_isrc,
                existing_by_current_id,
            )
            for spotify_id in answered_ids
            if spotify_id in spotify_metadata
        ]
        writes, folded = await self._plan_identity_reuse(writes, uow, user_id=user_id)
        result, failed_ids = await self._persist_planned_writes(
            writes, uow, user_id=user_id
        )
        if folded:
            followed, follower_failures = await self._persist_folded(
                folded, result, uow, user_id=user_id
            )
            result.update(followed)
            failed_ids |= follower_failures
        if failed_ids:
            await self._note_write_failures(failed_ids, uow, user_id=user_id)

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
            for dead_id in fallback_tracks:
                self._provenance[dead_id] = Provenance.FALLBACK

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
            if self._provenance.get(spotify_id) is not Provenance.FALLBACK
        ]
        if directly_resolved:
            _ = await uow.get_resolution_recorder().clear_negatives(
                directly_resolved, user_id=user_id, connector_name=self.connector_name
            )

        return result

    async def _note_write_failures(
        self, failed_ids: AbstractSet[str], uow: UnitOfWorkProtocol, *, user_id: str
    ) -> None:
        """Leave a durable trace for ids the provider answered but we could not store.

        An event, never a ``no_match`` negative. The backoff clock exists for
        ids the provider cannot account for; this id it vouched for, and
        suppressing the next attempt would turn a transient write failure into
        a permanent hole. The event is what makes these countable — until one
        existed, a rolled-back chunk was indistinguishable from a batch of dead
        identifiers in every metric the run recorded.
        """
        for spotify_id in failed_ids:
            self._provenance[spotify_id] = Provenance.WRITE_FAILED
        recorder = uow.get_resolution_recorder()
        # Savepointed, because this runs *after* a write the database already
        # refused: the diagnostic must not be able to widen one rejected id
        # into a rejected chunk, and an unisolated failure here would abort the
        # transaction the absent-id bookkeeping below still has to use.
        try:
            async with uow.savepoint():
                connector_tracks = await recorder.connector_track_ids(
                    sorted(failed_ids), connector_name=self.connector_name
                )
                _ = await recorder.record(
                    [
                        ResolutionDecision(
                            event_type="write_failed",
                            connector_name=self.connector_name,
                            connector_track_id=connector_tracks.get(spotify_id),
                            payload={"requested_id": spotify_id},
                        )
                        for spotify_id in sorted(failed_ids)
                    ],
                    user_id=user_id,
                )
        except Exception as e:
            logger.warning(
                f"Could not record write-failure events for "
                f"{len(failed_ids)} Spotify ids: {e}",
                exc_info=True,
            )

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
            connector_name=self.connector_name,
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

    async def _canonicals_holding_current_ids(
        self,
        answered_ids: list[str],
        spotify_metadata: Mapping[str, SpotifyTrack],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> dict[str, Track]:
        """Canonicals already mapped to the ids the provider calls current.

        Only a relink can be found here. An id whose current id is itself was
        asked about by the mapping lookup and would have resolved there, so
        reaching this method at all means the requested id is unmapped — and
        the id being probed is a different one.
        """
        current_ids = {
            track.id
            for spotify_id in answered_ids
            if (track := spotify_metadata.get(spotify_id)) is not None
            and track.id
            and track.id != spotify_id
        }
        if not current_ids:
            return {}
        held = await uow.get_connector_repository().find_tracks_by_connectors(
            [(self.connector_name, current_id) for current_id in sorted(current_ids)],
            user_id=user_id,
        )
        return {current_id: track for (_, current_id), track in held.items()}

    def _plan_direct_write(
        self,
        spotify_id: str,
        spotify_track: SpotifyTrack,
        existing_by_isrc: dict[str, Track],
        existing_by_current_id: Mapping[str, Track],
    ) -> PlannedWrite[SpotifyTrack]:
        """Decide what one API-answered id should persist as. Pure — no I/O.

        Four outcomes: alias the requested id onto the canonical that already
        holds the id Spotify relinked it to, or one of the shared ISRC arms —
        reuse the canonical that owns this ISRC, defer a suspect collision to
        review, or create a plain new canonical.

        The relink arm is first because the current id is the provider's own
        assertion of identity, which outranks an ISRC match. It also outranks
        an ISRC *miss*: a relink onto a remaster carries the original
        recording's ISRC while the canonical holding it carries the remaster's,
        so the ISRC arm cannot see a pairing Spotify has stated outright.
        """
        current_id = spotify_track.id or spotify_id
        relink_owner = existing_by_current_id.get(current_id)
        if relink_owner is not None:
            # The mapping this write asserts only aliases the requested id
            # onto an already-mapped canonical, so it never takes primacy.
            return PlannedWrite(
                requested_id=spotify_id,
                current_id=current_id,
                payload=spotify_track,
                match_method=MatchMethod.DIRECT_IMPORT_STALE_ID,
                confidence=MatchMethod.DIRECT_IMPORT_CONFIDENCE,
                reuse_track=relink_owner,
                primary=False,
            )

        isrc = normalized_spotify_isrc(spotify_track)
        if isrc is None:
            return PlannedWrite(
                requested_id=spotify_id,
                current_id=current_id,
                payload=spotify_track,
                match_method=MatchMethod.DIRECT_IMPORT,
                confidence=MatchMethod.DIRECT_IMPORT_CONFIDENCE,
            )

        primary_artist = spotify_track.artists[0].name if spotify_track.artists else ""
        service_data: dict[str, JsonValue] = {
            "title": spotify_track.name,
            "artist": primary_artist,
            "artists": [a.name for a in spotify_track.artists],
            "duration_ms": spotify_track.duration_ms,
            "isrc": isrc,
        }
        return plan_isrc_write(
            connector=self.connector_name,
            requested_id=spotify_id,
            current_id=current_id,
            payload=spotify_track,
            duration_ms=spotify_track.duration_ms,
            isrc=isrc,
            existing_by_isrc=existing_by_isrc,
            service_data=service_data,
        )

    def _identity_reuse_confidence(
        self, candidate: Track, spotify_track: SpotifyTrack
    ) -> int | None:
        """Confidence for reusing ``candidate`` for this payload, or None to refuse.

        ``describes_same_recording`` is the gate; the evaluation service only
        prices what it has already accepted. The duration half of that gate is
        what actually separates the two populations here: Fellegi-Sunter
        saturates on an exact artist+title agreement — a pair four minutes
        apart in length still scores 100 — so the matcher can say which
        canonical is the candidate but not whether it is the same recording.
        Asking it anyway keeps the number the mapping stores derived from the
        model rather than from a fresh constant.
        """
        if not describes_same_recording(
            describe_track(candidate), _describe_payload(spotify_track)
        ):
            return None
        match_result = self._match_evaluation_service.evaluate_single_match(
            candidate,
            RawProviderMatch(
                connector_id=spotify_track.id or "",
                match_method=MatchMethod.CANONICAL_REUSE,
                service_data={
                    "title": spotify_track.name,
                    "artist": spotify_track.artists[0].name,
                    "duration_ms": spotify_track.duration_ms,
                },
            ),
            self.connector_name,
        )
        return match_result.confidence if match_result.success else None

    async def _plan_identity_reuse(
        self,
        writes: list[PlannedWrite[SpotifyTrack]],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> tuple[list[PlannedWrite[SpotifyTrack]], list[_FoldedWrite]]:
        """Refuse to mint a second canonical for a recording already described.

        The reuse step upstream probes with the *export's* artist and title;
        this one probes with the provider's, after the provider has answered.
        They are not the same probe, and the gap between them is a canonical:
        an export row spelling a title "Syvlia Says (Mind Enterprises Remix)"
        finds nothing, and the track Spotify then describes is titled "Sylvia
        Says" — normalization-equal to a canonical that already exists, keyed
        on a different ISRC, and so created again. A remaster never shares its
        original's ISRC, which is why an ISRC check alone can never see the
        pairing.

        Only a plain creation is eligible, and the exclusions are the point:

        - a **relink** or an **ISRC reuse** already resolved to a canonical, on
          evidence that outranks a name match.
        - a **suspect ISRC collision** deliberately creates a distinct canonical
          and queues a review; folding it into some third canonical on a name
          match would decide by the back door exactly what that review exists
          to put in front of a person.
        - a **stale requested id** stays out so the redirect path keeps its
          shape — its ``substituted`` event and dual mapping are the provider's
          own assertion of identity, and a reuse write records neither.

        Returns the writes to persist now, and the ones waiting on a leader.
        """
        eligible = [
            write
            for write in writes
            if write.creates_canonical
            and write.review is None
            and not write.requested_id_is_stale
            and write.payload.artists
            and write.payload.artists[0].name
        ]
        if not eligible:
            return writes, []

        owners = await uow.get_track_repository().find_tracks_by_title_artist(
            [(write.payload.name, write.payload.artists[0].name) for write in eligible],
            user_id=user_id,
        )

        reused: dict[str, PlannedWrite[SpotifyTrack]] = {}
        folded: list[_FoldedWrite] = []
        # Leaders bucket on their identity key: equality on it is a necessary
        # condition of the same-recording predicate, so a leader in another
        # bucket can never fold a later write — comparing against it would
        # rebuild its comparison Track only to refuse. Within a bucket the
        # accumulation order is chunk order, so the first id to describe a
        # recording keeps the canonical and every later one folds onto it;
        # each leader's comparison Track is built once, when it is appended.
        leaders: dict[tuple[str, str], list[tuple[str, Track]]] = {}
        for write in eligible:
            payload = write.payload
            owner = owners.get((payload.name.lower(), payload.artists[0].name.lower()))
            owner_confidence = (
                self._identity_reuse_confidence(owner, payload)
                if owner is not None
                else None
            )
            if owner is not None and owner_confidence is not None:
                logger.info(
                    f"Identity reuse: spotify:{write.requested_id} describes the "
                    f"recording canonical {owner.id} already holds "
                    f"(ISRC {normalized_spotify_isrc(payload)} vs {owner.isrc})"
                )
                reused[write.requested_id] = evolve(
                    write,
                    match_method=MatchMethod.CANONICAL_REUSE,
                    confidence=owner_confidence,
                    reuse_track=owner,
                )
                continue

            bucket = leaders.setdefault(identity_key(_describe_payload(payload)), [])
            fold = self._fold_onto_leader(write, bucket)
            if fold is not None:
                logger.info(
                    f"Identity fold: spotify:{write.requested_id} describes the "
                    f"same recording as spotify:{fold.leader_id}, earlier in this "
                    f"chunk"
                )
                folded.append(fold)
                continue

            bucket.append((write.requested_id, _payload_as_candidate(payload)))

        held_back = {item.write.requested_id for item in folded}
        return [
            reused.get(write.requested_id, write)
            for write in writes
            if write.requested_id not in held_back
        ], folded

    def _fold_onto_leader(
        self,
        write: PlannedWrite[SpotifyTrack],
        bucket: Sequence[tuple[str, Track]],
    ) -> _FoldedWrite | None:
        """The earlier same-bucket write describing the same recording, if any."""
        for leader_id, leader_candidate in bucket:
            confidence = self._identity_reuse_confidence(
                leader_candidate, write.payload
            )
            if confidence is not None:
                return _FoldedWrite(
                    write=write,
                    leader_id=leader_id,
                    confidence=confidence,
                )
        return None

    async def _persist_folded(
        self,
        folded: Sequence[_FoldedWrite],
        resolved: Mapping[str, Track],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> tuple[dict[str, Track], set[str]]:
        """Map the held-back ids onto the canonicals their leaders created.

        A leader whose own write was rolled back leaves its followers with
        nothing to map onto. They join it in the failed set rather than falling
        through to creation: the chunk has just decided they are that recording,
        so minting a canonical for them now would write the duplicate this pass
        exists to prevent. The next import asks about them again and resolves
        them at the mapping lookup, against whichever writer won.
        """
        orphaned = {
            item.write.requested_id for item in folded if item.leader_id not in resolved
        }
        if orphaned:
            logger.warning(
                f"{len(orphaned)} Spotify ids reuse a canonical whose write was "
                f"rolled back — deferred to the next import"
            )
        followers = [
            evolve(
                item.write,
                match_method=MatchMethod.CANONICAL_REUSE,
                confidence=item.confidence,
                reuse_track=resolved[item.leader_id],
            )
            for item in folded
            if item.leader_id in resolved
        ]
        persisted, failed_ids = await self._persist_planned_writes(
            followers, uow, user_id=user_id
        )
        return persisted, failed_ids | orphaned

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
        async with phase("api"):
            found = await bounded_fan_out(
                hinted_ids,
                self._fallback_search_api,
                concurrency=settings.api.spotify.concurrency,
            )
        search_results = {
            dead_id: search_result
            for dead_id, search_result in zip(hinted_ids, found, strict=True)
            if search_result is not None
        }

        # Step 2: DB writes (the session is not concurrency-safe, so these are
        # sequential regardless — batching them is what makes them cheap).
        result, failed_ids = await self._persist_planned_writes(
            [
                PlannedWrite(
                    requested_id=dead_id,
                    current_id=search_result.candidate.id or dead_id,
                    payload=search_result.candidate,
                    match_method=MatchMethod.SEARCH_FALLBACK,
                    confidence=search_result.confidence,
                )
                for dead_id, search_result in search_results.items()
            ],
            uow,
            user_id=user_id,
        )
        if failed_ids:
            await self._note_write_failures(failed_ids, uow, user_id=user_id)
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
