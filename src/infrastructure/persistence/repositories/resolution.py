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
from datetime import UTC, datetime, timedelta
from typing import cast, override
from uuid import UUID

from attrs import define
from sqlalchemy import (
    ColumnElement,
    Interval,
    delete,
    func,
    literal_column,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from src.config import get_logger
from src.domain.entities.resolution_event import ResolutionEvent, ResolutionEventType
from src.domain.entities.resolution_negative import (
    NegativeKind,
    ResolutionNegative,
)
from src.domain.repositories.resolution import (
    NegativeCacheSize,
    NegativeListing,
    NegativeListingKind,
    RejectedPairRow,
)
from src.domain.services.resolution_retry import (
    DEATH_DEBOUNCE_FAILURES,
    DEATH_DEBOUNCE_MIN_SPAN_SECONDS,
    NO_MATCH_BACKOFF,
    next_no_match_check,
)
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBResolutionEvent,
    DBResolutionNegative,
    DBTrack,
    DBTrackMapping,
)
from src.infrastructure.persistence.database.live_rows import live_only
from src.infrastructure.persistence.repositories.base_repo import (
    BaseRepository,
    rows_affected,
)
from src.infrastructure.persistence.repositories.mappers import (
    BaseModelMapper,
    SimpleMapperFactory,
)
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)

_NO_MATCH: NegativeKind = "no_match"
_REJECTED_PAIR: NegativeKind = "rejected_pair"


def _interval(seconds: int) -> ColumnElement[timedelta]:
    """A literal PostgreSQL interval.

    ``literal_column`` rather than ``text`` so the expression carries a type:
    an untyped ``TextClause`` inside ``coalesce``/``least`` makes the whole
    comparison ``Any``, and the point of building these from column expressions
    is that the arithmetic stays checkable.
    """
    return literal_column(f"interval '{seconds} seconds'", type_=Interval())


def active_rejected_pair() -> ColumnElement[bool]:
    """A cannot-link constraint that has not been withdrawn."""
    return (DBResolutionNegative.kind == _REJECTED_PAIR) & (
        DBResolutionNegative.unrejected_at.is_(None)
    )


def pending_no_match() -> ColumnElement[bool]:
    """A backoff clock that has not yet come due."""
    return (DBResolutionNegative.kind == _NO_MATCH) & (
        DBResolutionNegative.check_again > func.now()
    )


def _miss_span() -> ColumnElement[timedelta]:
    """How long this row's run of misses has actually lasted.

    ``created_at`` is stamped by the first miss and never rewritten (the
    ON CONFLICT ``set_`` does not touch it); ``last_checked_at`` moves with
    every subsequent one. The difference is therefore bounded at *both* ends by
    an observation, which ``now() - created_at`` is not — that grows on its own
    while nothing happens, so a row would eventually satisfy any span threshold
    by sitting still.
    """
    return DBResolutionNegative.last_checked_at - DBResolutionNegative.created_at


def looks_dead() -> ColumnElement[bool]:
    """A backoff row that has missed enough times, over a long enough span.

    Both halves are required, and this is the whole death debounce: a count
    alone would condemn an id that flickered three times in one import run, and
    a span alone would condemn a single failure that happened to be old.

    The span is measured between the first miss and the most recent one, not
    from the first miss to *now*. Three failures in one afternoon must not
    become a death nine days later purely because nobody asked again — the row
    would be unchanged, and an id nothing has re-checked has produced no new
    evidence to condemn it with.

    Derived from the backoff row rather than from a streak scanned out of the
    event log. The row is written by the same call that observes the miss, it
    already resets on success (``clear_no_match`` deletes it), and it is keyed
    by connector — so every connector gets this for free rather than only the
    one that happened to emit ``suspect`` events.
    """
    return (
        (DBResolutionNegative.kind == _NO_MATCH)
        & (DBResolutionNegative.consecutive_misses >= DEATH_DEBOUNCE_FAILURES)
        & (_miss_span() >= _interval(DEATH_DEBOUNCE_MIN_SPAN_SECONDS))
    )


def actively_suppressing() -> ColumnElement[bool]:
    """A negative row that is *currently* withholding something.

    One definition because two readers depend on it and disagreeing would make
    both wrong in opposite directions: the cache-size metric would overstate
    the cache by counting rows that suppress nothing, and the orphan detector
    would accept a long-expired row as the excuse for a mapping-less connector
    track — hiding a genuine leak for as long as the row exists.
    """
    return active_rejected_pair() | pending_no_match()


# The doubling happens in SQL over the row's own previous interval — the
# ListenBrainz ``check_again`` shape — so the counter and the clock advance in
# the same atomic statement and two concurrent importers cannot lose a miss
# between them. ``coalesce`` covers the one shape the columns admit but the
# writers never produce (a row with NULL clocks): it falls back to the base
# interval rather than writing NULL, which would read as "no backoff at all".
#
# Built from column expressions rather than one raw SQL string. Inside an
# ON CONFLICT ``set_`` these render to the *existing* row's columns, which is
# what the doubling has to measure — identical SQL to the hand-written version,
# but the table and column names are now the model's rather than spelled out in
# a string that no rename would ever reach. It also puts ``last_checked_at`` on
# the same footing as the three predicate helpers above, each of which
# references its column symbolically.
_DOUBLED_CHECK_AGAIN = func.now() + func.least(
    func.coalesce(
        (DBResolutionNegative.check_again - DBResolutionNegative.last_checked_at) * 2,
        _interval(NO_MATCH_BACKOFF.base_seconds),
    ),
    _interval(NO_MATCH_BACKOFF.cap_seconds),
)


@define(frozen=True, slots=True)
class ResolutionEventMapper(BaseModelMapper[DBResolutionEvent, ResolutionEvent]):
    """Row ↔ entity for the append-only event log.

    ``to_db`` is deliberately absent (the base class's ``NotImplementedError``
    stub is what a caller would hit). It has exactly one call site in the
    codebase — ``BaseRepository.update`` — reached only when ``update()`` is
    given a domain model instead of a ``Mapping``, and ``ResolutionEventRepository``
    never calls ``update()``: this is an append-only log, so the only writer is
    ``append_events``, which builds column dicts via ``_to_row`` and never
    round-trips through the mapper. ``get_default_relationships`` is likewise
    absent — the base class's ``[]`` already says what it would.
    """

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


# Row ↔ entity for the negative cache. Every field on ResolutionNegative copies
# straight onto the same-named column on DBResolutionNegative; the only non-copy
# in the hand-written mapper this replaced was `cast("NegativeKind", ...)` on
# `kind`, and typing.cast is a no-op at runtime, so it added nothing
# SimpleMapperFactory doesn't already do (it copies the raw DB string straight
# through; the Literal is a compile-time-only annotation on the domain side
# either way). The generated mapper also needs no edit when a column is added
# or dropped, which the hand-written pair needed twice over.
ResolutionNegativeMapper = SimpleMapperFactory.create(
    DBResolutionNegative, ResolutionNegative
)


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
        connector_track_ids: Sequence[UUID],
        matcher_version: str,
    ) -> int:
        """Start or extend the no-match backoff for a batch of connector tracks.

        One atomic upsert, not a read-modify-write: two importers that miss the
        same id concurrently would otherwise both read ``consecutive_misses =
        n`` and both write ``n + 1``, losing a miss and holding the retry clock
        in. The counter increments and ``check_again`` doubles in the same
        statement, on the 1d→32d curve.

        One statement for the whole batch, not one per id: an import that finds
        300 absent ids was issuing 300 round-trips inside a single transaction
        to write 300 rows that differ only in an id and an interval.

        The jitter key is the connector track id, so a batch of ids that all
        missed together do not all come due in the same second — the same
        reason the play poller jitters. That is why each row carries its own
        interval expression rather than the batch sharing one. It applies to
        the *first* interval; the doublings inherit it, because they are
        computed from the row's own previous span.
        """
        if not connector_track_ids:
            return 0
        now = datetime.now(UTC)
        model = DBResolutionNegative
        rows = [
            {
                "user_id": user_id,
                "kind": _NO_MATCH,
                "connector_name": connector_name,
                "connector_track_id": connector_track_id,
                "candidate_track_id": None,
                "matcher_version": matcher_version,
                "consecutive_misses": 1,
                # Both clocks read the *database's* now so the span the doubling
                # measures next time is exactly the interval written here.
                # `next_no_match_check(0)` is the base interval: the first miss
                # must schedule 1 day, not the doubled one.
                "check_again": text(
                    f"now() + interval "
                    f"'{next_no_match_check(0, key=str(connector_track_id))} seconds'"
                ),
                "last_checked_at": func.now(),
                "created_at": now,
                "updated_at": now,
            }
            # dict.fromkeys rather than set(): duplicates inside one statement
            # would be a cardinality violation, and preserving order keeps the
            # emitted SQL stable across runs.
            for connector_track_id in dict.fromkeys(connector_track_ids)
        ]
        insert_stmt = pg_insert(model).values(rows)
        upsert = insert_stmt.on_conflict_do_update(
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
        ).returning(model.id)
        result = await self.session.execute(upsert)
        return len(result.scalars().all())

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

    @db_operation("list_negatives")
    async def list_negatives(
        self,
        *,
        user_id: str,
        kind: NegativeListingKind,
        connector_track_ids: Sequence[UUID] | None = None,
    ) -> list[NegativeListing]:
        """What the cache is holding against this user, identity joined in.

        One query, not one per row: connector-track and track identity are
        joined directly, because a listing has no business issuing a lookup per
        row.

        Two LEFT JOINs to tracks, because "the track this row is about" is
        reached differently per kind — a rejection *names* its candidate, while
        a dead id is only reachable through the live mapping that still points
        at it. Coalescing them is what lets both kinds share this query and the
        renderer above it, rather than growing a second near-identical listing
        the moment the second kind needed one. The mapping join cannot multiply
        rows: ``uq_track_mappings_live_connector`` admits one live mapping per
        (user, connector track, connector).

        Scoped by ``user_id`` in the WHERE clause rather than relying on RLS —
        PDR-002 records the Neon owner role as BYPASSRLS in production, so RLS
        is inert there and this predicate is the actual isolation.
        """
        if connector_track_ids is not None and not connector_track_ids:
            return []
        conditions = [
            DBResolutionNegative.user_id == user_id,
            active_rejected_pair() if kind == "rejected" else looks_dead(),
        ]
        if connector_track_ids is not None:
            conditions.append(
                DBResolutionNegative.connector_track_id.in_(connector_track_ids)
            )

        mapped_track = aliased(DBTrack)
        track_id: ColumnElement[UUID | None] = func.coalesce(
            DBResolutionNegative.candidate_track_id, mapped_track.id
        )
        track_title: ColumnElement[str | None] = func.coalesce(
            DBTrack.title, mapped_track.title
        )
        result = await self.session.execute(
            select(
                DBResolutionNegative.connector_track_id,
                DBConnectorTrack.connector_name,
                DBConnectorTrack.connector_track_identifier,
                track_id,
                track_title,
                DBResolutionNegative.consecutive_misses,
            )
            .join(
                DBConnectorTrack,
                DBConnectorTrack.id == DBResolutionNegative.connector_track_id,
            )
            .outerjoin(DBTrack, DBTrack.id == DBResolutionNegative.candidate_track_id)
            .outerjoin(
                DBTrackMapping,
                (
                    DBTrackMapping.connector_track_id
                    == DBResolutionNegative.connector_track_id
                )
                & (DBTrackMapping.user_id == DBResolutionNegative.user_id)
                & live_only(DBTrackMapping),
            )
            .outerjoin(mapped_track, mapped_track.id == DBTrackMapping.track_id)
            .where(*conditions)
            .order_by(
                DBResolutionNegative.consecutive_misses.desc(),
                DBResolutionNegative.connector_track_id,
                DBResolutionNegative.candidate_track_id,
            )
        )
        return [
            NegativeListing(
                connector_track_id=connector_track_id,
                connector_name=connector_name,
                connector_track_identifier=connector_track_identifier,
                track_id=listed_track_id,
                track_title=listed_title,
                consecutive_misses=misses,
            )
            for (
                connector_track_id,
                connector_name,
                connector_track_identifier,
                listed_track_id,
                listed_title,
                misses,
            ) in result.tuples()
        ]

    @db_operation("count_active_negatives")
    async def count_active_negatives(self, *, user_id: str) -> NegativeCacheSize:
        """Size of each half of the cache for one tenant.

        "Active" means *currently suppressing something*: a rejection that has
        not been withdrawn, a backoff clock that has not yet come due. A row
        whose ``check_again`` has passed is not suppressing anything — the next
        pass will re-ask — so counting it would overstate the cache and hide
        the growth signal the metric exists to expose.
        """
        rejected_count: ColumnElement[int] = func.count().filter(active_rejected_pair())
        no_match_count: ColumnElement[int] = func.count().filter(pending_no_match())
        dead_count: ColumnElement[int] = func.count().filter(looks_dead())
        result = await self.session.execute(
            select(rejected_count, no_match_count, dead_count).where(
                DBResolutionNegative.user_id == user_id
            )
        )
        rejected, pending, dead = result.tuples().one()
        return NegativeCacheSize(
            rejected_pairs_active=rejected,
            no_match_pending=pending,
            dead_id_candidates=dead,
        )


__all__ = ["ResolutionEventRepository", "ResolutionNegativeRepository"]
