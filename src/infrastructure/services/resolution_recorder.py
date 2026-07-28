"""The single write seam for identity-resolution decisions (v0.10.2).

Every mapping mutation in mixd routes through this object: the four FM6a
writers, plus relink, unlink, set-primary, delete, and review-accept. One path
means the integrity rules are enforced once, and no mutation can outrun its
event — the recorder never opens or commits a transaction, so an event is
always written into whatever transaction its mapping write already occupies.
If that transaction rolls back, the event goes with it.

**Why this lives in infrastructure and not application.** The plan called for
an application service, but the call sites span three layers — a use case, an
infrastructure service, a repository — and infrastructure may not import
application. The shape that serves all three is the one
``TrackIdentityServiceImpl`` already uses: a domain protocol
(:class:`~src.domain.repositories.resolution.ResolutionRecorderProtocol`) that
every layer may depend on, an infrastructure implementation, and a UoW
accessor. The seam is still singular; only its address changed.

**What it does not do.** It never decides anything. Resolvers and the
evaluation service keep their decide-don't-write split; what arrives here is
already a decision, and the recorder's whole job is to make it durable, keyed,
and explicable.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import NamedTuple, override
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import create_matching_config, get_logger
from src.domain.entities.resolution_event import ResolutionEvent
from src.domain.entities.shared import JsonDict
from src.domain.entities.track_mapping import SupersessionReason
from src.domain.matching.content_digest import DigestSide, content_digest, track_side
from src.domain.matching.version import matcher_version as compute_matcher_version
from src.domain.repositories.resolution import (
    RejectedPairRow,
    RejectionCandidate,
    ResolutionDecision,
    ResolutionEventRepositoryProtocol,
    ResolutionNegativeRepositoryProtocol,
    ResolutionRecorderProtocol,
)
from src.domain.services.resolution_retry import (
    DEATH_DEBOUNCE_FAILURES,
    DEATH_DEBOUNCE_MIN_SPAN_SECONDS,
)
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBTrackMapping,
)
from src.infrastructure.persistence.database.live_rows import (
    expire_mapping_identity,
    live_only,
)
from src.infrastructure.persistence.repositories.resolution import (
    ResolutionEventRepository,
    ResolutionNegativeRepository,
)

logger = get_logger(__name__)


class _ConnectorTrackFact(NamedTuple):
    """A persisted connector track's id and the description digests key on."""

    id: UUID
    side: DigestSide


class ResolutionRecorder(ResolutionRecorderProtocol):
    """Writes resolution events and negative-cache state on the caller's session."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        events: ResolutionEventRepositoryProtocol | None = None,
        negatives: ResolutionNegativeRepositoryProtocol | None = None,
        run_id: UUID | None = None,
    ) -> None:
        """Bind to a session; the two repositories may be supplied or derived.

        The UoW passes its own repository instances so every accessor resolves
        to the same objects on the same transaction; the repository layer,
        which has a session but no UoW, lets them default.
        """
        self._session = session
        self._events = events or ResolutionEventRepository(session)
        self._negatives = negatives or ResolutionNegativeRepository(session)
        self._run_id = run_id
        # Computed once per recorder: the hash covers a module's contents plus
        # a config object, neither of which changes inside one transaction.
        self._matcher_version: str = compute_matcher_version(create_matching_config())

    @property
    @override
    def matcher_version(self) -> str:
        """The matcher identity every event and negative-cache row is keyed on.

        Read-only: it is a *derived* fact about the code and config that made a
        decision, and a caller able to set it could stamp decisions with a
        version that never ran — which would then silently expire, or fail to
        expire, every cached rejection keyed on it.
        """
        return self._matcher_version

    # ── event writing ────────────────────────────────────────────────

    @override
    def build_events(
        self, decisions: Sequence[ResolutionDecision], *, user_id: str
    ) -> list[ResolutionEvent]:
        """Stamp decisions with the provenance no call site should supply itself.

        ``decided_at`` is *now* because every current writer decides online; a
        future backfill or offline re-resolution would pass its own value here
        and ``recorded_at`` would still say when mixd learned it.
        """
        decided_at = datetime.now(UTC)
        return [
            ResolutionEvent(
                user_id=user_id,
                event_type=decision.event_type,
                matcher_version=self.matcher_version,
                decided_at=decided_at,
                evidence_as_of=decision.evidence_as_of,
                run_id=self._run_id,
                connector_name=decision.connector_name,
                connector_track_id=decision.connector_track_id,
                track_id=decision.track_id,
                resulting_mapping_id=decision.resulting_mapping_id,
                confidence=decision.confidence,
                score=decision.score,
                zone=decision.zone,
                selection_probability=decision.selection_probability,
                payload=dict(decision.payload),
            )
            for decision in decisions
        ]

    @override
    async def write_events(
        self, events: Sequence[ResolutionEvent], *, user_id: str
    ) -> Sequence[ResolutionEvent]:
        """Append pre-built events, stamping the caller's tenancy on every one.

        The stamp is unconditional, not a fill-in-the-blank. ``ResolutionEvent``
        defaults ``user_id`` to ``"default"``, so a "only if missing" guard is
        dead code by construction — and its live consequence is worse than
        dead: a caller that built events under the local-dev default would
        write them into the log as another tenant's history, past an RLS
        policy that reads ``app.user_id`` and would have rejected them.

        Callers who deliberately construct events with a different ``user_id``
        get it overwritten. That is the contract: the seam's ``user_id``
        argument is the authority on whose decision this is.
        """
        owned = [_with_user(event, user_id) for event in events]
        _ = await self._events.append_events(owned)
        return owned

    @override
    async def record(
        self, decisions: Sequence[ResolutionDecision], *, user_id: str
    ) -> int:
        """Build and append in one step, for sites that own no commit."""
        if not decisions:
            return 0
        return await self._events.append_events(
            self.build_events(decisions, user_id=user_id)
        )

    # ── supersession ─────────────────────────────────────────────────

    @override
    async def record_supersessions(
        self,
        edges: Mapping[UUID, UUID],
        *,
        user_id: str,
        connector_name: str,
        reason: SupersessionReason = "rematch",
    ) -> int:
        """One ``superseded`` event per predecessor→successor edge.

        The event is written against the *successor*: "this mapping is what
        replaced that one" is the question a reader asks, and the successor is
        the row still reachable by a live lookup.
        """
        if not edges:
            return 0
        rows = await self._mapping_rows(list(edges.values()), user_id=user_id)
        decisions = [
            ResolutionDecision(
                event_type="superseded",
                connector_name=connector_name,
                connector_track_id=rows[successor].connector_track_id
                if successor in rows
                else None,
                track_id=rows[successor].track_id if successor in rows else None,
                resulting_mapping_id=successor,
                payload={"superseded_mapping_id": str(predecessor), "reason": reason},
            )
            for predecessor, successor in edges.items()
        ]
        return await self.record(decisions, user_id=user_id)

    @override
    async def retire_mapping(
        self,
        mapping_id: UUID,
        *,
        user_id: str,
        reason: SupersessionReason,
        scope: str | None = None,
    ) -> bool:
        """Retire a live mapping with no successor. True when a row moved.

        ``superseded_by_id`` stays NULL on purpose — a retirement without a
        replacement is a first-class outcome (an id died; a user unlinked), and
        the 044 CHECK encodes exactly that: the timestamp and reason move as a
        unit, the successor is optional.
        """
        result = await self._session.execute(
            update(DBTrackMapping)
            .where(
                DBTrackMapping.id == mapping_id,
                DBTrackMapping.user_id == user_id,
                live_only(DBTrackMapping),
            )
            .values(
                superseded_at=datetime.now(UTC),
                supersession_reason=reason,
                supersession_scope=scope,
                superseded_by_id=None,
                is_primary=False,
            )
            .returning(DBTrackMapping.track_id)
            .execution_options(synchronize_session=False)
        )
        track_ids = list(result.scalars().all())
        # A Core UPDATE leaves the identity map holding the pre-retirement row.
        expire_mapping_identity(
            self._session, mapping_ids=[mapping_id], track_ids=track_ids
        )
        return bool(track_ids)

    # ── negative cache ───────────────────────────────────────────────

    @override
    async def active_rejections(
        self,
        candidates: Sequence[RejectionCandidate],
        *,
        user_id: str,
        connector_name: str,
    ) -> frozenset[tuple[str, UUID]]:
        """Which candidate pairs are still suppressed, keyed by external id.

        External ids rather than connector-track ids because callers hold the
        former: at the moment a match is proposed, the connector track row may
        not exist yet. Pairs whose connector track has never been materialized
        cannot be suppressed at all — no row could have been written about them.
        """
        if not candidates:
            return frozenset()
        by_identifier = await self._connector_track_facts(
            connector_name, [candidate.connector.identifier for candidate in candidates]
        )
        if not by_identifier:
            return frozenset()

        digests: dict[tuple[UUID, UUID], str] = {}
        pair_identifiers: dict[tuple[UUID, UUID], str] = {}
        for candidate in candidates:
            fact = by_identifier.get(candidate.connector.identifier)
            if fact is None:
                continue
            key = (fact.id, candidate.candidate_track.id)
            digests[key] = _digest(fact.side, candidate)
            pair_identifiers[key] = candidate.connector.identifier

        active = await self._negatives.active_rejected_pairs(
            user_id=user_id,
            connector_track_ids=list({ct_id for ct_id, _ in digests}),
            matcher_version=self.matcher_version,
            content_digests=digests,
        )
        return frozenset(
            (pair_identifiers[pair], pair[1]) for pair in active if pair in digests
        )

    @override
    async def remember_rejections(
        self,
        candidates: Sequence[RejectionCandidate],
        *,
        user_id: str,
        connector_name: str,
    ) -> int:
        """Persist cannot-link entries, materializing connector tracks as needed.

        Materializing is not incidental: the cache is keyed on the connector
        track row, and a rejected candidate has usually never been persisted
        (only accepted ones were). Without the row there is nothing to hang the
        constraint on, and the same wrong candidate returns next import.
        """
        if not candidates:
            return 0
        by_identifier = await self._materialize_connector_tracks(
            connector_name, [candidate.connector for candidate in candidates]
        )
        pairs = [
            RejectedPairRow(
                connector_track_id=fact.id,
                candidate_track_id=candidate.candidate_track.id,
                connector_name=connector_name,
                content_digest=_digest(fact.side, candidate),
            )
            for candidate in candidates
            if (fact := by_identifier.get(candidate.connector.identifier)) is not None
        ]
        return await self._negatives.record_rejected_pairs(
            pairs, user_id=user_id, matcher_version=self.matcher_version
        )

    @override
    async def remember_no_match(
        self,
        connector_sides: Sequence[DigestSide],
        *,
        user_id: str,
        connector_name: str,
    ) -> int:
        """Start or extend the backoff clock for connector ids that found nothing."""
        if not connector_sides:
            return 0
        by_identifier = await self._materialize_connector_tracks(
            connector_name, connector_sides
        )
        recorded = 0
        for fact in by_identifier.values():
            _ = await self._negatives.upsert_no_match(
                user_id=user_id,
                connector_name=connector_name,
                connector_track_id=fact.id,
                matcher_version=self.matcher_version,
            )
            recorded += 1
        return recorded

    @override
    async def clear_negatives(
        self,
        connector_identifiers: Sequence[str],
        *,
        user_id: str,
        connector_name: str,
    ) -> int:
        """Any success clears the backoff — no hysteresis on recovery."""
        if not connector_identifiers:
            return 0
        by_identifier = await self._lookup_connector_tracks(
            connector_name, connector_identifiers
        )
        if not by_identifier:
            return 0
        return await self._negatives.clear_no_match(
            user_id=user_id,
            connector_name=connector_name,
            connector_track_ids=list(by_identifier.values()),
        )

    @override
    async def backoff_suppressed(
        self,
        connector_identifiers: Sequence[str],
        *,
        user_id: str,
        connector_name: str,
    ) -> frozenset[str]:
        """External ids still inside their no-match backoff window."""
        if not connector_identifiers:
            return frozenset()
        by_identifier = await self._lookup_connector_tracks(
            connector_name, connector_identifiers
        )
        if not by_identifier:
            return frozenset()
        pending = await self._negatives.pending_no_match(
            user_id=user_id,
            connector_name=connector_name,
            connector_track_ids=list(by_identifier.values()),
        )
        return frozenset(
            identifier
            for identifier, ct_id in by_identifier.items()
            if ct_id in pending
        )

    @override
    async def note_suspect(
        self,
        connector_side: DigestSide,
        *,
        user_id: str,
        connector_name: str,
        payload: JsonDict | None = None,
    ) -> bool:
        """Record one failed lookup; report (and act on) proof that the id is dead.

        The suspect streak is never stored as a counter — it is re-derived from
        the events each time, bounded by the most recent success. That is what
        makes "one success resets immediately" free rather than another piece
        of state to keep in step.
        """
        by_identifier = await self._materialize_connector_tracks(
            connector_name, [connector_side]
        )
        fact = by_identifier.get(connector_side.identifier)
        if fact is None:
            return False
        ct_id = fact.id

        _ = await self.record(
            [
                ResolutionDecision(
                    event_type="suspect",
                    connector_name=connector_name,
                    connector_track_id=ct_id,
                    payload=dict(payload or {}),
                )
            ],
            user_id=user_id,
        )

        window = await self._events.recent_suspect_window(
            user_id=user_id,
            connector_name=connector_name,
            connector_track_id=ct_id,
        )
        if (
            window.count < DEATH_DEBOUNCE_FAILURES
            or window.span_seconds < DEATH_DEBOUNCE_MIN_SPAN_SECONDS
        ):
            return False

        return await self._retire_dead_id(
            ct_id,
            user_id=user_id,
            connector_name=connector_name,
            window_count=window.count,
        )

    async def _retire_dead_id(
        self,
        connector_track_id: UUID,
        *,
        user_id: str,
        connector_name: str,
        window_count: int,
    ) -> bool:
        """Retire the live mapping behind a debounced-dead connector id."""
        result = await self._session.execute(
            select(DBTrackMapping.id, DBTrackMapping.track_id).where(
                DBTrackMapping.user_id == user_id,
                DBTrackMapping.connector_track_id == connector_track_id,
                live_only(DBTrackMapping),
            )
        )
        rows = result.tuples().all()
        if not rows:
            # Nothing live to retire — the id is dead but mixd never believed
            # anything about it. The suspect events stand as the record.
            return False
        for mapping_id, track_id in rows:
            _ = await self.retire_mapping(mapping_id, user_id=user_id, reason="id_dead")
            # Retiring the row is only half the repair: the track keeps a
            # denormalized ``spotify_id``/``mbid`` pointing at the dead
            # identifier, and if the retired row was the primary the track now
            # has none. The read-path healer promotes a surviving sibling or
            # clears the column — the same policy the read path would apply,
            # applied here so nothing has to hit the stale value first.
            await self._heal_primary(track_id, connector_name)
            _ = await self.record(
                [
                    ResolutionDecision(
                        event_type="superseded",
                        connector_name=connector_name,
                        connector_track_id=connector_track_id,
                        track_id=track_id,
                        resulting_mapping_id=mapping_id,
                        payload={
                            "reason": "id_dead",
                            "consecutive_failures": window_count,
                        },
                    )
                ],
                user_id=user_id,
            )
        logger.info(
            "identity_id_dead",
            connector=connector_name,
            connector_track_id=str(connector_track_id),
            retired=len(rows),
        )
        return True

    async def _heal_primary(self, track_id: UUID, connector_name: str) -> None:
        """Promote a surviving mapping to primary, or clear the denormalized id.

        Reaching into the connector repository from the seam follows the
        precedent in ``track/mapper.py``: the promotion *policy* (highest
        confidence, then sync the fast-path column only if the promotion
        actually landed) lives in one place, and a second copy here would be
        the disagreement migration 044 had to repair on 366 production rows.
        """
        from src.infrastructure.persistence.repositories.track.connector import (
            TrackConnectorRepository,
        )

        await TrackConnectorRepository(self._session).ensure_primary_for_connector(
            track_id, connector_name
        )

    # ── connector-track plumbing ─────────────────────────────────────

    async def _connector_track_facts(
        self, connector_name: str, identifiers: Sequence[str]
    ) -> dict[str, _ConnectorTrackFact]:
        """Id **and** stored description for these external ids (no writes).

        The description is what the digest is computed from, and it comes from
        the persisted row rather than from whatever the caller happened to
        know. The two producers of rejections describe the same connector
        track differently — the matching pipeline holds a full provider
        payload, the canonical-reuse gate holds an artist and a title from a
        play record and no duration at all — so digesting the caller's view
        would give one pair two digests, and the second writer would overwrite
        the first's working entry with one that can never match.
        """
        # Lazy import: the track repositories import this module (the write
        # seam), so the package-level import would close a cycle — same
        # precedent as ``track/mapper.py::_get_promote_primary_fn``.
        from src.infrastructure.persistence.repositories.track.mapper import (
            extract_db_artist_names,
        )

        wanted = sorted({identifier for identifier in identifiers if identifier})
        if not wanted:
            return {}
        result = await self._session.execute(
            select(
                DBConnectorTrack.connector_track_identifier,
                DBConnectorTrack.id,
                DBConnectorTrack.title,
                DBConnectorTrack.artists,
                DBConnectorTrack.duration_ms,
            ).where(
                DBConnectorTrack.connector_name == connector_name,
                DBConnectorTrack.connector_track_identifier.in_(wanted),
            )
        )
        return {
            identifier: _ConnectorTrackFact(
                id=ct_id,
                side=DigestSide(
                    identifier=identifier,
                    title=title,
                    artists=tuple(extract_db_artist_names(artists)),
                    duration_ms=duration_ms,
                ),
            )
            for identifier, ct_id, title, artists, duration_ms in result.tuples()
        }

    async def _lookup_connector_tracks(
        self, connector_name: str, identifiers: Sequence[str]
    ) -> dict[str, UUID]:
        """Existing connector-track ids for these external ids (no writes)."""
        facts = await self._connector_track_facts(connector_name, identifiers)
        return {identifier: fact.id for identifier, fact in facts.items()}

    async def _materialize_connector_tracks(
        self, connector_name: str, sides: Sequence[DigestSide]
    ) -> dict[str, _ConnectorTrackFact]:
        """Ensure connector-track rows exist for these sides; return their facts.

        ``DO NOTHING`` rather than ``DO UPDATE``: an existing row is the real
        cached connector track, with metadata a rejected-candidate stub has no
        business overwriting. The follow-up SELECT then reads ids *and stored
        descriptions* for inserted and pre-existing rows alike — which is why
        the digest a stub writer computes matches the one a full-payload
        writer computes for the same pair.
        """
        by_identifier = {side.identifier: side for side in sides if side.identifier}
        if not by_identifier:
            return {}
        now = datetime.now(UTC)
        rows: list[dict[str, object]] = [
            {
                "connector_name": connector_name,
                "connector_track_identifier": side.identifier,
                "title": side.title,
                "artists": {"names": list(side.artists)},
                "album": None,
                "duration_ms": side.duration_ms,
                "release_date": None,
                "isrc": None,
                "raw_metadata": {},
                "last_updated": now,
                "created_at": now,
                "updated_at": now,
            }
            for side in by_identifier.values()
        ]
        _ = await self._session.execute(
            pg_insert(DBConnectorTrack)
            .values(rows)
            .on_conflict_do_nothing(
                index_elements=["connector_name", "connector_track_identifier"]
            )
        )
        return await self._connector_track_facts(
            connector_name, list(by_identifier.keys())
        )

    async def _mapping_rows(
        self, mapping_ids: Sequence[UUID], *, user_id: str
    ) -> dict[UUID, DBTrackMapping]:
        """Fetch mappings by id, superseded ones included (a chain read)."""
        if not mapping_ids:
            return {}
        result = await self._session.execute(
            select(DBTrackMapping)
            .where(
                DBTrackMapping.id.in_(list(mapping_ids)),
                DBTrackMapping.user_id == user_id,
            )
            .execution_options(include_superseded=True, populate_existing=True)
        )
        return {row.id: row for row in result.scalars().all()}


def _digest(connector: DigestSide, candidate: RejectionCandidate) -> str:
    """Digest the *persisted* connector description against the candidate track.

    ``connector`` is deliberately not ``candidate.connector``: see
    :meth:`ResolutionRecorder._connector_track_facts`.
    """
    return content_digest(connector, track_side(candidate.candidate_track))


def _with_user(event: ResolutionEvent, user_id: str) -> ResolutionEvent:
    from attrs import evolve

    return evolve(event, user_id=user_id)


__all__ = ["ResolutionRecorder"]
