"""Persistence for the resolution event log and the negative cache (v0.10.2).

Two repositories with opposite storage contracts, which is why they are two:

- :class:`ResolutionEventRepository` only ever appends. Nothing here updates or
  deletes a row, and ``recorded_at`` is left entirely to the column default so
  the log's ordering is the database's clock rather than any writer's.
- :class:`ResolutionNegativeRepository` is read-modify-write state: counters
  advance, clocks are cleared on success, rejections are stamped un-rejected.

Both go through the ``BaseRepository`` bulk helpers rather than hand-rolled
inserts — the v0.10.0 review found a hand-rolled upsert that had silently
forked exactly this machinery and lost its intra-batch deduplication.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import cast, override
from uuid import UUID

from attrs import define
from sqlalchemy import ColumnElement, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities.resolution_event import ResolutionEvent, ResolutionEventType
from src.domain.entities.resolution_negative import NegativeKind, ResolutionNegative
from src.domain.entities.shared import JsonDict
from src.domain.repositories.resolution import (
    NegativeCacheSize,
    RejectedPairRow,
    SuspectWindow,
)
from src.domain.services.resolution_retry import NO_MATCH_BACKOFF, next_no_match_check
from src.infrastructure.persistence.database.db_models import (
    DBResolutionEvent,
    DBResolutionNegative,
)
from src.infrastructure.persistence.repositories.base_repo import (
    BaseRepository,
    rows_affected,
)
from src.infrastructure.persistence.repositories.mappers import BaseModelMapper
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)

# Event types that end a suspect streak. A success is a success regardless of
# which path produced it, so the streak resets without a counter to clear —
# the window query simply stops looking further back (memo §10.5: recovery has
# no hysteresis).
_STREAK_RESETTING_EVENTS: tuple[ResolutionEventType, ...] = (
    "accepted",
    "verified",
    "substituted",
    "unrejected",
)

_NO_MATCH: NegativeKind = "no_match"
_REJECTED_PAIR: NegativeKind = "rejected_pair"

# The doubling happens in SQL over the row's own previous interval — the
# ListenBrainz ``check_again`` shape — so the counter and the clock advance in
# the same atomic statement and two concurrent importers cannot lose a miss
# between them. ``coalesce`` covers the one shape the columns admit but the
# writers never produce (a row with NULL clocks): it falls back to the base
# interval rather than writing NULL, which would read as "no backoff at all".
_DOUBLED_CHECK_AGAIN = text(
    "now() + least("
    "coalesce("
    "(resolution_negatives.check_again - resolution_negatives.last_checked_at) * 2, "
    f"interval '{NO_MATCH_BACKOFF.base_seconds} seconds'), "
    f"interval '{NO_MATCH_BACKOFF.cap_seconds} seconds')"
)


@define(frozen=True, slots=True)
class ResolutionEventMapper(BaseModelMapper[DBResolutionEvent, ResolutionEvent]):
    """Row ↔ entity for the append-only event log."""

    @override
    @staticmethod
    async def to_domain(db_model: DBResolutionEvent) -> ResolutionEvent:
        return ResolutionEvent(
            id=db_model.id,
            user_id=db_model.user_id,
            event_type=cast("ResolutionEventType", db_model.event_type),
            matcher_version=db_model.matcher_version,
            recorded_at=db_model.recorded_at,
            decided_at=db_model.decided_at,
            evidence_as_of=db_model.evidence_as_of,
            run_id=db_model.run_id,
            connector_name=db_model.connector_name,
            connector_track_id=db_model.connector_track_id,
            track_id=db_model.track_id,
            resulting_mapping_id=db_model.resulting_mapping_id,
            confidence=db_model.confidence,
            score=db_model.score,
            zone=db_model.zone,
            selection_probability=db_model.selection_probability,
            payload=dict(db_model.payload or {}),
        )

    @override
    @staticmethod
    def to_db(domain_model: ResolutionEvent) -> DBResolutionEvent:
        """Build a row from an entity.

        ``recorded_at`` is deliberately absent: leaving the attribute unset is
        what lets the column's ``now()`` default win. Setting it from Python —
        even to "now" — would hand the log's ordering to whichever process
        happened to write, across machines whose clocks disagree.
        """
        return DBResolutionEvent(
            id=domain_model.id,
            user_id=domain_model.user_id,
            event_type=domain_model.event_type,
            matcher_version=domain_model.matcher_version,
            decided_at=domain_model.decided_at,
            evidence_as_of=domain_model.evidence_as_of,
            run_id=domain_model.run_id,
            connector_name=domain_model.connector_name,
            connector_track_id=domain_model.connector_track_id,
            track_id=domain_model.track_id,
            resulting_mapping_id=domain_model.resulting_mapping_id,
            confidence=domain_model.confidence,
            score=domain_model.score,
            zone=domain_model.zone,
            selection_probability=domain_model.selection_probability,
            payload=cast("JsonDict", dict(domain_model.payload)),
        )

    @override
    @staticmethod
    def get_default_relationships() -> list[str]:
        """No relationships: the log carries no foreign keys by design."""
        return []


@define(frozen=True, slots=True)
class ResolutionNegativeMapper(
    BaseModelMapper[DBResolutionNegative, ResolutionNegative]
):
    """Row ↔ entity for the negative cache."""

    @override
    @staticmethod
    async def to_domain(db_model: DBResolutionNegative) -> ResolutionNegative:
        return ResolutionNegative(
            id=db_model.id,
            user_id=db_model.user_id,
            kind=cast("NegativeKind", db_model.kind),
            connector_name=db_model.connector_name,
            connector_track_id=db_model.connector_track_id,
            candidate_track_id=db_model.candidate_track_id,
            matcher_version=db_model.matcher_version,
            content_digest=db_model.content_digest,
            consecutive_misses=db_model.consecutive_misses,
            check_again=db_model.check_again,
            last_checked_at=db_model.last_checked_at,
            unrejected_at=db_model.unrejected_at,
        )

    @override
    @staticmethod
    def to_db(domain_model: ResolutionNegative) -> DBResolutionNegative:
        return DBResolutionNegative(
            id=domain_model.id,
            user_id=domain_model.user_id,
            kind=domain_model.kind,
            connector_name=domain_model.connector_name,
            connector_track_id=domain_model.connector_track_id,
            candidate_track_id=domain_model.candidate_track_id,
            matcher_version=domain_model.matcher_version,
            content_digest=domain_model.content_digest,
            consecutive_misses=domain_model.consecutive_misses,
            check_again=domain_model.check_again,
            last_checked_at=domain_model.last_checked_at,
            unrejected_at=domain_model.unrejected_at,
        )

    @override
    @staticmethod
    def get_default_relationships() -> list[str]:
        return []


class ResolutionEventRepository(BaseRepository[DBResolutionEvent, ResolutionEvent]):
    """Append-only storage for identity-resolution events."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(
            session=session,
            model_class=DBResolutionEvent,
            mapper=ResolutionEventMapper(),
        )

    @db_operation("append_resolution_events")
    async def append_events(self, events: Sequence[ResolutionEvent]) -> int:
        """Insert events, skipping id collisions; return rows actually written.

        The conflict key is the primary key alone. There is no natural
        uniqueness on an event log — two identical decisions a second apart are
        two facts, not a duplicate — so the only thing worth ignoring is a
        genuine id replay (a retried batch), which uuid7 makes rare and this
        makes harmless.
        """
        if not events:
            return 0
        return await self.bulk_insert_ignore_conflicts(
            [self._to_row(event) for event in events], ["id"]
        )

    @staticmethod
    def _to_row(event: ResolutionEvent) -> dict[str, object]:
        """Column dict for one event; ``recorded_at`` is left to the DB default."""
        return {
            "id": event.id,
            "user_id": event.user_id,
            "event_type": event.event_type,
            "matcher_version": event.matcher_version,
            "decided_at": event.decided_at,
            "evidence_as_of": event.evidence_as_of,
            "run_id": event.run_id,
            "connector_name": event.connector_name,
            "connector_track_id": event.connector_track_id,
            "track_id": event.track_id,
            "resulting_mapping_id": event.resulting_mapping_id,
            "confidence": event.confidence,
            "score": event.score,
            "zone": event.zone,
            "selection_probability": event.selection_probability,
            "payload": dict(event.payload),
        }

    @db_operation("recent_suspect_window")
    async def recent_suspect_window(
        self, *, user_id: str, connector_name: str, connector_track_id: UUID
    ) -> SuspectWindow:
        """Length and span of the current ``suspect`` streak.

        Two queries rather than a window function, because the second one's
        predicate depends on the first's answer: find when this connector track
        last succeeded, then measure only the suspects after it. That is what
        makes "one success resets" free — no counter is stored, so none can
        drift out of step with the events.
        """
        scope = (
            DBResolutionEvent.user_id == user_id,
            DBResolutionEvent.connector_name == connector_name,
            DBResolutionEvent.connector_track_id == connector_track_id,
        )
        last_success = await self.session.scalar(
            select(func.max(DBResolutionEvent.recorded_at)).where(
                *scope, DBResolutionEvent.event_type.in_(_STREAK_RESETTING_EVENTS)
            )
        )

        conditions: list[ColumnElement[bool]] = [
            *scope,
            DBResolutionEvent.event_type == "suspect",
        ]
        if last_success is not None:
            conditions.append(DBResolutionEvent.recorded_at > last_success)

        # Explicit column types: the aggregate expressions are ``Any`` in the
        # SQLAlchemy stubs, and the annotation caps that to this one line
        # instead of letting it spread into SuspectWindow's constructor.
        counted: ColumnElement[int] = func.count()
        # ``min``/``max`` over an empty set are SQL NULL, so the declared type
        # has to admit None even though the column itself never does.
        earliest: ColumnElement[datetime | None] = func.min(
            DBResolutionEvent.recorded_at
        )
        latest: ColumnElement[datetime | None] = func.max(DBResolutionEvent.recorded_at)
        result = await self.session.execute(
            select(counted, earliest, latest).where(*conditions)
        )
        count, first, last = result.tuples().one()
        if not count or first is None or last is None:
            return SuspectWindow()
        return SuspectWindow(count=count, span_seconds=(last - first).total_seconds())

    @db_operation("events_for_mapping")
    async def events_for_mapping(
        self, mapping_id: UUID, *, user_id: str, limit: int = 100
    ) -> list[ResolutionEvent]:
        """Every event naming this mapping, newest first."""
        result = await self.session.execute(
            select(DBResolutionEvent)
            .where(
                DBResolutionEvent.user_id == user_id,
                DBResolutionEvent.resulting_mapping_id == mapping_id,
            )
            .order_by(DBResolutionEvent.recorded_at.desc())
            .limit(limit)
        )
        return [
            await ResolutionEventMapper.to_domain(row) for row in result.scalars().all()
        ]


class ResolutionNegativeRepository(
    BaseRepository[DBResolutionNegative, ResolutionNegative]
):
    """Storage for no-match backoff clocks and sticky cannot-link pairs."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(
            session=session,
            model_class=DBResolutionNegative,
            mapper=ResolutionNegativeMapper(),
        )

    @db_operation("upsert_no_match")
    async def upsert_no_match(
        self,
        *,
        user_id: str,
        connector_name: str,
        connector_track_id: UUID,
        matcher_version: str,
    ) -> ResolutionNegative:
        """Start or extend one connector track's no-match backoff.

        One atomic upsert, not a read-modify-write: two importers that miss the
        same id concurrently would otherwise both read ``consecutive_misses =
        n`` and both write ``n + 1``, losing a miss and holding the retry clock
        in. The counter increments and ``check_again`` doubles in the same
        statement, on the 1d→32d curve.

        The jitter key is the connector track id, so a batch of ids that all
        missed together do not all come due in the same second — the same
        reason the play poller jitters. It applies to the *first* interval; the
        doublings inherit it, because they are computed from the row's own
        previous span.
        """
        now = datetime.now(UTC)
        # `next_no_match_check(0)` is the base interval: the first miss must
        # schedule 1 day, not the doubled one.
        first_seconds = next_no_match_check(0, key=str(connector_track_id))
        model = DBResolutionNegative
        insert_stmt = pg_insert(model).values(
            user_id=user_id,
            kind=_NO_MATCH,
            connector_name=connector_name,
            connector_track_id=connector_track_id,
            candidate_track_id=None,
            matcher_version=matcher_version,
            consecutive_misses=1,
            # Both clocks read the *database's* now so the span the doubling
            # measures next time is exactly the interval written here.
            check_again=text(f"now() + interval '{first_seconds} seconds'"),
            last_checked_at=func.now(),
            created_at=now,
            updated_at=now,
        )
        upsert = (
            insert_stmt
            .on_conflict_do_update(
                index_elements=[
                    model.user_id,
                    model.connector_track_id,
                    model.connector_name,
                ],
                # `uq_resolution_negatives_no_match` is partial; the predicate
                # is what lets PostgreSQL infer it as the arbiter.
                index_where=model.kind == _NO_MATCH,
                set_={
                    "matcher_version": insert_stmt.excluded.matcher_version,
                    "consecutive_misses": model.consecutive_misses + 1,
                    "last_checked_at": func.now(),
                    "check_again": _DOUBLED_CHECK_AGAIN,
                    "updated_at": insert_stmt.excluded.updated_at,
                },
            )
            .returning(model)
            .execution_options(populate_existing=True)
        )
        result = await self.session.execute(upsert)
        return await ResolutionNegativeMapper.to_domain(result.scalars().one())

    @db_operation("clear_no_match")
    async def clear_no_match(
        self,
        *,
        user_id: str,
        connector_name: str,
        connector_track_ids: Sequence[UUID],
    ) -> int:
        """Delete the backoff rows for ids that just resolved.

        Delete rather than "set ``check_again`` to NULL": a no-match row *is*
        the absence of a match, so once one exists the row has nothing left to
        say. Keeping a zeroed row would only invite a later reader to treat a
        resolved track as still-pending.
        """
        if not connector_track_ids:
            return 0
        result = await self.session.execute(
            delete(DBResolutionNegative).where(
                DBResolutionNegative.user_id == user_id,
                DBResolutionNegative.kind == _NO_MATCH,
                DBResolutionNegative.connector_name == connector_name,
                DBResolutionNegative.connector_track_id.in_(connector_track_ids),
            )
        )
        return rows_affected(result)

    @db_operation("pending_no_match")
    async def pending_no_match(
        self,
        *,
        user_id: str,
        connector_name: str,
        connector_track_ids: Sequence[UUID],
    ) -> set[UUID]:
        """Which of these ids are still inside their no-match backoff window.

        The write side of ``check_again`` existed from the start; this is its
        reader. Without one the clock was a column nothing consulted, and every
        import re-asked the provider about ids it had already been told twice
        do not exist — the exact spend the backoff was written to stop.
        """
        if not connector_track_ids:
            return set()
        result = await self.session.execute(
            select(DBResolutionNegative.connector_track_id).where(
                DBResolutionNegative.user_id == user_id,
                DBResolutionNegative.kind == _NO_MATCH,
                DBResolutionNegative.connector_name == connector_name,
                DBResolutionNegative.connector_track_id.in_(connector_track_ids),
                DBResolutionNegative.check_again > func.now(),
            )
        )
        return set(result.scalars().all())

    @db_operation("record_rejected_pairs")
    async def record_rejected_pairs(
        self, pairs: Sequence[RejectedPairRow], *, user_id: str, matcher_version: str
    ) -> int:
        """Persist cannot-link facts, refreshing any that already exist.

        Re-asserting a pair updates its digest and matcher version rather than
        inserting a second row: the constraint is about the pair, and letting
        stale copies accumulate is exactly the pathology (silent
        undermatching) the digest exists to bound.
        """
        if not pairs:
            return 0
        now = datetime.now(UTC)
        rows: list[dict[str, object]] = [
            {
                "user_id": user_id,
                "kind": _REJECTED_PAIR,
                "connector_name": pair.connector_name,
                "connector_track_id": pair.connector_track_id,
                "candidate_track_id": pair.candidate_track_id,
                "matcher_version": matcher_version,
                "content_digest": pair.content_digest,
                "consecutive_misses": 0,
                "last_checked_at": now,
                # A re-assertion is a fresh rejection, so any previous
                # un-reject is void — the user's override was reconsidered by
                # the same decision path that produced it.
                "unrejected_at": None,
            }
            for pair in pairs
        ]
        return await self.bulk_upsert(
            rows,
            lookup_keys=["user_id", "connector_track_id", "candidate_track_id"],
            return_models=False,
            # ``uq_resolution_negatives_pair`` is partial. Without the
            # predicate PostgreSQL cannot infer the arbiter, every conflict
            # raises 23505, and the batch degrades to the per-row fallback —
            # which is the same upsert one row at a time, so the fault is
            # invisible except as latency.
            index_where=DBResolutionNegative.kind == _REJECTED_PAIR,
        )

    @db_operation("active_rejected_pairs")
    async def active_rejected_pairs(
        self,
        *,
        user_id: str,
        connector_track_ids: Sequence[UUID],
        matcher_version: str,
        content_digests: Mapping[tuple[UUID, UUID], str] | None = None,
    ) -> set[tuple[UUID, UUID]]:
        """Pairs still suppressed for this matcher version.

        Three expiry conditions, all applied here so no caller can honour two
        of the three: a matcher-version bump invalidates every stored score, an
        explicit un-reject withdraws the constraint, and a digest that no
        longer matches means the metadata the rejection was computed from has
        changed underneath it.
        """
        if not connector_track_ids:
            return set()
        result = await self.session.execute(
            select(
                DBResolutionNegative.connector_track_id,
                DBResolutionNegative.candidate_track_id,
                DBResolutionNegative.content_digest,
            ).where(
                DBResolutionNegative.user_id == user_id,
                DBResolutionNegative.kind == _REJECTED_PAIR,
                DBResolutionNegative.connector_track_id.in_(connector_track_ids),
                DBResolutionNegative.matcher_version == matcher_version,
                DBResolutionNegative.unrejected_at.is_(None),
                DBResolutionNegative.candidate_track_id.is_not(None),
            )
        )
        active: set[tuple[UUID, UUID]] = set()
        for connector_track_id, candidate_track_id, stored_digest in result.tuples():
            if candidate_track_id is None:
                continue
            pair = (connector_track_id, candidate_track_id)
            if content_digests is not None:
                current = content_digests.get(pair)
                if current is not None and current != stored_digest:
                    continue
            active.add(pair)
        return active

    @db_operation("unreject")
    async def unreject(
        self, *, user_id: str, connector_track_id: UUID, candidate_track_id: UUID
    ) -> bool:
        """Withdraw one cannot-link constraint; True when one was live."""
        result = await self.session.execute(
            update(DBResolutionNegative)
            .where(
                DBResolutionNegative.user_id == user_id,
                DBResolutionNegative.kind == _REJECTED_PAIR,
                DBResolutionNegative.connector_track_id == connector_track_id,
                DBResolutionNegative.candidate_track_id == candidate_track_id,
                DBResolutionNegative.unrejected_at.is_(None),
            )
            .values(unrejected_at=datetime.now(UTC))
            .execution_options(synchronize_session=False)
        )
        return rows_affected(result) > 0

    @db_operation("count_active_negatives")
    async def count_active_negatives(self, *, user_id: str) -> NegativeCacheSize:
        """Size of each half of the cache for one tenant.

        "Active" means *currently suppressing something*: a rejection that has
        not been withdrawn, a backoff clock that has not yet come due. A row
        whose ``check_again`` has passed is not suppressing anything — the next
        pass will re-ask — so counting it would overstate the cache and hide
        the growth signal the metric exists to expose.
        """
        active_rejected: ColumnElement[int] = func.count().filter(
            DBResolutionNegative.kind == _REJECTED_PAIR,
            DBResolutionNegative.unrejected_at.is_(None),
        )
        pending_no_match: ColumnElement[int] = func.count().filter(
            DBResolutionNegative.kind == _NO_MATCH,
            DBResolutionNegative.check_again > func.now(),
        )
        result = await self.session.execute(
            select(active_rejected, pending_no_match).where(
                DBResolutionNegative.user_id == user_id
            )
        )
        rejected, pending = result.tuples().one()
        return NegativeCacheSize(
            rejected_pairs_active=rejected, no_match_pending=pending
        )


__all__ = ["ResolutionEventRepository", "ResolutionNegativeRepository"]
