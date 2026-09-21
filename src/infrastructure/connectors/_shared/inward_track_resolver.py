"""Shared 'resolve inward' pattern: external connector IDs → canonical tracks.

Every inward resolver follows a three-step pipeline:
1. **Mapping Lookup**: Bulk-fetch existing connector→track mappings (fast path)
2. **Canonical Reuse**: Match unresolved IDs against existing canonical tracks
3. **Track Creation**: Batch-create new tracks for remaining unresolved IDs

``InwardTrackResolver`` captures that shared pattern while letting subclasses
define connector-specific creation logic and metadata extraction for canonical
reuse (via the _extract_reuse_metadata hook). Every reuse-or-create decision
is the domain planner's (``domain.matching.canonical_resolution``): the
resolver runs the probes, hands the answers in and persists what comes out.

``WritePlanningResolver`` layers the shared planned-write persist pipeline on
top for connectors whose creation step plans immutable writes and persists
them through the batch primitives: save_tracks → map_tracks_to_connectors →
collision reviews → substitution events. Apple, Tidal and Spotify run on it.
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Generator, Hashable, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import cast

from attrs import Factory, define, evolve, field

from src.config import create_evaluation_service, get_logger
from src.domain.entities import Track
from src.domain.entities.match_review import MatchReview
from src.domain.entities.shared import JsonValue
from src.domain.entities.track_mapping import MatchMethod
from src.domain.matching.artist_resolution import (
    ArtistCreditSource,
    credit_source,
    credited_artists,
)
from src.domain.matching.canonical_resolution import (
    Described,
    Outcome,
    Refusal,
    ResolutionEvidence,
    Reuse,
    TrackResolutionRules,
    creation_of,
    evidence_of,
    owners_by_identity,
    plan_resolution,
    price_reuse,
    suspect_review,
    track_name_key,
    undecided_name_pairs,
)
from src.domain.matching.config import MatchingConfig
from src.domain.matching.evaluation_service import MatchEvaluationService
from src.domain.matching.recording_identity import RecordingDescription, identity_key
from src.domain.matching.types import evidence_number, final_score_of
from src.domain.repositories.connector import ConnectorMappingSpec
from src.domain.repositories.errors import (
    is_transient_contention,
    postgres_sqlstate,
)
from src.domain.repositories.resolution import ResolutionDecision
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.connectors._shared.successor_resolution import (
    SuccessorAssertion,
    record_substitutions,
    stale_id_mapping_spec,
)

logger = get_logger(__name__)


@define(slots=True)
class _DegradedTally:
    """How many bulk persists fell back to per-item retries this pass."""

    count: int = 0


_degraded_persists: ContextVar[_DegradedTally | None] = ContextVar(
    "mixd_degraded_persists", default=None
)


@contextmanager
def count_degraded_persists() -> Generator[_DegradedTally]:
    """Tally per-item fallbacks across one resolution pass.

    The degraded path is the only one that opens a savepoint per item, and a
    subxid is released on rollback but *retained* through commit — so its
    count tracks successes and is what would bind PostgreSQL's 64-subxid cache
    if the chunk grew. Surfacing it keeps that constraint observable instead of
    inferred.
    """
    tally = _DegradedTally()
    token = _degraded_persists.set(tally)
    try:
        yield tally
    finally:
        _degraded_persists.reset(token)


@define(slots=True)
class _WriteFailureTally:
    """Keys of writes rolled back across one resolution pass."""

    keys: set[object] = field(factory=set)


_write_failures: ContextVar[_WriteFailureTally | None] = ContextVar(
    "mixd_write_failures", default=None
)


@contextmanager
def collect_write_failures() -> Generator[_WriteFailureTally]:
    """Collect rolled-back write keys across one resolution pass.

    ``persist_bulk_with_item_fallback`` already isolates and reports the keys
    whose savepoint rolled back; this collector is how the base resolver sees
    them without every subclass threading a failure set back up. The count
    feeds ``TrackResolutionMetrics.write_failed`` — the ids that were
    answered for but not stored, as against dead identifiers.
    """
    tally = _WriteFailureTally()
    token = _write_failures.set(tally)
    try:
        yield tally
    finally:
        _write_failures.reset(token)


async def persist_bulk_with_item_fallback[TWrite, TKey, TPersisted](
    writes: Sequence[TWrite],
    uow: UnitOfWorkProtocol,
    *,
    persist: Callable[[Sequence[TWrite]], Awaitable[Mapping[TKey, TPersisted]]],
    write_key: Callable[[TWrite], TKey],
    describe: str,
    on_persisted: Callable[[Sequence[TWrite]], None] | None = None,
    on_item_failure: Callable[[TWrite, Exception], None] | None = None,
) -> tuple[dict[TKey, TPersisted], set[TKey]]:
    """Savepoint-bulk a chunk of planned writes, one savepoint per item on failure.

    The one skeleton every tolerated write loop persists through — Spotify's
    resolved-track chunks, Last.fm's, and the canonical-reuse mapping batch.
    The bulk statements are all-or-nothing, so a single poisoned row would
    otherwise cost the chunk; the per-item retry costs it only the item that
    is actually bad, and per-item IS ``persist`` on a one-element chunk — a
    single write stays the degenerate case of a batch, so the fast path and
    the isolating path cannot drift into storing different rows.

    ``on_persisted`` runs only after its savepoint has released — until then
    the rows any in-memory bookkeeping would describe may still be discarded.
    ``on_item_failure`` owns the per-item log line (callers differ on level
    and wording); the failed key is collected here regardless.

    Returns:
        (persisted map, keys whose write was rolled back).
    """
    if not writes:
        return {}, set()

    try:
        async with uow.savepoint():
            persisted = dict(await persist(writes))
    except Exception as e:
        # Contention is not a poisoned row: per-item splitting would re-block
        # once per item on the same held key. Re-raise — the import fails
        # loudly and the schedule-level retry (adaptive poller) reruns it.
        if is_transient_contention(e):
            logger.warning(
                f"Bulk persist of {len(writes)} {describe} hit transient "
                f"contention (SQLSTATE {postgres_sqlstate(e)}) — re-raising",
                sqlstate=postgres_sqlstate(e),
            )
            raise
        tally = _degraded_persists.get()
        if tally is not None:
            tally.count += 1
        logger.warning(
            f"Bulk persist of {len(writes)} {describe} failed — "
            f"retrying one savepoint per item: {e}",
            exc_info=True,
        )
    else:
        if on_persisted is not None:
            on_persisted(writes)
        return persisted, set()

    resolved: dict[TKey, TPersisted] = {}
    failed_keys: set[TKey] = set()
    for write in writes:
        try:
            async with uow.savepoint():
                persisted = dict(await persist([write]))
        except Exception as e:
            failed_keys.add(write_key(write))
            if on_item_failure is not None:
                on_item_failure(write, e)
        else:
            if on_persisted is not None:
                on_persisted([write])
            resolved.update(persisted)
    failure_tally = _write_failures.get()
    if failure_tally is not None:
        failure_tally.keys.update(failed_keys)
    return resolved, failed_keys


@define(frozen=True)
class TrackResolutionMetrics:
    """Outcome counts from an inward resolution pass.

    ``redirects``/``fallbacks`` are Spotify-specific (relinked/dead track ID
    recovery — see ``SpotifyInwardResolver``); connectors without an
    equivalent concept (e.g. Last.fm) leave them at the default 0.

    ``suppressed`` counts ids the provider was deliberately *not* asked about
    because their no-match backoff has not come due. They are neither failures
    nor successes — nothing was attempted — so they get their own bucket rather
    than inflating ``failed``, which would read as a rising error rate exactly
    as the cache started doing its job.

    ``write_failed`` breaks ``failed`` down rather than adding to it: it counts
    the ids the provider *did* answer for whose persist was rolled back, as
    against the ones it could not account for. The two want opposite responses
    — one is a retry, the other is a dead identifier — and a single count
    cannot tell them apart, which is what made a chunk-persist failure read as
    a library full of dead ids.
    """

    existing: int = 0
    reused: int = 0
    created: int = 0
    failed: int = 0
    redirects: int = 0
    fallbacks: int = 0
    suppressed: int = 0
    degraded_persists: int = 0
    write_failed: int = 0

    @property
    def total(self) -> int:
        return (
            self.existing + self.reused + self.created + self.failed + self.suppressed
        )


@define(frozen=True, slots=True)
class ReuseMetadata:
    """Metadata extracted from a connector identifier for canonical reuse matching."""

    artist: str
    title: str
    connector_id: str

    @property
    def lookup_pair(self) -> tuple[str, str]:
        """The (title, artist) pair the title+artist probe is keyed by."""
        return (self.title.strip().lower(), self.artist.strip().lower())


@define(frozen=True, slots=True)
class _AcceptedReuse:
    """One planned-and-accepted reuse, decided before anything is written.

    ``spec.track`` is the pre-mapping canonical the caller resolves to —
    deliberately not the track ``map_tracks_to_connectors`` hands back, which
    carries the new connector id folded in; resolution semantics predate the
    mapping write and must not change with it.
    """

    identifier: str
    spec: ConnectorMappingSpec


class InwardTrackResolver[THint = object](ABC):
    """Shared 'resolve inward' pattern: external IDs → canonical tracks.

    Three-step pipeline:
    1. **Mapping Lookup**: Bulk-fetch existing connector→track mappings
    2. **Canonical Reuse**: Match unresolved IDs against existing canonical tracks
    3. **Track Creation**: Batch-create new tracks for remaining unresolved IDs

    Subclasses provide:
    - connector_name: str property (e.g. "spotify", "lastfm")
    - _normalize_id(raw_id) → connector_track_identifier for DB lookup
    - _create_tracks_batch(missing_ids, uow) → dict mapping ID → Track
    - _extract_reuse_metadata(identifier) → ReuseMetadata or None

    ``THint`` types the optional per-id hints a caller can pass to
    ``resolve_to_canonical_tracks`` — connector-specific evidence about the
    ids being asked about (Spotify's fallback hints). The default hook
    ignores them; a hint-aware resolver parameterizes the class and
    overrides ``_begin_resolution`` to stash them.
    """

    _match_evaluation_service: MatchEvaluationService
    # The reuse step's rules. Names alone: an identifier carries no duration
    # for the recording gate to rule on, so the gate is the evaluator's
    # accept plus a title-similarity floor.
    _reuse_rules: TrackResolutionRules
    _reuse_failed_ids: set[str]

    def __init__(self, match_evaluation_service: MatchEvaluationService | None = None):
        if match_evaluation_service is None:
            match_evaluation_service = create_evaluation_service()
        self._match_evaluation_service = match_evaluation_service
        self._reuse_rules = TrackResolutionRules(
            match_evaluation_service.config, names_alone=True
        )
        self._reuse_failed_ids = set()

    @property
    @abstractmethod
    def connector_name(self) -> str:
        """Service identifier for connector lookups (e.g. 'spotify', 'lastfm')."""

    @abstractmethod
    def _normalize_id(self, raw_id: str) -> str:
        """Normalize a raw external ID for dedup and DB lookup."""

    @abstractmethod
    async def _create_tracks_batch(
        self,
        missing_ids: list[str],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> dict[str, Track]:
        """Create canonical tracks for IDs not found in existing mappings.

        Args:
            missing_ids: Normalized IDs that had no existing connector mapping.
            uow: Unit of work for database operations.

        Returns:
            Dict mapping normalized ID → newly created Track.
            IDs absent from the result are counted as failures.
        """

    @abstractmethod
    def _extract_reuse_metadata(self, identifier: str) -> ReuseMetadata | None:
        """Extract metadata for canonical reuse matching.

        Args:
            identifier: One normalized connector ID with no existing mapping.

        Returns:
            Artist/title metadata to match against existing canonicals, or
            ``None`` to skip this identifier. A resolver with no reuse
            strategy returns ``None`` for every identifier.
        """

    async def _reuse_existing_canonical_tracks(
        self,
        missing_ids: list[str],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> dict[str, Track]:
        """Canonical Reuse: match unresolved IDs against existing canonical tracks.

        For each unresolved ID, extracts artist+title metadata via the
        _extract_reuse_metadata hook, batch-searches for existing canonicals
        by title+artist, prices the candidates under the names-alone rules
        (``price_reuse``, one description at a time — no leaders: an
        unresolved twin has no canonical to fold onto yet) and creates
        connector mappings for what it reuses, recording the candidate each
        refusal turned down.
        """
        # Extract metadata from identifiers via subclass hook
        pairs: list[tuple[str, str]] = []
        id_to_meta: dict[str, ReuseMetadata] = {}
        for identifier in missing_ids:
            meta = self._extract_reuse_metadata(identifier)
            if not meta:
                continue
            pairs.append(meta.lookup_pair)
            id_to_meta[identifier] = meta

        if not pairs:
            return {}

        candidates = await uow.get_track_repository().find_tracks_by_title_artist(
            pairs, user_id=user_id
        )
        if not candidates:
            return {}

        # Keyed by the description that proposed each candidate: the gate is
        # a similarity floor, so a candidate the probe reached on a looser
        # form is still priced — and its refusal recorded.
        descriptions: dict[str, RecordingDescription] = {}
        name_owners: dict[Hashable, list[Track]] = {}
        for identifier, meta in id_to_meta.items():
            description = RecordingDescription(title=meta.title, artist=meta.artist)
            descriptions[identifier] = description
            candidate = candidates.get(meta.lookup_pair)
            if candidate is not None:
                bucket = name_owners.setdefault(identity_key(description), [])
                if all(owner.id != candidate.id for owner in bucket):
                    bucket.append(candidate)

        # Accepted mappings accumulate into one batch persisted below.
        accepted: list[_AcceptedReuse] = []
        refusal_events: list[ResolutionDecision] = []
        for identifier, meta in id_to_meta.items():
            description = descriptions[identifier]
            key = track_name_key(description)
            priced: Reuse[str, Track] | Refusal[Track] | None = (
                price_reuse(description, name_owners.get(key, ()), self._reuse_rules)
                if key is not None
                else None
            )
            if priced is None:
                continue
            if isinstance(priced, Reuse):
                if priced.canonical is not None:
                    accepted.append(
                        _AcceptedReuse(
                            identifier=identifier,
                            # ``primary=True`` is the single-mapping call's
                            # ``auto_set_primary`` default this batch replaces —
                            # ``map_track_to_connector`` is a one-spec call to
                            # ``map_tracks_to_connectors`` with exactly this flag.
                            spec=ConnectorMappingSpec.priced(
                                priced.canonical,
                                self.connector_name,
                                meta.connector_id,
                                priced.evidence,
                                metadata={
                                    "artist_name": meta.artist,
                                    "track_name": meta.title,
                                },
                                primary=True,
                            ),
                        )
                    )
                continue
            refusal = priced.evidence
            title_similarity = evidence_number(refusal.evidence, "title_similarity")
            logger.debug(
                f"Canonical reuse rejected candidate {priced.candidate.id} for "
                f"{identifier} (confidence: {refusal.confidence}, "
                f"title_sim: {title_similarity})"
            )
            refusal_events.append(
                ResolutionDecision(
                    event_type="rejected",
                    connector_name=self.connector_name,
                    track_id=priced.candidate.id,
                    confidence=refusal.confidence,
                    score=final_score_of(refusal.evidence),
                    zone=refusal.zone,
                    payload={
                        "connector_id": meta.connector_id,
                        # The reuse gate is stricter than the matcher's own
                        # accept threshold, so record which of the two
                        # refused — otherwise a "rejected" event with a
                        # high confidence looks like a contradiction.
                        "title_similarity": title_similarity,
                        "title_threshold": self._reuse_rules.config.high_similarity_threshold,
                    },
                )
            )

        async def _persist_reuse_mappings(
            chunk: Sequence[_AcceptedReuse],
        ) -> dict[str, Track]:
            _ = await uow.get_connector_repository().map_tracks_to_connectors([
                item.spec for item in chunk
            ])
            return {item.identifier: item.spec.track for item in chunk}

        def _log_failed_reuse(item: _AcceptedReuse, e: Exception) -> None:
            # The matcher just said an existing canonical holds this
            # recording, so creating one instead would duplicate it — the
            # failure is recorded and the identifier is kept out of step 3
            # (see ``resolve_to_canonical_tracks``).
            logger.warning(
                f"Failed to create reuse mapping for {item.identifier}: {e}",
                exc_info=e,
            )

        result, failed_ids = await persist_bulk_with_item_fallback(
            accepted,
            uow,
            persist=_persist_reuse_mappings,
            write_key=lambda item: item.identifier,
            describe=f"{self.connector_name} canonical-reuse mappings",
            on_item_failure=_log_failed_reuse,
        )
        self._reuse_failed_ids.update(failed_ids)
        for item in accepted:
            if item.identifier in result:
                logger.info(
                    f"Reused canonical track {item.spec.track.id} for "
                    f"{self.connector_name}:{item.spec.connector_id} "
                    f"(confidence: {item.spec.confidence})"
                )

        # Events only — this gate does not write to the negative cache.
        #
        # It refuses on a stricter similarity bar than the matcher's own accept
        # threshold, deliberately: reusing an existing canonical is harder to
        # undo than proposing a match. Every reader of the cache honours
        # everything in it, so storing a refusal made on that narrower bar
        # suppressed, everywhere, pairs the matcher would have auto-accepted.
        #
        # Nor would the entry earn its keep for this gate itself. A refusal
        # ends in step 3 creating a canonical and mapping the identifier, so
        # the next import resolves it at step 1 and never reaches here again —
        # the row could only ever be written, never read. And the decision is
        # local string similarity over data already in hand, so re-deciding it
        # is cheaper than a cached answer with expiry rules to keep honest.
        #
        # The `rejected` event above is the durable record, carrying the
        # confidence, the title similarity, and the threshold that refused it.
        if refusal_events:
            _ = await uow.get_resolution_recorder().record(
                refusal_events, user_id=user_id
            )

        return result

    async def resolve_to_canonical_tracks(
        self,
        connector_ids: list[str],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        hints: Mapping[str, THint] | None = None,
    ) -> tuple[dict[str, Track], TrackResolutionMetrics]:
        """Resolve external connector IDs to canonical tracks.

        1. Mapping Lookup: Bulk lookup existing mappings.
        2. Canonical Reuse: Match unresolved IDs against existing canonical tracks.
        3. Track Creation: Batch-create missing tracks via subclass hook.

        Args:
            connector_ids: Raw external IDs (will be normalized).
            uow: Unit of work for database operations.
            hints: Optional per-id evidence, handed to ``_begin_resolution``.

        Returns:
            Tuple of (normalized_id → Track mapping, resolution metrics).
        """
        self._begin_resolution(hints or {})
        with (
            count_degraded_persists() as degraded,
            collect_write_failures() as failures,
        ):
            result, metrics = await self._resolve_to_canonical_tracks(
                connector_ids, uow, user_id=user_id
            )
        metrics = evolve(
            metrics,
            degraded_persists=degraded.count,
            write_failed=len(failures.keys),
        )
        return result, self._decorate_metrics(metrics)

    def _begin_resolution(self, hints: Mapping[str, THint]) -> None:
        """Per-pass setup, called before any lookup. Default: ignore hints.

        Owns the base's per-pass reset. A hint-aware resolver overrides this
        to stash ``hints`` and reset its own tracking state, and calls super.
        """
        _ = hints
        self._reuse_failed_ids = set()

    def _decorate_metrics(
        self, metrics: TrackResolutionMetrics
    ) -> TrackResolutionMetrics:
        """Final-metrics hook. Default: pass through unchanged.

        A resolver with counters the base cannot see (Spotify's redirect and
        fallback tracking) overrides this to fold them in.
        """
        return metrics

    async def _resolve_to_canonical_tracks(
        self,
        connector_ids: list[str],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> tuple[dict[str, Track], TrackResolutionMetrics]:
        """The three-step resolution itself; see the public wrapper."""
        if not connector_ids:
            return {}, TrackResolutionMetrics()

        # Normalize + deduplicate
        unique_ids = list({self._normalize_id(cid) for cid in connector_ids})

        # Step 1 — Mapping Lookup: bulk-fetch existing connector→track mappings
        connections = [(self.connector_name, uid) for uid in unique_ids]
        existing_by_connector = (
            await uow.get_connector_repository().find_tracks_by_connectors(
                connections, user_id=user_id
            )
        )

        # Map connector results back to normalized IDs
        result: dict[str, Track] = {}
        for uid in unique_ids:
            track = existing_by_connector.get((self.connector_name, uid))
            if track:
                result[uid] = track

        existing_count = len(result)

        if existing_count:
            logger.info(
                f"Mapping lookup found {existing_count}/{len(unique_ids)} existing {self.connector_name} tracks"
            )

        # Step 2 — Canonical Reuse: match unresolved IDs against existing canonical tracks
        missing_ids = [uid for uid in unique_ids if uid not in result]
        reused_count = 0

        if missing_ids:
            reused_tracks = await self._reuse_existing_canonical_tracks(
                missing_ids, uow, user_id=user_id
            )
            result.update(reused_tracks)
            reused_count = len(reused_tracks)
            if reused_count:
                logger.info(
                    f"Canonical reuse matched {reused_count}/{len(missing_ids)} existing tracks for {self.connector_name}"
                )

        # Step 3 — Track Creation: batch-create remaining missing tracks.
        #
        # An id whose reuse mapping failed to write never reaches creation. The
        # matcher had already accepted an existing canonical for it, so the row
        # the savepoint discarded was a mapping onto a track that exists —
        # creating a second canonical instead would duplicate the recording
        # (the losing side of two concurrent imports racing the unique
        # constraint). It counts as failed, and the next import resolves it at
        # step 1 against whichever writer won.
        #
        # Then drop the ids whose no-match backoff has not come due: the
        # provider has already been asked and answered "nothing", and asking
        # again before the clock expires spends quota to learn the same thing.
        still_missing = [
            uid
            for uid in missing_ids
            if uid not in result and uid not in self._reuse_failed_ids
        ]
        created_count = 0
        suppressed_count = 0

        if still_missing:
            suppressed = await uow.get_resolution_recorder().backoff_suppressed(
                still_missing, user_id=user_id, connector_name=self.connector_name
            )
            if suppressed:
                still_missing = [uid for uid in still_missing if uid not in suppressed]
                suppressed_count = len(suppressed)
                logger.info(
                    f"Skipped {suppressed_count} {self.connector_name} ids inside "
                    f"their no-match backoff window"
                )

        if still_missing:
            logger.info(
                f"Creating {len(still_missing)} new tracks for {self.connector_name}"
            )
            new_tracks = await self._create_tracks_batch(
                still_missing, uow, user_id=user_id
            )
            result.update(new_tracks)
            created_count = len(new_tracks)

        failed_count = (
            len(unique_ids)
            - existing_count
            - reused_count
            - created_count
            - suppressed_count
        )
        metrics = TrackResolutionMetrics(
            existing=existing_count,
            reused=reused_count,
            created=created_count,
            failed=failed_count,
            suppressed=suppressed_count,
        )

        logger.info(
            f"{self.connector_name} resolution: {metrics.existing} existing, {metrics.reused} reused, {metrics.created} created, {metrics.failed} failed, {metrics.suppressed} suppressed"
        )

        return result, metrics


def _leader_of(reuse: Reuse[str, Track]) -> str:
    """A reuse without a canonical names a leader — the planner guarantees it."""
    if reuse.leader is None:
        raise ValueError("Reuse names neither a canonical nor a leader")
    return reuse.leader


async def mint_credit_artists(
    connector: str,
    sources: Sequence[ArtistCreditSource],
    canonicals: Mapping[str, Track],
    uow: UnitOfWorkProtocol,
    *,
    user_id: str,
    config: MatchingConfig,
) -> None:
    """Mint canonical artists from the ids the payloads carry, after the mapping write.

    The walk is the unit of work's ``ArtistMinter`` — the same one the
    application ``ArtistResolutionService`` delegates to. Best effort under
    its own savepoint: the tracks just resolved are the import, and an
    artist-side failure must not cost them, so it is logged and the chunk
    goes on. Transient contention still propagates. A batch that names no
    connector artist never opens the savepoint.
    """
    if not credited_artists(connector, sources).connector_artists:
        return
    try:
        async with uow.savepoint():
            summary = await uow.get_artist_minter().mint(
                connector, sources, canonicals, user_id=user_id, config=config
            )
    except Exception as error:
        if is_transient_contention(error):
            raise
        logger.error(
            f"Artist minting failed for {len(sources)} {connector} payloads",
            connector=connector,
            track_count=len(sources),
            exc_info=error,
        )
        return
    if summary.artists_created or summary.credits_assigned:
        logger.info(
            "artists_minted",
            connector=connector,
            created=summary.artists_created,
            credits_assigned=summary.credits_assigned,
        )


def json_metadata(metadata: Mapping[str, object]) -> Mapping[str, JsonValue]:
    """A mapping's JSON-able metadata as the domain's credit reader takes it.

    ``_mapping_metadata`` is JSON-able by contract (it is what the mapping
    row stores), so the view is a retyping, not a conversion.
    """
    return cast("Mapping[str, JsonValue]", metadata)


class LeaderNotPersistedError(LookupError):
    """A write's leader was rolled back, so there is no row for it to depend on.

    A follower has nothing to map onto; a contested creation has nothing to
    queue its review against — and persisted without it, would keep its ISRC
    withheld with no question ever asked.
    """

    def __init__(self, requested_id: str, leader: str) -> None:
        super().__init__(
            f"{requested_id} depends on the canonical {leader} creates, and "
            f"that write was rolled back — deferred to the next import"
        )


@define(frozen=True, slots=True)
class ProviderAnswer[TPayload]:
    """What the provider said about one requested id, ready to be planned.

    ``current_id`` is the id the provider considers current — it differs
    from ``requested_id`` on a platform-asserted successor. ``description``
    is the payload as the same-recording question sees it; ``isrc`` is its
    strong id. ``names_decide`` is False where names may not settle the
    answer: a relink (the provider's own assertion of identity) and the
    ISRC-or-nothing connectors.
    """

    requested_id: str
    current_id: str
    payload: TPayload
    description: RecordingDescription
    isrc: str | None = None
    names_decide: bool = True


def _priced_by_the_planner(write: PlannedWrite[object]) -> ResolutionEvidence:
    """A write's default price: the one its planner outcome carries."""
    return evidence_of(write.outcome)


@define(frozen=True, slots=True)
class PlannedWrite[TPayload]:
    """One requested id's persist, decided before anything is written.

    A thin carrier over the planner's ``outcome``, keyed by *current* id —
    a ``Reuse`` of a persisted canonical or of an earlier write in this
    chunk (its leader), a ``Create``, contested or not, or a
    ``DeferToReview``. The persist step writes *this* and builds no payload
    of its own — once for the whole chunk, or one savepoint at a time over
    the same code when the chunk has to be isolated. ``match_method`` is
    the connector's label for the main mapping; the planner's price of the
    decision is where the mapping's confidence and evidence come from.
    """

    requested_id: str
    current_id: str
    payload: TPayload
    match_method: MatchMethod
    outcome: Outcome[str, Track]
    # The price of the decision the main mapping asserts. Defaults to the
    # outcome's own; the Spotify rescue overrides it, because the search
    # price belongs on every mapping that rescue writes.
    evidence: ResolutionEvidence = field(
        default=Factory(_priced_by_the_planner, takes_self=True)
    )
    # Does the main mapping this write asserts hold primacy? A creation and
    # an ISRC reuse do; a mapping that only aliases a stale id onto an
    # already-mapped canonical does not.
    primary: bool = True
    # Is the current id already mapped to the canonical this write resolves
    # to? Then the write asserts no mapping of its own for that id — the
    # live one already describes it — and owes only the requested id's
    # non-primary cache alias.
    held: bool = False

    @property
    def confidence(self) -> int:
        return self.evidence.confidence

    @property
    def requested_id_is_stale(self) -> bool:
        return self.current_id != self.requested_id

    @property
    def creates_canonical(self) -> bool:
        return self.outcome.kind != "reuse"

    @property
    def defers_to_review(self) -> bool:
        """A suspect ISRC collision, with a persisted owner or an in-chunk leader."""
        create = creation_of(self.outcome)
        return self.outcome.kind == "defer_to_review" or (
            create is not None and create.contest is not None
        )


def planned_write[TPayload](
    outcome: Outcome[str, Track], answer: ProviderAnswer[TPayload], *, held: bool
) -> PlannedWrite[TPayload]:
    """The write one planner outcome persists as, in the connector's labels.

    A held write carries the plain import label: the only mapping it writes
    is the requested id's cache alias, and ``stale_id_mapping_spec`` derives
    that alias's own ``*_STALE_ID`` method from this one. Any other reuse
    keeps the planner's method; a creation is a direct import.
    """
    if held or outcome.kind != "reuse":
        match_method: MatchMethod = "direct_import"
    else:
        match_method = outcome.evidence.method
    return PlannedWrite(
        requested_id=answer.requested_id,
        current_id=answer.current_id,
        payload=answer.payload,
        match_method=match_method,
        outcome=outcome,
        held=held,
    )


class WritePlanningResolver[TPayload, THint = object](InwardTrackResolver[THint], ABC):
    """Inward resolver whose creations persist through planned writes.

    Owns the two probes and the planner call that decide a chunk of
    provider answers (``_plan_writes``), and the full ordered write path for
    the ``PlannedWrite``s that come out: save_tracks →
    map_tracks_to_connectors → collision reviews → substitution events —
    one savepoint for the chunk, one per id on failure, followers after
    their leaders. Subclasses supply payload extraction and detection
    labels only:

    - ``_canonical_payload(write)`` → the Track a creation saves
    - ``_mapping_metadata(write)`` → the main mapping's metadata
    - ``_successor_assertion(write, track)`` → the substitution this write
      records, or None

    Two policy hooks carry the per-connector mapping shape and default to
    the majority behavior; see each hook's docstring.
    """

    # The creation-step rules: a provider payload carries its duration, so
    # the name gate is the recording gate.
    _rules: TrackResolutionRules

    def __init__(self, match_evaluation_service: MatchEvaluationService | None = None):
        super().__init__(match_evaluation_service)
        self._rules = TrackResolutionRules(self._match_evaluation_service.config)

    @abstractmethod
    def _canonical_payload(
        self, write: PlannedWrite[TPayload], *, user_id: str
    ) -> Track:
        """The canonical this write creates, keyed on the *current* id.

        The contested ISRC of a deferred creation is withheld by the base
        — the owner keeps it, and the queued review decides later whether
        the two are one recording.
        """

    @abstractmethod
    def _mapping_metadata(self, write: PlannedWrite[TPayload]) -> dict[str, object]:
        """JSON-able metadata the main connector mapping stores."""

    @abstractmethod
    def _successor_assertion(
        self, write: PlannedWrite[TPayload], track: Track
    ) -> SuccessorAssertion | None:
        """The successor assertion this write records, or None.

        Detection stays per-connector — the label and the qualifying
        condition are the connector's own (Apple records creations only,
        Tidal any stale requested id, Spotify provider-asserted relinks).
        """

    def _primary_mapping_id(self, write: PlannedWrite[TPayload]) -> str:
        """The connector id the main mapping names.

        Default: the requested id for a reuse (it answered under its own
        id), the current id for a creation. Tidal overrides — its reuses
        answer under the successor, so the current id always wins there.
        """
        return write.current_id if write.creates_canonical else write.requested_id

    def _owes_stale_mapping(self, write: PlannedWrite[TPayload]) -> bool:
        """Does the requested id get a non-primary stale-id cache mapping?

        Default: only a creation whose requested id is stale. Tidal
        overrides — a reuse can substitute there, and the dead requested id
        still owes its cache mapping.
        """
        return write.creates_canonical and write.requested_id_is_stale

    def _on_writes_persisted(self, writes: Sequence[PlannedWrite[TPayload]]) -> None:
        """Post-savepoint bookkeeping hook. Default: nothing.

        Runs only after a savepoint releases — over the whole chunk on the
        bulk path, over the single write on the isolating one. Never before:
        an unreleased savepoint's rows can still be discarded.
        """

    async def _canonicals_holding(
        self,
        answers: Sequence[ProviderAnswer[TPayload]],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> dict[str, Track]:
        """Canonicals already mapped to the ids the provider calls current.

        Only the current ids that differ from their requested id: a
        requested id's own mapping was already asked about by the mapping
        lookup, so only a substitution's current id can be found here. The
        write that follows aliases the requested id onto that canonical.
        """
        current_ids = sorted({
            answer.current_id
            for answer in answers
            if answer.current_id != answer.requested_id
        })
        if not current_ids:
            return {}
        mapped = await uow.get_connector_repository().find_tracks_by_connectors(
            [(self.connector_name, current_id) for current_id in current_ids],
            user_id=user_id,
        )
        return {current_id: track for (_, current_id), track in mapped.items()}

    async def _plan_writes(
        self,
        answers: Sequence[ProviderAnswer[TPayload]],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> list[PlannedWrite[TPayload]]:
        """The three batch probes, the planner, and one write per answer.

        One decision per *current* id: two requested ids the provider
        answers with one current id (two dead ids sharing a successor) are
        one answer, and every write sharing it fans in to the same
        canonical. Who already holds these current ids, then who owns these
        ISRCs, then who already describes these names — the last only for
        answers names may decide and the earlier steps leave undecided. The
        planner ranks the evidence and the outcomes become writes in chunk
        order.
        """
        if not answers:
            return []
        track_repo = uow.get_track_repository()

        by_current: dict[str, list[ProviderAnswer[TPayload]]] = {}
        for answer in answers:
            by_current.setdefault(answer.current_id, []).append(answer)
        described = [
            Described(
                key=current_id,
                description=group[0].description,
                strong_id=group[0].isrc,
                name_key=(
                    track_name_key(group[0].description)
                    if any(answer.names_decide for answer in group)
                    else None
                ),
            )
            for current_id, group in by_current.items()
        ]

        held = await self._canonicals_holding(answers, uow, user_id=user_id)

        isrcs = sorted({item.strong_id for item in described if item.strong_id})
        isrc_owners = (
            await track_repo.find_tracks_by_isrcs(isrcs, user_id=user_id)
            if isrcs
            else {}
        )

        pairs = undecided_name_pairs(
            described, held_owners=held, strong_owners=isrc_owners
        )
        found = (
            await track_repo.find_tracks_by_title_artist(pairs, user_id=user_id)
            if pairs
            else {}
        )

        plan = plan_resolution(
            described,
            strong_owners=isrc_owners,
            name_owners=owners_by_identity(found.values()),
            rules=self._rules,
            held_owners=held,
        )
        writes: list[PlannedWrite[TPayload]] = []
        for answer in answers:
            outcome = plan[answer.current_id]
            write = planned_write(outcome, answer, held=answer.current_id in held)
            match outcome.kind:
                case "reuse" if outcome.canonical is not None:
                    logger.info(
                        f"Identity reuse: {self.connector_name}:{write.requested_id} "
                        f"describes the recording canonical {outcome.canonical.id} "
                        f"already holds ({write.match_method}, "
                        f"confidence: {write.confidence})"
                    )
                case "reuse":
                    logger.info(
                        f"Identity fold: {self.connector_name}:{write.requested_id} "
                        f"describes the same recording as "
                        f"{self.connector_name}:{outcome.leader}, earlier in this chunk"
                    )
                case "defer_to_review":
                    logger.info(
                        f"ISRC suspect: deferring {self.connector_name}:"
                        f"{write.current_id} to review against canonical "
                        f"{outcome.owner.id} (ISRC={answer.isrc})"
                    )
                case "create":
                    pass
            writes.append(write)
        return writes

    async def _persist_planned_writes(
        self,
        writes: Sequence[PlannedWrite[TPayload]],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> tuple[dict[str, Track], set[str]]:
        """Write a chunk's resolutions — one savepoint for all, per id on failure.

        A follower reuses the canonical its leader creates: in the bulk pass
        the leader is in the same chunk, and on the per-item pass it was
        persisted an item earlier (chunk order puts every leader before its
        followers), which ``persisted`` remembers across savepoints. A
        leader whose own write was rolled back leaves its followers with
        nothing to map onto: they fail rather than fall through to creation
        — the chunk has just decided they are that recording, so minting a
        canonical for them now would write the duplicate this pass exists
        to prevent. A contested creation fails with its leader the same
        way: persisted alone it would hold neither the ISRC nor the review
        that decides who keeps it. The next import resolves them at the
        mapping lookup, against whichever writer won.

        Returns the resolved tracks and the ids whose write was rolled back —
        never absent ids, which the caller classifies separately.
        """
        persisted: dict[str, Track] = {}

        async def _persist(chunk: Sequence[PlannedWrite[TPayload]]) -> dict[str, Track]:
            canonicals = await self._persist_planned_bulk(
                chunk, uow, user_id=user_id, persisted=persisted
            )
            persisted.update({
                write.current_id: canonicals[write.requested_id] for write in chunk
            })
            return canonicals

        def _log_failed_write(write: PlannedWrite[TPayload], e: Exception) -> None:
            logger.error(
                f"Failed to create track for "
                f"{self.connector_name}:{write.requested_id}: {e}",
                exc_info=e,
            )

        return await persist_bulk_with_item_fallback(
            writes,
            uow,
            persist=_persist,
            write_key=lambda write: write.requested_id,
            describe=f"resolved {self.connector_name} tracks",
            on_persisted=self._on_writes_persisted,
            on_item_failure=_log_failed_write,
        )

    async def _persist_planned_bulk(
        self,
        writes: Sequence[PlannedWrite[TPayload]],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        persisted: Mapping[str, Track] | None = None,
    ) -> dict[str, Track]:
        """Write every resolution in the chunk through the batch primitives.

        Writes that share an identity are deduped by their identity key (the
        current id) before anything persists: one canonical create, one
        collision review — every requested id fans in to the same canonical,
        while each still owes its own mappings and substitution event. A
        follower maps onto the canonical its leader's current id created,
        in this chunk or (``persisted``) an earlier savepoint of the pass.
        """
        connector_repo = uow.get_connector_repository()
        persisted = persisted or {}

        creates_by_id: dict[str, PlannedWrite[TPayload]] = {}
        for write in writes:
            if write.creates_canonical:
                _ = creates_by_id.setdefault(write.current_id, write)
        # Before anything is written: every leader a write depends on is
        # either created in this chunk or already has a row from an earlier
        # savepoint. Otherwise the write fails here, not after its own rows
        # have landed in a savepoint that then has to be discarded.
        for write in writes:
            leader_id = write.outcome.depends_on
            if (
                leader_id is not None
                and leader_id not in creates_by_id
                and leader_id not in persisted
            ):
                raise LeaderNotPersistedError(write.requested_id, leader_id)

        saved = await uow.get_track_repository().save_tracks([
            self._creation_payload(write, user_id=user_id)
            for write in creates_by_id.values()
        ])
        track_by_current_id = dict(zip(creates_by_id, saved, strict=True))
        leaders = {**persisted, **track_by_current_id}
        canonicals: dict[str, Track] = {}
        for write in writes:
            outcome = write.outcome
            match outcome.kind:
                case "reuse" if outcome.canonical is not None:
                    canonicals[write.requested_id] = outcome.canonical
                case "reuse":
                    canonicals[write.requested_id] = leaders[_leader_of(outcome)]
                case "create" | "defer_to_review":
                    canonicals[write.requested_id] = track_by_current_id[
                        write.current_id
                    ]

        _ = await connector_repo.map_tracks_to_connectors(
            self._mapping_batch(writes, canonicals)
        )

        # Every write, held ones included: a re-encountered track heals the
        # credits an earlier pass left without ids. The payload's own credits
        # line up with its ``artist_ids``; the canonical's need not.
        await mint_credit_artists(
            self.connector_name,
            [
                credit_source(
                    write.requested_id,
                    self._canonical_payload(write, user_id=user_id).artists,
                    json_metadata(self._mapping_metadata(write)),
                )
                for write in writes
            ],
            canonicals,
            uow,
            user_id=user_id,
            config=self._rules.config,
        )

        # Reviews after the mappings: a review names the connector-track row
        # the mapping upsert writes for the current id.
        await self._queue_collision_reviews(
            [write for write in creates_by_id.values() if write.defers_to_review],
            uow,
            user_id=user_id,
            leaders=leaders,
        )

        assertions = [
            assertion
            for write in writes
            if (
                assertion := self._successor_assertion(
                    write, canonicals[write.requested_id]
                )
            )
            is not None
        ]
        await record_substitutions(
            uow.get_resolution_recorder(),
            connector_name=self.connector_name,
            assertions=assertions,
            user_id=user_id,
        )
        return canonicals

    def _creation_payload(
        self, write: PlannedWrite[TPayload], *, user_id: str
    ) -> Track:
        """The row a creation saves, with a deferred creation's ISRC withheld."""
        track = self._canonical_payload(write, user_id=user_id)
        if write.defers_to_review and track.isrc:
            track = evolve(track, isrc=None)
        return track

    async def _queue_collision_reviews(
        self,
        deferred: Sequence[PlannedWrite[TPayload]],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        leaders: Mapping[str, Track],
    ) -> None:
        """Queue one ``isrc_suspect`` review per deferred creation.

        Against the persisted owner, or — for a collision with an earlier
        creation in the chunk — against the canonical that leader's current
        id created (``leaders``; ``_persist_planned_bulk`` has already failed
        any write whose leader has no row). ``create_reviews_batch``
        refreshes a pending review and leaves an accepted or rejected one
        exactly as the person left it, so a re-import never resurrects a
        dismissed question.
        """
        if not deferred:
            return
        connector_tracks = await uow.get_resolution_recorder().connector_track_ids(
            [write.current_id for write in deferred], connector_name=self.connector_name
        )
        reviews: list[MatchReview] = []
        for write in deferred:
            connector_track_id = connector_tracks.get(write.current_id)
            if connector_track_id is None:
                logger.warning(
                    f"No connector track for {self.connector_name}:"
                    f"{write.current_id} — its ISRC collision review was not queued"
                )
                continue
            owner, priced = self._collision_of(write, leaders)
            reviews.append(
                suspect_review(
                    owner,
                    priced,
                    connector=self.connector_name,
                    connector_track_id=connector_track_id,
                    user_id=user_id,
                )
            )
            logger.warning(
                "isrc_collision_deferred",
                track_id=owner.id,
                connector=self.connector_name,
                connector_id=write.current_id,
                confidence=priced.confidence,
            )
        if reviews:
            _ = await uow.get_match_review_repository().create_reviews_batch(reviews)

    @staticmethod
    def _collision_of(
        write: PlannedWrite[TPayload], leaders: Mapping[str, Track]
    ) -> tuple[Track, ResolutionEvidence]:
        """The owner a deferred write's review names, and the collision's price.

        The persisted owner of a deferral, or the canonical a contested
        creation's leader created (``leaders``).
        """
        outcome = write.outcome
        if outcome.kind == "defer_to_review":
            return outcome.owner, outcome.review
        create = creation_of(outcome)
        if create is None or create.contest is None:
            raise ValueError("only a deferred or contested creation queues a review")
        leader = leaders.get(create.contest.leader)
        if leader is None:
            raise LeaderNotPersistedError(write.requested_id, create.contest.leader)
        return leader, create.contest.evidence

    def _mapping_batch(
        self,
        writes: Sequence[PlannedWrite[TPayload]],
        canonicals: Mapping[str, Track],
    ) -> list[ConnectorMappingSpec]:
        """Every mapping the chunk owes, each saying whether it holds primacy.

        One spec per connector id — two requested ids can share a successor,
        and `uq_track_mappings_live_connector` admits one live mapping per
        (user, connector track, connector), so a second spec for an id
        already claimed is a constraint violation that costs the whole chunk
        its bulk write. A held write asserts no mapping for its current id:
        the live one already there is the mapping that describes the track.
        """
        specs: list[ConnectorMappingSpec] = []
        claimed: set[str] = set()

        def claim(spec: ConnectorMappingSpec) -> None:
            if spec.connector_id in claimed:
                return
            claimed.add(spec.connector_id)
            specs.append(spec)

        for write in writes:
            track = canonicals[write.requested_id]
            if write.held:
                # The current id is mapped to this canonical already — that
                # is what held means — so re-asserting it would only
                # supersede a live mapping with weaker provenance. All this
                # write owes is the requested id's cache alias.
                if write.requested_id_is_stale:
                    claim(
                        stale_id_mapping_spec(
                            track=track,
                            connector=self.connector_name,
                            requested_id=write.requested_id,
                            primary_method=write.match_method,
                            confidence=write.confidence,
                            metadata=self._mapping_metadata(write),
                        )
                    )
                continue
            claim(
                ConnectorMappingSpec(
                    track=track,
                    connector=self.connector_name,
                    connector_id=self._primary_mapping_id(write),
                    match_method=write.match_method,
                    confidence=write.confidence,
                    metadata=self._mapping_metadata(write),
                    confidence_evidence=write.evidence.evidence,
                    primary=write.primary,
                )
            )
            if self._owes_stale_mapping(write):
                claim(
                    stale_id_mapping_spec(
                        track=track,
                        connector=self.connector_name,
                        requested_id=write.requested_id,
                        primary_method=write.match_method,
                        confidence=write.confidence,
                    )
                )
        return specs
