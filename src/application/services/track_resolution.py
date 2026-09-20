"""Resolve connector payloads to canonical tracks, and record the decision.

The one ingest path for likes and playlist imports. It owns the order of the
steps — lock, store the payloads, resolve what is already mapped, plan the
rest with the domain planner, persist creations, assert mappings, queue
reviews — and nothing else: every decision is the planner's
(``domain.matching.canonical_resolution``) and every write is a repository
seam that only persists what it is handed.
"""

from collections.abc import Mapping, Sequence
from uuid import UUID

from attrs import Factory, define, evolve

from src.application.services.artist_resolution import ArtistResolutionService
from src.config import create_matching_config, get_logger
from src.domain.entities import ArtistCredit, ConnectorTrack, Track
from src.domain.entities.match_review import MatchReview
from src.domain.matching.canonical_resolution import (
    Described,
    Outcome,
    ResolutionEvidence,
    creation_of,
    owners_by_identity,
    plan_canonical_resolution,
    suspect_review,
    track_name_key,
    undecided_name_pairs,
)
from src.domain.matching.config import MatchingConfig
from src.domain.matching.recording_identity import (
    RecordingDescription,
    describe_recording,
)
from src.domain.repositories.connector import ConnectorMappingSpec
from src.domain.repositories.errors import is_transient_contention, postgres_sqlstate
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)


def describe_connector_track(track: ConnectorTrack) -> RecordingDescription:
    """A connector payload as the same-recording question sees it."""
    return describe_recording(
        track.title, [a.credited_name for a in track.artists], track.duration_ms
    )


def canonical_from_connector_track(
    ct: ConnectorTrack,
    *,
    user_id: str,
    isrc: str | None = None,
    unknown_artist: str | None = None,
) -> Track:
    """The canonical row a connector payload becomes.

    ``isrc`` is the strong id the row claims — the caller decides, since a
    contested or deliberately withheld ISRC stays on the connector track.
    ``unknown_artist`` stands in when the payload credits nobody; ``None``
    leaves the credit list as the payload gave it. The caller attaches the
    connector id where the row should carry it.
    """
    artists = [ArtistCredit(credited_name=a.credited_name) for a in ct.artists]
    if not artists and unknown_artist is not None:
        artists = [ArtistCredit(credited_name=unknown_artist)]
    return Track(
        title=ct.title,
        artists=artists,
        album=ct.album,
        duration_ms=ct.duration_ms,
        release_date=ct.release_date,
        isrc=isrc,
        user_id=user_id,
    )


@define(frozen=True, slots=True)
class TrackResolutionService:
    """Ingest connector tracks: one canonical per payload, decided once.

    ``evaluator_config`` is the matching configuration the planner prices
    every decision with — injected so a test can pin thresholds without
    reaching into settings.
    """

    evaluator_config: MatchingConfig = Factory(create_matching_config)
    # The artist minter that follows every mapping write: the payloads'
    # own artist ids become canonical artists and fill the credits.
    artists: ArtistResolutionService = Factory(ArtistResolutionService)

    async def ingest(
        self,
        connector: str,
        tracks: Sequence[ConnectorTrack],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> list[Track]:
        """Resolve every payload to a canonical track, creating where needed.

        Returns one ``Track`` per input, in input order, each naming this
        batch's connector id — callers key the result by that id to attach
        playlist positions and ``liked_at`` stamps. Runs inside the caller's
        transaction; the caller commits.
        """
        if not tracks:
            return []

        track_repo = uow.get_track_repository()
        connector_repo = uow.get_connector_repository()

        # Before the first probe: the competing writer is the play-import
        # resolver's ``save_tracks``, which takes the same lock, so the two
        # queue here instead of inside ``uq_tracks_user_isrc``.
        await track_repo.acquire_ingest_lock(user_id)

        # One payload per external id (a playlist repeats tracks; Spotify
        # returns the same track across pagination boundaries), last wins.
        payloads: dict[str, ConnectorTrack] = {
            track.connector_track_identifier: track for track in tracks
        }
        stored = await connector_repo.upsert_connector_tracks(
            connector, list(payloads.values())
        )

        # Already mapped: resolved by the mapping alone, and the re-encounter
        # is freshness, not a decision. Keyed by the rows just stored, so the
        # lookup is the mappings and the tracks, never the rows again.
        existing = await connector_repo.find_tracks_by_connector_track_ids(
            [stored[identifier].id for identifier in payloads], user_id=user_id
        )
        resolved: dict[str, Track] = {
            identifier: track.with_connector_track_id(connector, identifier)
            for identifier in payloads
            if (track := existing.get(stored[identifier].id)) is not None
        }
        if resolved:
            await connector_repo.touch_last_seen(
                connector,
                [stored[identifier].id for identifier in resolved],
                user_id=user_id,
            )

        pending = [identifier for identifier in payloads if identifier not in resolved]
        if not pending:
            # Still worth a minting pass: a re-encountered track heals the
            # credits an earlier import (or the backfill) left without ids.
            await self._mint_artists(
                connector, payloads, resolved, uow, user_id=user_id
            )
            return [resolved[t.connector_track_identifier] for t in tracks]

        plan = await self._plan(pending, payloads, uow, user_id=user_id)

        # Creations first: a leader has to exist before its followers map.
        creations = {
            identifier: create
            for identifier in pending
            if (create := creation_of(plan[identifier])) is not None
        }
        created: dict[str, Track] = {}
        if creations:
            saved = await track_repo.save_tracks([
                canonical_from_connector_track(
                    payloads[identifier], user_id=user_id, isrc=create.strong_id
                ).with_connector_track_id(connector, identifier)
                for identifier, create in creations.items()
            ])
            created = dict(zip(creations, saved, strict=True))

        specs: list[ConnectorMappingSpec] = []
        reviews: list[MatchReview] = []
        # ISRC owners with blank metadata filled from their payloads, written
        # once for the batch and mapped at their bumped version.
        backfilled: dict[UUID, Track] = {}
        if fills := _backfills(pending, plan, payloads):
            backfilled = {
                track.id: track for track in await track_repo.fill_blank_metadata(fills)
            }
        for identifier in pending:
            outcome = plan[identifier]
            payload = payloads[identifier]
            metadata: dict[str, object] | None = dict(payload.raw_metadata) or None
            if outcome.kind == "reuse":
                if outcome.canonical is None:
                    canonical = created[_leader_of(outcome.leader)]
                else:
                    canonical = backfilled.get(outcome.canonical.id, outcome.canonical)
                logger.info(
                    f"Identity reuse: {connector}:{identifier} describes the "
                    f"recording canonical {canonical.id} already holds",
                    connector=connector,
                    connector_id=identifier,
                    track_id=canonical.id,
                    match_method=outcome.evidence.method,
                    confidence=outcome.evidence.confidence,
                )
                specs.append(
                    ConnectorMappingSpec.priced(
                        canonical,
                        connector,
                        identifier,
                        outcome.evidence,
                        metadata=metadata,
                        # An alias: the id the canonical already describes
                        # itself by keeps primacy; a vacancy is filled.
                        primary=False,
                    )
                )
                continue
            create = creation_of(outcome)
            if create is None:
                raise ValueError("an outcome that is not a reuse creates")
            # A suspect collision — with a persisted owner, or with a leader
            # persisted just above — has a row to be reviewed against.
            collision: tuple[Track, ResolutionEvidence] | None = None
            if outcome.kind == "defer_to_review":
                collision = (outcome.owner, outcome.review)
            elif create.contest is not None:
                collision = (created[create.contest.leader], create.contest.evidence)
            if collision is not None:
                owner, priced = collision
                reviews.append(
                    suspect_review(
                        owner,
                        priced,
                        connector=connector,
                        connector_track_id=stored[identifier].id,
                        user_id=user_id,
                    )
                )
                logger.warning(
                    "isrc_collision_deferred",
                    track_id=owner.id,
                    connector=connector,
                    connector_id=identifier,
                    isrc=payload.isrc,
                    confidence=priced.confidence,
                )
            specs.append(
                ConnectorMappingSpec.priced(
                    created[identifier],
                    connector,
                    identifier,
                    create.evidence,
                    metadata=metadata,
                    primary=True,
                )
            )

        mapped = await connector_repo.map_tracks_to_connectors(
            specs,
            connector_track_ids={
                (connector, identifier): stored[identifier].id for identifier in pending
            },
        )
        resolved.update(zip(pending, mapped, strict=True))

        if reviews:
            _ = await uow.get_match_review_repository().create_reviews_batch(reviews)

        # Over every payload, resolved and pending alike: the mapping write
        # above is the one seam all of them pass through.
        await self._mint_artists(connector, payloads, resolved, uow, user_id=user_id)

        return [resolved[t.connector_track_identifier] for t in tracks]

    async def _mint_artists(
        self,
        connector: str,
        payloads: Mapping[str, ConnectorTrack],
        resolved: Mapping[str, Track],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> None:
        """Mint canonical artists from the ids the payloads carry.

        Under its own savepoint and best effort: the tracks just resolved
        are the import, and an artist-side failure must not cost them, so
        it is logged and the batch goes on. Transient contention still
        propagates — the caller's retry policy owns that.
        """
        try:
            async with uow.savepoint():
                summary = await self.artists.ingest(
                    connector,
                    list(payloads.values()),
                    uow,
                    user_id=user_id,
                    canonicals=resolved,
                )
        except Exception as error:
            if is_transient_contention(error):
                raise
            logger.error(
                f"Artist minting failed for {len(payloads)} {connector} payloads",
                connector=connector,
                track_count=len(payloads),
                exc_info=error,
            )
            return
        if not summary.empty:
            logger.info(
                "artists_minted",
                connector=connector,
                connector_artists=summary.connector_artists_upserted,
                created=summary.artists_created,
                reused=summary.artists_reused,
                credits_assigned=summary.credits_assigned,
            )

    async def ingest_isolating(
        self,
        connector: str,
        tracks: Sequence[ConnectorTrack],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        describe: str = "tracks",
    ) -> tuple[list[Track], int]:
        """``ingest`` the batch; isolate a bad row rather than lose the batch.

        The bulk attempt runs under a savepoint; when it throws for one row's
        sake — an identity key the planner's probes did not see, a bad
        value — every payload is retried alone under its own savepoint, so
        the batch keeps every track it can and the failure count says what
        it could not. The savepoints are what make "continue" meaningful:
        without one a failed statement leaves the transaction aborted and
        every later retry fails for a reason that has nothing to do with the
        row it names.

        Transient contention (lock timeout, deadlock, serialization loss) is
        not about the rows and is re-raised from either pass: the two
        writers racing on the ``tracks`` identity keys are serialized by the
        per-user ingest lock, so contention surviving to here means a holder
        outlived ``lock_timeout`` — worth a visible failure, where splitting
        the batch would queue each retry on the same held key in turn and
        still resolve nothing. Returns the ingested tracks and how many
        payloads failed.
        """
        try:
            async with uow.savepoint():
                return (
                    await self.ingest(connector, tracks, uow, user_id=user_id),
                    0,
                )
        except Exception as bulk_error:
            if is_transient_contention(bulk_error):
                logger.error(
                    f"Bulk ingest of {len(tracks)} {connector} {describe} failed "
                    f"under transient database contention (SQLSTATE "
                    f"{postgres_sqlstate(bulk_error)}) despite the per-user "
                    f"ingest lock — failing the run",
                    connector=connector,
                    track_count=len(tracks),
                    sqlstate=postgres_sqlstate(bulk_error),
                    exc_info=True,
                )
                raise
            logger.error(
                f"Bulk ingest of {len(tracks)} {connector} {describe} failed — "
                f"retrying them one at a time",
                connector=connector,
                track_count=len(tracks),
                exc_info=bulk_error,
            )

        ingested: list[Track] = []
        failed = 0
        for ct in tracks:
            try:
                async with uow.savepoint():
                    ingested.extend(
                        await self.ingest(connector, [ct], uow, user_id=user_id)
                    )
            except Exception as individual_error:
                if is_transient_contention(individual_error):
                    raise
                failed += 1
                logger.warning(
                    f"Failed to ingest {connector}:{ct.connector_track_identifier}",
                    error=str(individual_error),
                    connector=connector,
                    connector_id=ct.connector_track_identifier,
                )
        if failed:
            logger.error(
                f"{failed} of {len(tracks)} {connector} {describe} could not be "
                f"ingested individually either",
                connector=connector,
                failed=failed,
            )
        return ingested, failed

    async def _plan(
        self,
        pending: Sequence[str],
        payloads: dict[str, ConnectorTrack],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> dict[str, Outcome[str, Track]]:
        """The two batch probes, then the planner."""
        track_repo = uow.get_track_repository()

        isrcs = sorted({
            isrc for identifier in pending if (isrc := payloads[identifier].isrc)
        })
        isrc_owners = (
            await track_repo.find_tracks_by_isrcs(isrcs, user_id=user_id)
            if isrcs
            else {}
        )

        described: list[Described[str, RecordingDescription]] = []
        for identifier in pending:
            description = describe_connector_track(payloads[identifier])
            described.append(
                Described(
                    key=identifier,
                    description=description,
                    strong_id=payloads[identifier].isrc or None,
                    name_key=track_name_key(description),
                )
            )
        pairs = undecided_name_pairs(described, strong_owners=isrc_owners)
        found = (
            await track_repo.find_tracks_by_title_artist(pairs, user_id=user_id)
            if pairs
            else {}
        )

        return plan_canonical_resolution(
            described,
            isrc_owners=isrc_owners,
            name_owners=owners_by_identity(found.values()),
            config=self.evaluator_config,
        )


def _backfill(owner: Track, payload: ConnectorTrack) -> Track | None:
    """The owner with its blank metadata columns taken from the payload.

    Only ``duration_ms``, ``album`` and ``release_date`` — descriptive
    columns, never an identity key — and only where the owner has none: a
    value it already holds is never overwritten. An owner left without a
    duration can never again be reused on names alone, so a sparse row the
    ISRC vouched for would otherwise accrue twins. ``None`` when the payload
    supplies nothing the owner lacks.
    """
    changes: dict[str, object] = {}
    if owner.duration_ms is None and payload.duration_ms is not None:
        changes["duration_ms"] = payload.duration_ms
    if owner.album is None and payload.album is not None:
        changes["album"] = payload.album
    if owner.release_date is None and payload.release_date is not None:
        changes["release_date"] = payload.release_date
    return evolve(owner, **changes) if changes else None


def _backfills(
    pending: Sequence[str],
    plan: Mapping[str, Outcome[str, Track]],
    payloads: Mapping[str, ConnectorTrack],
) -> list[Track]:
    """Every ISRC owner this batch reuses, with its blank metadata filled.

    An ISRC owner vouches for the recording, so its blank metadata may take
    the payload's; a name reuse was accepted *on* the owner's metadata and
    never edits it. One entry per owner: a second payload on the same owner
    fills from the first's result, so the batch writes each row once.
    """
    fills: dict[UUID, Track] = {}
    for identifier in pending:
        outcome = plan[identifier]
        if outcome.kind != "reuse" or outcome.canonical is None:
            continue
        if outcome.evidence.method != "isrc_match":
            continue
        owner = fills.get(outcome.canonical.id, outcome.canonical)
        if filled := _backfill(owner, payloads[identifier]):
            fills[owner.id] = filled
    return list(fills.values())


def _leader_of(leader: str | None) -> str:
    """A reuse without a canonical names a leader — the planner guarantees it."""
    if leader is None:
        raise ValueError("Reuse names neither a canonical nor a leader")
    return leader
