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

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import NamedTuple, override
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import create_matching_config, get_logger
from src.domain.entities.resolution_event import ResolutionEvent
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
    SupersessionEdge,
)
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBTrackMapping,
)
from src.infrastructure.persistence.database.live_rows import (
    expire_mapping_identity,
    live_only,
)
from src.infrastructure.persistence.repositories._shared.connector_tracks import (
    build_connector_track_row,
    extract_db_artist_names,
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
    ) -> None:
        """Bind to a session; the two repositories may be supplied or derived.

        The UoW passes its own repository instances so every accessor resolves
        to the same objects on the same transaction; the repository layer,
        which has a session but no UoW, lets them default.
        """
        self._session = session
        self._events = events or ResolutionEventRepository(session)
        self._negatives = negatives or ResolutionNegativeRepository(session)
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

    def _build_events(
        self, decisions: Sequence[ResolutionDecision], *, user_id: str
    ) -> list[ResolutionEvent]:
        """Stamp decisions with the provenance no call site should supply itself.

        ``decided_at`` is *now* because every current writer decides online; a
        future backfill or offline re-resolution would pass its own value here
        and ``recorded_at`` would still say when mixd learned it.

        ``user_id`` is stamped unconditionally, not filled in only where a
        decision lacks one — ``ResolutionDecision`` carries no owner of its
        own, so the seam is the only place that supplies it. Private: the only
        caller is :meth:`record`, which used to be reachable from two entry
        points before ``write_events`` (the pre-v0.10.2-consolidation partner
        of this method) was folded away as dead code — its only caller passed
        a literal ``changed=True``.
        """
        decided_at = datetime.now(UTC)
        return [
            # ``run_id`` is left at its default (None): no online writer has
            # one to stamp. See ``ResolutionEvent.run_id`` for what it is for.
            ResolutionEvent(
                user_id=user_id,
                event_type=decision.event_type,
                matcher_version=self.matcher_version,
                decided_at=decided_at,
                evidence_as_of=decision.evidence_as_of,
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
    async def record(
        self, decisions: Sequence[ResolutionDecision], *, user_id: str
    ) -> int:
        """Build and append in one step, for sites that own no commit.

        The ``user_id`` stamp ``_build_events`` applies is unconditional, not
        a fill-in-the-blank. ``ResolutionEvent`` defaults ``user_id`` to
        ``"default"``, so an "only if missing" guard would be dead code by
        construction — and its live consequence would be worse than dead: a
        caller running under the local-dev default could otherwise have its
        events recorded as another tenant's history, past an RLS policy that
        reads ``app.user_id`` and would have rejected them.
        """
        if not decisions:
            return 0
        return await self._events.append_events(
            self._build_events(decisions, user_id=user_id)
        )

    # ── supersession ─────────────────────────────────────────────────

    @override
    async def record_supersessions(
        self,
        edges: Sequence[SupersessionEdge],
        *,
        reason: SupersessionReason = "rematch",
    ) -> int:
        """One ``superseded`` event per edge, grouped by (user_id, connector_name).

        Grouped rather than emitted one at a time because a caller's batch can
        span both — the edges from an ``assert_mappings`` call or a merge's
        conflation set already carry their own per-edge tenancy, and an
        event's owner has to come from the row it describes, not from
        whichever edge happened to be first in the caller's list. Each event
        is written against its edge's *successor*: "this mapping is what
        replaced that one" is the question a reader asks, and the successor is
        the row still reachable by a live lookup.
        """
        if not edges:
            return 0
        grouped: dict[tuple[str, str], list[SupersessionEdge]] = {}
        for edge in edges:
            grouped.setdefault((edge.user_id, edge.connector_name), []).append(edge)

        total = 0
        for (user_id, connector_name), group in grouped.items():
            rows = await self._mapping_rows(
                [edge.successor_id for edge in group], user_id=user_id
            )
            decisions = [
                ResolutionDecision(
                    event_type="superseded",
                    connector_name=connector_name,
                    connector_track_id=rows[edge.successor_id].connector_track_id
                    if edge.successor_id in rows
                    else None,
                    track_id=rows[edge.successor_id].track_id
                    if edge.successor_id in rows
                    else None,
                    resulting_mapping_id=edge.successor_id,
                    payload={
                        "superseded_mapping_id": str(edge.predecessor_id),
                        "reason": reason,
                    },
                )
                for edge in group
            ]
            total += await self.record(decisions, user_id=user_id)
        return total

    @override
    async def retire_mapping(
        self,
        mapping_id: UUID,
        *,
        user_id: str,
        reason: SupersessionReason,
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
                # No caller passes a scope — every retirement today is global.
                # See ``TrackMapping.supersession_scope`` for what would write
                # a non-None value here.
                supersession_scope=None,
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

        # One dict keyed by the pair, holding both things the pair needs
        # downstream (the digest to ask the negative cache with, the external
        # identifier to answer the caller with) — built in a single pass
        # rather than two same-keyed dicts that a later step re-joins.
        by_pair: dict[tuple[UUID, UUID], tuple[str, str]] = {}
        for candidate in candidates:
            fact = by_identifier.get(candidate.connector.identifier)
            if fact is None:
                continue
            key = (fact.id, candidate.candidate_track.id)
            by_pair[key] = (
                candidate.connector.identifier,
                _digest(fact.side, candidate),
            )

        active = await self._negatives.active_rejected_pairs(
            user_id=user_id,
            connector_track_ids=list({ct_id for ct_id, _ in by_pair}),
            matcher_version=self.matcher_version,
            content_digests={pair: digest for pair, (_, digest) in by_pair.items()},
        )
        return frozenset(
            (identifier, pair[1])
            for pair, (identifier, _) in by_pair.items()
            if pair in active
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

        Every reader of this store honours everything in it, so a refusal
        belongs here only if it should stop *all* future matching. A decision
        made on a narrower bar than the matcher's is a ``rejected`` event and
        nothing more — recording it here would suppress, everywhere, pairs the
        matcher itself would have auto-accepted.
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
        return await self._negatives.upsert_no_match(
            user_id=user_id,
            connector_name=connector_name,
            connector_track_ids=[fact.id for fact in by_identifier.values()],
            matcher_version=self.matcher_version,
        )

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
        by_identifier = await self.connector_track_ids(
            connector_identifiers, connector_name=connector_name
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
        by_identifier = await self.connector_track_ids(
            connector_identifiers, connector_name=connector_name
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

    # ── connector-track plumbing ─────────────────────────────────────

    @override
    async def connector_track_ids(
        self, identifiers: Sequence[str], *, connector_name: str
    ) -> dict[str, UUID]:
        """The connector-track rows these external ids name (no writes).

        Every event, streak and backoff query keys on ``connector_track_id``,
        so anything recording an event *about* an external id has to trade the
        string for the row first. Ids mixd has never stored are simply absent
        from the result — a caller that needs a row created should go through
        the negative cache, which materializes deliberately.
        """
        facts = await self._connector_track_facts(connector_name, identifiers)
        return {identifier: fact.id for identifier, fact in facts.items()}

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
        # ``pg_insert`` bypasses the ORM defaults ``bulk_upsert`` relies on, so
        # the two timestamp columns are unioned onto the shared row shape here.
        rows: list[dict[str, object]] = [
            build_connector_track_row(
                connector_name,
                side.identifier,
                title=side.title,
                artist_names=side.artists,
                duration_ms=side.duration_ms,
                last_updated=now,
            )
            | {"created_at": now, "updated_at": now}
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


__all__ = ["ResolutionRecorder"]
