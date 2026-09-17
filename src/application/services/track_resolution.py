"""Resolve connector payloads to canonical tracks, and record the decision.

The one ingest path for likes and playlist imports. It owns the order of the
steps — lock, store the payloads, resolve what is already mapped, plan the
rest with the domain planner, persist creations, assert mappings, queue
reviews — and nothing else: every decision is the planner's
(``domain.matching.canonical_resolution``) and every write is a repository
seam that only persists what it is handed.
"""

from collections.abc import Hashable, Sequence
from uuid import UUID

from attrs import Factory, define, evolve

from src.config import create_matching_config, get_logger
from src.domain.entities import Artist, ConnectorTrack, Track
from src.domain.entities.match_review import MatchReview
from src.domain.matching.canonical_resolution import (
    Create,
    Described,
    Outcome,
    contest_review_for,
    plan_canonical_resolution,
    review_for,
)
from src.domain.matching.config import MatchingConfig
from src.domain.matching.recording_identity import (
    RecordingDescription,
    describe_track,
    identity_key,
)
from src.domain.repositories.connector import ConnectorMappingSpec
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__)


def describe_connector_track(track: ConnectorTrack) -> RecordingDescription:
    """A connector payload as the same-recording question sees it."""
    return RecordingDescription(
        title=track.title,
        artist=track.artists[0].name if track.artists else "",
        duration_ms=track.duration_ms,
    )


@define(frozen=True, slots=True)
class TrackResolutionService:
    """Ingest connector tracks: one canonical per payload, decided once.

    ``evaluator_config`` is the matching configuration the planner prices
    every decision with — injected so a test can pin thresholds without
    reaching into settings.
    """

    evaluator_config: MatchingConfig = Factory(create_matching_config)

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
        # is freshness, not a decision.
        existing = await connector_repo.find_tracks_by_connectors(
            [(connector, identifier) for identifier in payloads], user_id=user_id
        )
        resolved: dict[str, Track] = {
            identifier: track.with_connector_track_id(connector, identifier)
            for (_, identifier), track in existing.items()
        }
        if resolved:
            await connector_repo.touch_last_seen(
                connector,
                [stored[identifier].id for identifier in resolved],
                user_id=user_id,
            )

        pending = [identifier for identifier in payloads if identifier not in resolved]
        if not pending:
            return [resolved[t.connector_track_identifier] for t in tracks]

        plan = await self._plan(pending, payloads, uow, user_id=user_id)

        # Creations first: a leader has to exist before its followers map.
        creations = {
            identifier: create
            for identifier in pending
            if (create := _creation_of(plan[identifier])) is not None
        }
        created: dict[str, Track] = {}
        if creations:
            saved = await track_repo.save_tracks([
                self._canonical(
                    payloads[identifier], create, connector, user_id=user_id
                )
                for identifier, create in creations.items()
            ])
            created = dict(zip(creations, saved, strict=True))

        specs: list[ConnectorMappingSpec] = []
        reviews: list[MatchReview] = []
        # Owners this batch has already backfilled, at their bumped version,
        # so a second payload reusing the same owner updates the fresh row.
        backfilled: dict[UUID, Track] = {}
        for identifier in pending:
            outcome = plan[identifier]
            payload = payloads[identifier]
            metadata: dict[str, object] | None = dict(payload.raw_metadata) or None
            if outcome.kind == "reuse":
                if outcome.canonical is None:
                    canonical = created[_leader_of(outcome.leader)]
                else:
                    canonical = backfilled.get(outcome.canonical.id, outcome.canonical)
                    # An ISRC owner vouches for the recording, so its blank
                    # metadata may take the payload's; a name reuse was
                    # accepted *on* the owner's metadata and never edits it.
                    if outcome.evidence.method == "isrc_match" and (
                        filled := _backfill(canonical, payload)
                    ):
                        canonical = await track_repo.save_track(filled)
                        backfilled[canonical.id] = canonical
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
                    ConnectorMappingSpec(
                        track=canonical,
                        connector=connector,
                        connector_id=identifier,
                        match_method=outcome.evidence.method,
                        confidence=outcome.evidence.confidence,
                        metadata=metadata,
                        confidence_evidence=outcome.evidence.evidence,
                        # An alias: the id the canonical already describes
                        # itself by keeps primacy; a vacancy is filled.
                        primary=False,
                    )
                )
                continue
            create = outcome if outcome.kind == "create" else outcome.create
            if outcome.kind == "defer_to_review":
                reviews.append(
                    review_for(
                        outcome,
                        connector=connector,
                        connector_track_id=stored[identifier].id,
                        user_id=user_id,
                    )
                )
                logger.warning(
                    "isrc_collision_deferred",
                    track_id=outcome.owner.id,
                    connector=connector,
                    connector_id=identifier,
                    isrc=payload.isrc,
                    confidence=outcome.review.confidence,
                )
            elif create.contested_leader is not None and create.contest is not None:
                # The leader was persisted just above, so the suspect
                # in-batch collision has a row to be reviewed against.
                leader = created[create.contested_leader]
                reviews.append(
                    contest_review_for(
                        create,
                        leader,
                        connector=connector,
                        connector_track_id=stored[identifier].id,
                        user_id=user_id,
                    )
                )
                logger.warning(
                    "isrc_collision_deferred",
                    track_id=leader.id,
                    connector=connector,
                    connector_id=identifier,
                    isrc=payload.isrc,
                    confidence=create.contest.confidence,
                )
            specs.append(
                ConnectorMappingSpec(
                    track=created[identifier],
                    connector=connector,
                    connector_id=identifier,
                    match_method=create.evidence.method,
                    confidence=create.evidence.confidence,
                    metadata=metadata,
                    confidence_evidence=create.evidence.evidence,
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

        return [resolved[t.connector_track_identifier] for t in tracks]

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

        # The name probe, only for what the ISRC step leaves undecided: a
        # payload whose ISRC has an owner is settled either way by that owner.
        described = [
            Described(
                key=identifier,
                description=describe_connector_track(payloads[identifier]),
                strong_id=payloads[identifier].isrc or None,
            )
            for identifier in pending
        ]
        pairs = [
            (item.description.title, item.description.artist)
            for item in described
            if item.description.title
            and item.description.artist
            and not (item.strong_id and item.strong_id in isrc_owners)
        ]
        found = (
            await track_repo.find_tracks_by_title_artist(pairs, user_id=user_id)
            if pairs
            else {}
        )
        # Keyed by what the *found canonical* normalizes to, not by the probe
        # pair that surfaced it: the probe also answers on a
        # parenthetical-stripped form, and a candidate reached that way keys
        # differently from the payload — the pairing the recording gate refuses.
        name_owners: dict[Hashable, list[Track]] = {}
        seen: set[UUID] = set()
        for owner in found.values():
            if owner.id in seen:
                continue
            seen.add(owner.id)
            name_owners.setdefault(identity_key(describe_track(owner)), []).append(
                owner
            )

        return plan_canonical_resolution(
            described,
            isrc_owners=isrc_owners,
            name_owners=name_owners,
            config=self.evaluator_config,
        )

    @staticmethod
    def _canonical(
        payload: ConnectorTrack,
        create: Create[str, Track],
        connector: str,
        *,
        user_id: str,
    ) -> Track:
        """The canonical row a creation persists, keyed on the payload's id."""
        return Track(
            title=payload.title,
            artists=[Artist(name=a.name) for a in payload.artists],
            album=payload.album,
            duration_ms=payload.duration_ms,
            release_date=payload.release_date,
            isrc=create.strong_id,
            user_id=user_id,
        ).with_connector_track_id(connector, payload.connector_track_identifier)


def _creation_of(outcome: Outcome[str, Track]) -> Create[str, Track] | None:
    """The creation an outcome persists, if it persists one."""
    if outcome.kind == "create":
        return outcome
    if outcome.kind == "defer_to_review":
        return outcome.create
    return None


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


def _leader_of(leader: str | None) -> str:
    """A reuse without a canonical names a leader — the planner guarantees it."""
    if leader is None:
        raise ValueError("Reuse names neither a canonical nor a leader")
    return leader
