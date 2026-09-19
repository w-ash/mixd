"""One mapping mechanism for every typed mapping table (v0.12.0.2).

Tracks, artists and albums each keep "this connector row is this canonical
entity" in a table of their own — RLS is per table, the FK semantics differ
per entity, and the live-uniqueness index is per entity — but all three need
the same four things done to it: assert a batch of mappings, keep the live
rows unique, elect one primary per (canonical, connector) pair, and record the
decision as an event. :class:`MappingRepository` is that mechanism, addressed
by a :class:`MappingShape` that names the two entity-specific columns and says
whether the table carries supersession.

The track instantiation (``track/connector.py``) is the one exercised in
production; its ``assert_mappings`` shape — two statements, a deferred
self-FK, a 23505 retry — is PG17-verified and reproduced here unchanged.
Everything the generic does not know about an entity (the
``DBTrack.mappings`` identity-map collection) is a hook the instantiation
overrides.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from itertools import starmap
from typing import Final, NamedTuple, Self, cast
from uuid import UUID, uuid7

from attrs import define, field
from psycopg.errors import UniqueViolation
from sqlalchemy import (
    Boolean,
    ColumnCollection,
    ColumnElement,
    FromClause,
    Tuple,
    case,
    column,
    func,
    literal_column,
    select,
    true,
    tuple_,
    update,
    values,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID, insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import KeyedColumnElement

from src.config import get_logger
from src.domain.entities.resolution_event import EntityKind
from src.domain.entities.shared import JsonDict
from src.domain.entities.track_mapping import (
    STALE_ID_FOR,
    MatchMethod,
    SupersessionReason,
)
from src.domain.matching.types import final_score_of
from src.domain.repositories.mapping import (
    ElectionMode,
    PrimaryCandidate,
    PrimaryVacancyRepair,
)
from src.domain.repositories.resolution import (
    ResolutionDecision,
    ResolutionRecorderProtocol,
    SupersessionEdge,
)
from src.infrastructure.persistence.database.live_rows import (
    expire_mapping_identity,
)
from src.infrastructure.persistence.database.models import DatabaseModel
from src.infrastructure.persistence.repositories.base_repo import BaseRepository
from src.infrastructure.persistence.repositories.mappers import ModelMapper
from src.infrastructure.persistence.repositories.repo_decorator import db_operation
from src.infrastructure.services.resolution_recorder import ResolutionRecorder

logger = get_logger(__name__)

# ``assert_mappings`` races another writer on the same live key: ON CONFLICT
# guarantees atomic insert-or-update, but the branch where the conflicting row
# is superseded mid-statement is undocumented PostgreSQL internals, so a unique
# violation is retried rather than trusted away. ``db_operation`` does not
# retry anything.
_ASSERT_RETRY_ATTEMPTS: Final = 3

# Columns an asserted mapping row may carry, with the defaults applied when a
# caller omits them. Every row in one INSERT must present the same key set.
# ``match_method`` has no default: a decision without a method is not one.
_ASSERT_DEFAULTS: Final[Mapping[str, object]] = {
    "confidence": 0,
    "confidence_evidence": None,
    "origin": "automatic",
    "is_primary": False,
}

# A stale-id mapping is a cache entry for a dead connector id, written beside
# the live id it was redirected to so a later import carrying the old id still
# resolves from cache. It never holds primacy: a pair's primary is the mapping
# that names its current identity, and a dead id is not one. Every election
# reads this predicate; a pair whose only live rows are stale-id ones has no
# live identity and is neither elected nor reported as a vacancy.
STALE_ID_METHODS: Final[frozenset[MatchMethod]] = frozenset(STALE_ID_FOR.values())


@define(frozen=True, slots=True)
class MappingShape:
    """How one mapping table is addressed.

    ``owner_id_col`` is the canonical entity's column (``track_id``,
    ``artist_id``), ``connector_id_col`` the connector row's
    (``connector_track_id``, ``connector_artist_id``). ``live_key`` is the
    unique key the assert conflicts on — the partial live index where the
    table supersedes, a plain unique constraint where it does not. Every other
    column the mechanism touches (``user_id``, ``connector_name``,
    ``match_method``, ``confidence``, ``confidence_evidence``, ``origin``,
    ``is_primary``, ``last_seen_at``) is spelled the same on every table.

    ``supersession`` is a per-table capability: with it, a changed decision
    retires the incumbent and inserts a successor (the append-only ledger);
    without it, the row is the mapping and a changed decision rewrites it in
    place — the event log is then the only history, which is why
    :meth:`MappingRepository.record_assertion` reports rewritten rows too.
    """

    entity_kind: EntityKind
    owner_id_col: str
    connector_id_col: str
    live_key: tuple[str, ...]
    supersession: bool

    @property
    def required_keys(self) -> tuple[str, ...]:
        """Keys every asserted row must carry."""
        return (
            "user_id",
            self.owner_id_col,
            self.connector_id_col,
            "connector_name",
            "match_method",
        )

    @property
    def decision_cols(self) -> tuple[str, ...]:
        """The identity of a mapping *decision*: owner, confidence, method, origin.

        A change to any of them retires (or rewrites) the incumbent; every
        other field — evidence, freshness, primacy — is drift and never does,
        or an enrichment refresh would grow the chain (ROR's rule).
        """
        return (self.owner_id_col, "confidence", "match_method", "origin")


@define(frozen=True, slots=True)
class MappingColumns:
    """Typed handles on one mapping table's columns, or on an alias of them.

    Built from a column collection rather than from ORM attributes so the same
    code addresses the model's table, an alias of it for the peer guard, and
    an INSERT's ``EXCLUDED`` pseudo-row. The casts here are the one place the
    entity-specific column names meet the type system.
    """

    id: ColumnElement[UUID]
    user_id: ColumnElement[str]
    owner_id: ColumnElement[UUID]
    connector_id: ColumnElement[UUID]
    connector_name: ColumnElement[str]
    match_method: ColumnElement[str]
    confidence: ColumnElement[int]
    confidence_evidence: ColumnElement[JsonDict | None]
    origin: ColumnElement[str]
    is_primary: ColumnElement[bool]
    last_seen_at: ColumnElement[datetime | None]
    updated_at: ColumnElement[datetime]
    live_key: tuple[ColumnElement[object], ...]
    # Absent on a table without supersession.
    superseded_at: ColumnElement[datetime | None] | None
    superseded_by_id: ColumnElement[UUID | None] | None

    @classmethod
    def of(
        cls,
        columns: ColumnCollection[str, KeyedColumnElement[object]],
        shape: MappingShape,
    ) -> Self:
        """Resolve the shape against a table, alias or ``EXCLUDED`` collection."""
        return cls(
            id=cast("ColumnElement[UUID]", columns["id"]),
            user_id=cast("ColumnElement[str]", columns["user_id"]),
            owner_id=cast("ColumnElement[UUID]", columns[shape.owner_id_col]),
            connector_id=cast("ColumnElement[UUID]", columns[shape.connector_id_col]),
            connector_name=cast("ColumnElement[str]", columns["connector_name"]),
            match_method=cast("ColumnElement[str]", columns["match_method"]),
            confidence=cast("ColumnElement[int]", columns["confidence"]),
            confidence_evidence=cast(
                "ColumnElement[JsonDict | None]", columns["confidence_evidence"]
            ),
            origin=cast("ColumnElement[str]", columns["origin"]),
            is_primary=cast("ColumnElement[bool]", columns["is_primary"]),
            last_seen_at=cast(
                "ColumnElement[datetime | None]", columns["last_seen_at"]
            ),
            updated_at=cast("ColumnElement[datetime]", columns["updated_at"]),
            live_key=tuple(
                cast("ColumnElement[object]", columns[name]) for name in shape.live_key
            ),
            superseded_at=cast(
                "ColumnElement[datetime | None]", columns["superseded_at"]
            )
            if shape.supersession
            else None,
            superseded_by_id=cast(
                "ColumnElement[UUID | None]", columns["superseded_by_id"]
            )
            if shape.supersession
            else None,
        )

    @property
    def live(self) -> ColumnElement[bool]:
        """Rows that are current identity.

        ``superseded_at IS NULL`` where the table keeps history; every row on a
        table that does not. Core statements bypass the ORM's live-rows
        listener entirely, so every statement here names this itself.
        """
        if self.superseded_at is None:
            return true()
        return self.superseded_at.is_(None)

    @property
    def successor_pointer(self) -> ColumnElement[UUID | None]:
        """``superseded_by_id`` on a table that supersedes; a shape error otherwise."""
        if self.superseded_by_id is None:
            raise TypeError("this mapping table carries no supersession columns")
        return self.superseded_by_id

    @property
    def electable(self) -> ColumnElement[bool]:
        """Rows that may hold primacy — paired with :attr:`live` in every election."""
        return self.match_method.notin_(STALE_ID_METHODS)

    @property
    def decision(self) -> Tuple:
        """``ROW(owner, confidence, method, origin)`` — NULL-safe under IS DISTINCT FROM."""
        return tuple_(self.owner_id, self.confidence, self.match_method, self.origin)


class _Incumbent(NamedTuple):
    """What a live row on one of the batch's keys held before the upsert."""

    id: UUID
    owner_id: UUID
    confidence: int
    match_method: str
    origin: str
    is_primary: bool

    @property
    def decision(self) -> tuple[object, ...]:
        return (self.owner_id, self.confidence, self.match_method, self.origin)


@define(frozen=True, slots=True)
class AssertedMappingRow:
    """One row ``assert_mappings`` actually wrote this batch — new, successor, or rewritten.

    Populated in the assert straight from ``prepared`` (the repository's own
    normalised insert rows, keyed by the pre-generated ``id``), never re-read
    from the database. :meth:`MappingRepository.record_assertion` groups over
    these to emit events instead of re-querying: within one transaction, the
    row this session just wrote already *is* what a same-transaction SELECT
    would return.
    """

    id: UUID
    user_id: str
    owner_id: UUID
    connector_id: UUID
    connector_name: str
    confidence: int
    origin: str
    confidence_evidence: JsonDict | None


@define(frozen=True, slots=True)
class MappingAssertion:
    """Outcome of one ``assert_mappings`` batch.

    ``superseded`` maps each retired mapping to the successor that replaced it
    — the edges a resolution event needs. ``rewritten`` names the rows whose
    decision changed *in place*, which only a table without supersession
    columns does; both earn an event, and neither is a ``touched`` row.

    ``primacy_restorations`` names the pairs whose retired (or rewritten)
    incumbent had been primary, for the caller to re-elect on the successor's
    owner. ``vacated_owners`` names the (owner, connector) pairs a changed
    decision left without a primary because the successor landed on a
    *different* owner; the restoration above only re-promotes on the new one,
    and the departed owner needs its own heal (a surviving sibling promoted)
    — the FM4d drift migration 044's pre-pass had to repair on 366 production
    rows.

    ``written`` carries the full row for every mapping that landed live this
    batch (``created`` plus ``superseded.values()`` plus ``rewritten``); it is
    what :meth:`MappingRepository.record_assertion` sources its events from.
    ``reason`` is the reason stamped on every retired row this batch produced,
    so event emission agrees with the column.
    """

    created: tuple[UUID, ...] = ()
    touched: tuple[UUID, ...] = ()
    rewritten: tuple[UUID, ...] = ()
    superseded: Mapping[UUID, UUID] = field(factory=dict[UUID, UUID])
    primacy_restorations: tuple[PrimaryCandidate, ...] = ()
    vacated_owners: tuple[tuple[UUID, str], ...] = ()
    written: tuple[AssertedMappingRow, ...] = ()
    reason: SupersessionReason = "rematch"


def _is_unique_violation(error: IntegrityError) -> bool:
    """True for SQLSTATE 23505 — psycopg3 maps unique_violation to this class."""
    return isinstance(error.orig, UniqueViolation)


# The columns a superseding table must carry. ``supersession_reason`` is only
# ever a string key in the assert's ``set_`` — never resolved through
# ``MappingColumns`` — so without this check a table missing just that one
# passes construction and fails on its first changed decision.
_SUPERSESSION_COLUMNS: Final = (
    "superseded_at",
    "superseded_by_id",
    "supersession_reason",
)


def _require_shape_columns(table: FromClause, shape: MappingShape) -> None:
    """Fail at construction, naming shape and table, when the two disagree."""
    required = [shape.owner_id_col, shape.connector_id_col, *shape.live_key]
    if shape.supersession:
        required.extend(_SUPERSESSION_COLUMNS)
    missing = sorted(name for name in required if name not in table.c)
    if missing:
        raise ValueError(
            f"MappingShape({shape.entity_kind!r}) names column(s) {missing} "
            f"that table {table.description!r} does not have"
        )


class MappingRepository[DBM: DatabaseModel, M](BaseRepository[DBM, M]):
    """Assert, scope, elect and record for one typed mapping table.

    Instantiate with a :class:`MappingShape`; override the
    :meth:`_expire_owner_identity` hook where the entity keeps state outside
    its mapping table.
    """

    shape: MappingShape
    table: FromClause
    columns: MappingColumns

    def __init__(
        self,
        session: AsyncSession,
        *,
        model_class: type[DBM],
        mapper: ModelMapper[DBM, M],
        shape: MappingShape,
    ) -> None:
        super().__init__(session=session, model_class=model_class, mapper=mapper)
        self.shape = shape
        self.table = model_class.__table__
        _require_shape_columns(self.table, shape)
        self.columns = MappingColumns.of(self.table.c, shape)

    # ── hooks ────────────────────────────────────────────────────────

    def _expire_owner_identity(self, owner_ids: Sequence[UUID]) -> None:
        """Drop in-session owner objects whose mapping collection just changed.

        Core DML never touches the identity map; a canonical row whose
        ``mappings`` collection was loaded keeps serving the retired copy.
        The track instantiation expires ``DBTrack.mappings`` here.
        """

    # ── live scoping ─────────────────────────────────────────────────

    def _expire_identity(
        self, *, mapping_ids: Sequence[UUID], owner_ids: Sequence[UUID]
    ) -> None:
        """Drop stale in-session copies after a Core-level mapping mutation."""
        expire_mapping_identity(
            self.session, mapping_ids=mapping_ids, model=self.model_class
        )
        self._expire_owner_identity(owner_ids)

    # ── assert ───────────────────────────────────────────────────────

    @db_operation("assert_mappings")
    async def assert_mappings(
        self,
        rows: Sequence[Mapping[str, object]],
        *,
        reason: SupersessionReason = "rematch",
    ) -> MappingAssertion:
        """Assert a batch of mappings; the database decides each row's outcome.

        Three outcomes per row, never a read-modify-write:

        - **new key** → inserted live;
        - **same key, same decision** → freshness touch only (``last_seen_at``
          moves, no new row — the no-churn rule);
        - **same key, different decision** → with supersession, the incumbent
          is retired (``superseded_at`` / ``supersession_reason`` /
          ``superseded_by_id``) and the successor inserted live; without it,
          the row is rewritten in place and reported as ``rewritten``.

        With supersession this is two statements in one transaction: an
        ``ON CONFLICT … DO UPDATE`` restating the partial index predicate
        (PostgreSQL requires it to infer a partial unique index), then a plain
        insert of exactly the successors the first statement named — those
        keys are free by then, because their predecessors left the partial
        index the moment they were superseded. Without it, the first statement
        is the whole write.
        """
        if not rows:
            return MappingAssertion(reason=reason)

        prepared = self._deduplicate_batch(
            [self._prepare_assert_row(row) for row in rows],
            list(self.shape.live_key),
            label="assert_mappings",
        )

        remaining = _ASSERT_RETRY_ATTEMPTS
        while True:
            remaining -= 1
            try:
                # Savepoint per attempt: a unique violation poisons the
                # transaction, so a retry needs a clean point to resume from.
                async with self.session.begin_nested():
                    if self.shape.supersession:
                        return await self._assert_superseding(prepared, reason)
                    return await self._assert_in_place(prepared, reason)
            except IntegrityError as error:
                if remaining <= 0 or not _is_unique_violation(error):
                    raise
                logger.warning(
                    "assert_mappings_conflict_retry",
                    remaining=remaining,
                    rows=len(prepared),
                )

    def _prepare_assert_row(self, row: Mapping[str, object]) -> dict[str, object]:
        """Normalise one caller row: uniform key set, timestamps, successor id.

        The ``id`` is pre-generated because it does double duty — it is the id
        of the row when the key is new, and the ``EXCLUDED.id`` the conflicting
        incumbent stores in ``superseded_by_id`` when the decision changed.
        """
        now = datetime.now(UTC)
        return {
            "id": uuid7(),
            **{key: row[key] for key in self.shape.required_keys},
            **{key: row.get(key, default) for key, default in _ASSERT_DEFAULTS.items()},
            "last_seen_at": row.get("last_seen_at") or now,
            "created_at": now,
            "updated_at": now,
        }

    def _key_of(self, row: Mapping[str, object]) -> tuple[object, ...]:
        return tuple(row[name] for name in self.shape.live_key)

    def _decision_of(self, row: Mapping[str, object]) -> tuple[object, ...]:
        return tuple(row[name] for name in self.shape.decision_cols)

    def _written_row(
        self, row: Mapping[str, object], *, id_: UUID | None = None
    ) -> AssertedMappingRow:
        return AssertedMappingRow(
            id=id_ if id_ is not None else cast("UUID", row["id"]),
            user_id=cast("str", row["user_id"]),
            owner_id=cast("UUID", row[self.shape.owner_id_col]),
            connector_id=cast("UUID", row[self.shape.connector_id_col]),
            connector_name=cast("str", row["connector_name"]),
            confidence=cast("int", row["confidence"]),
            origin=cast("str", row["origin"]),
            confidence_evidence=cast("JsonDict | None", row["confidence_evidence"]),
        )

    def _key_predicate(
        self, prepared: Sequence[Mapping[str, object]]
    ) -> ColumnElement[bool]:
        """Rows on any of this batch's live keys."""
        return tuple_(*self.columns.live_key).in_([
            self._key_of(row) for row in prepared
        ])

    async def _live_primary_keys(
        self, prepared: Sequence[Mapping[str, object]]
    ) -> set[tuple[object, ...]]:
        """This batch's live keys whose row currently holds primacy.

        All a superseding assert needs of its incumbents. Read *before* the
        upsert because the upsert destroys the answer: a retired row's
        ``is_primary`` is cleared in the same statement, and ``RETURNING``
        reports post-update values.
        """
        cols = self.columns
        result = await self.session.execute(
            select(*cols.live_key).where(
                self._key_predicate(prepared), cols.is_primary.is_(True), cols.live
            )
        )
        keys = cast("Sequence[Sequence[object]]", result.all())
        return {tuple(key) for key in keys}

    async def _live_incumbents(
        self, prepared: Sequence[Mapping[str, object]]
    ) -> dict[tuple[object, ...], _Incumbent]:
        """What each of this batch's live keys currently holds, by key.

        The in-place assert needs the whole decision to tell a touch from a
        rewrite; read before the upsert for the same reason as
        :meth:`_live_primary_keys`.
        """
        cols = self.columns
        result = await self.session.execute(
            select(
                cols.id,
                cols.owner_id,
                cols.confidence,
                cols.match_method,
                cols.origin,
                cols.is_primary,
                *cols.live_key,
            ).where(self._key_predicate(prepared), cols.live)
        )
        # (*decision columns, *live_key) — the key's width is the shape's.
        rows = cast("Sequence[Sequence[object]]", result.all())
        return {tuple(row[6:]): _Incumbent._make(row[:6]) for row in rows}

    async def _assert_superseding(
        self, prepared: list[dict[str, object]], reason: SupersessionReason
    ) -> MappingAssertion:
        """The append-only write: touch, or retire the incumbent and insert a successor."""
        cols = self.columns
        successor_pointer = cols.successor_pointer
        primary_keys = await self._live_primary_keys(prepared)

        insert_stmt = pg_insert(self.model_class).values(prepared)
        excluded = MappingColumns.of(insert_stmt.excluded, self.shape)
        # ROW(...) IS DISTINCT FROM ROW(...) keeps the comparison NULL-safe
        # across the whole tuple, which ``!=`` would not.
        decision_differs = cols.decision.is_distinct_from(excluded.decision)
        # else_=None on all three is what keeps an unchanged row's supersession
        # columns correct: the conflict target is the *live* partial index, so
        # a row reached here is live by construction and its three columns are
        # already NULL — writing NULL preserves them instead of clobbering.
        upsert = (
            insert_stmt
            .on_conflict_do_update(
                index_elements=list(self.shape.live_key),
                index_where=cols.live,
                set_={
                    "last_seen_at": excluded.last_seen_at,
                    "updated_at": excluded.updated_at,
                    # Evidence refreshes without superseding: it is the
                    # *explanation* of a decision, not the decision. But only
                    # on a freshness touch — when the decision changed, this
                    # row is about to become history, and history explained by
                    # the evidence for a *different* decision is worse than no
                    # explanation at all; the successor carries the new
                    # evidence.
                    "confidence_evidence": case(
                        (decision_differs, cols.confidence_evidence),
                        else_=excluded.confidence_evidence,
                    ),
                    "superseded_at": case((decision_differs, func.now()), else_=None),
                    "superseded_by_id": case(
                        (decision_differs, excluded.id), else_=None
                    ),
                    "supersession_reason": case((decision_differs, reason), else_=None),
                    # A retired row must not keep primacy: it would collide
                    # with its successor under the primary partial unique.
                    # else_ keeps an unchanged (merely touched) row exactly as
                    # it was.
                    "is_primary": case(
                        (decision_differs, False), else_=cols.is_primary
                    ),
                },
            )
            # owner is the *predecessor's* — supersession does not rewrite
            # it, so this is the owner the mapping is leaving behind.
            .returning(cols.id, successor_pointer, cols.owner_id)
            .execution_options(synchronize_session=False)
        )
        result = await self.session.execute(upsert)
        affected: Sequence[tuple[UUID, UUID | None, UUID]] = result.tuples().all()

        inserted_ids = {row["id"] for row in prepared}
        created = tuple(mid for mid, _, _ in affected if mid in inserted_ids)
        touched = tuple(
            mid
            for mid, successor, _ in affected
            if mid not in inserted_ids and successor is None
        )
        superseded = {
            mid: successor for mid, successor, _ in affected if successor is not None
        }
        departed_from = {
            successor: owner_id
            for _, successor, owner_id in affected
            if successor is not None
        }
        written_ids = set(created) | set(superseded.values())
        written = tuple(
            self._written_row(row) for row in prepared if row["id"] in written_ids
        )

        restorations: tuple[PrimaryCandidate, ...] = ()
        vacated: tuple[tuple[UUID, str], ...] = ()
        if superseded:
            successors = [
                row for row in prepared if row["id"] in set(superseded.values())
            ]
            _ = await self.session.execute(
                pg_insert(self.model_class).values(successors)
            )
            was_primary = [
                row for row in successors if self._key_of(row) in primary_keys
            ]
            restorations = tuple(self._candidate_of(row) for row in was_primary)
            # A re-score that also moves the mapping to another owner leaves
            # the old one with no primary. Only the successor's owner is
            # re-promoted above, so name the departed owner for the healer.
            vacated = tuple({
                (departed, cast("str", row["connector_name"]))
                for row in was_primary
                if (departed := departed_from.get(cast("UUID", row["id"]))) is not None
                and departed != row[self.shape.owner_id_col]
            })
            self._expire_identity(
                mapping_ids=(*superseded.keys(), *superseded.values()),
                owner_ids=[
                    cast("UUID", row[self.shape.owner_id_col]) for row in successors
                ],
            )

        return MappingAssertion(
            created=created,
            touched=touched,
            superseded=superseded,
            primacy_restorations=restorations,
            vacated_owners=vacated,
            written=written,
            reason=reason,
        )

    async def _assert_in_place(
        self, prepared: list[dict[str, object]], reason: SupersessionReason
    ) -> MappingAssertion:
        """The plain write for a table without supersession: touch or rewrite.

        One statement. A same-decision conflict is a freshness touch exactly
        as with supersession; a changed decision overwrites the decision
        columns in place — the row *is* the mapping, and the event log is the
        history. Primacy is cleared on a rewrite for the same reason a
        retirement clears it: the pair the row now belongs to may already
        hold a primary, and two would violate the primary partial unique.

        Insert-or-update is read off ``RETURNING`` (``xmax = 0`` is true only
        for a freshly inserted tuple), not inferred from the pre-upsert read:
        a row another transaction committed between ``_live_incumbents`` and
        this statement is updated here without ever having been read. Touch
        versus rewrite still needs the incumbent's decision, so such a row —
        updated, but with no incumbent on record — is classified as rewritten:
        the event and the (vacancy-only) restoration are the safe side of not
        knowing, and its prior owner, which nobody read, cannot be reported
        as vacated.
        """
        incumbents = await self._live_incumbents(prepared)
        cols = self.columns
        insert_stmt = pg_insert(self.model_class).values(prepared)
        excluded = MappingColumns.of(insert_stmt.excluded, self.shape)
        decision_differs = cols.decision.is_distinct_from(excluded.decision)
        upsert = (
            insert_stmt
            .on_conflict_do_update(
                index_elements=list(self.shape.live_key),
                set_={
                    "last_seen_at": excluded.last_seen_at,
                    "updated_at": excluded.updated_at,
                    "confidence_evidence": excluded.confidence_evidence,
                    self.shape.owner_id_col: excluded.owner_id,
                    "confidence": excluded.confidence,
                    "match_method": excluded.match_method,
                    "origin": excluded.origin,
                    "is_primary": case(
                        (decision_differs, False), else_=cols.is_primary
                    ),
                },
            )
            .returning(cols.id, literal_column("xmax = 0", Boolean), *cols.live_key)
            .execution_options(synchronize_session=False)
        )
        result = await self.session.execute(upsert)
        # (id, inserted, *live_key) — the key's width is the shape's.
        returned: Sequence[Sequence[object]] = result.all()
        affected = [
            (cast("UUID", row[0]), cast("bool", row[1]), tuple(row[2:]))
            for row in returned
        ]

        by_key = {self._key_of(row): row for row in prepared}
        created: list[UUID] = []
        touched: list[UUID] = []
        rewritten: list[UUID] = []
        written: list[AssertedMappingRow] = []
        restorations: list[PrimaryCandidate] = []
        vacated: set[tuple[UUID, str]] = set()
        for mapping_id, inserted, key in affected:
            row = by_key[key]
            if inserted:
                created.append(mapping_id)
                written.append(self._written_row(row))
                continue
            incumbent = incumbents.get(key)
            if incumbent is not None and incumbent.decision == self._decision_of(row):
                touched.append(mapping_id)
                continue
            rewritten.append(mapping_id)
            written.append(self._written_row(row, id_=mapping_id))
            if incumbent is None or incumbent.is_primary:
                restorations.append(self._candidate_of(row))
            if incumbent is not None and incumbent.is_primary:
                new_owner = cast("UUID", row[self.shape.owner_id_col])
                if incumbent.owner_id != new_owner:
                    vacated.add((
                        incumbent.owner_id,
                        cast("str", row["connector_name"]),
                    ))
        if rewritten:
            self._expire_identity(
                mapping_ids=rewritten,
                owner_ids=[
                    *(cast("UUID", row[self.shape.owner_id_col]) for row in prepared),
                    *(owner for owner, _ in vacated),
                ],
            )

        return MappingAssertion(
            created=tuple(created),
            touched=tuple(touched),
            rewritten=tuple(rewritten),
            primacy_restorations=tuple(restorations),
            vacated_owners=tuple(vacated),
            written=tuple(written),
            reason=reason,
        )

    def _candidate_of(self, row: Mapping[str, object]) -> PrimaryCandidate:
        return PrimaryCandidate(
            owner_id=cast("UUID", row[self.shape.owner_id_col]),
            connector_name=cast("str", row["connector_name"]),
            connector_id=cast("UUID", row[self.shape.connector_id_col]),
        )

    # ── manual overrides ─────────────────────────────────────────────

    async def filter_manual_overrides(
        self, rows: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        """Drop rows whose connector row has a live manual-override mapping.

        ``manual_override`` rows are user-pinned identity decisions — an
        automatic bulk assert must never clobber them. Per user: connector
        rows are shared across tenants, and one user's pin says nothing about
        another's mapping of the same row.
        """
        if not rows:
            return rows
        cols = self.columns
        connector_ids = [row[self.shape.connector_id_col] for row in rows]
        user_ids = sorted({cast("str", row["user_id"]) for row in rows})
        result = await self.session.execute(
            select(cols.user_id, cols.connector_id).where(
                cols.user_id.in_(user_ids),
                cols.connector_id.in_(connector_ids),
                cols.origin == "manual_override",
                cols.live,
            )
        )
        pinned = set(result.tuples().all())
        if not pinned:
            return rows
        return [
            row
            for row in rows
            if (row["user_id"], row[self.shape.connector_id_col]) not in pinned
        ]

    # ── election ─────────────────────────────────────────────────────

    @db_operation("ensure_primaries")
    async def ensure_primaries(
        self, candidates: Sequence[PrimaryCandidate], *, mode: ElectionMode
    ) -> list[PrimaryCandidate]:
        """Elect the named mapping primary for each (owner, connector) pair.

        The one election. ``fill`` promotes into a vacancy only: the ``NOT
        EXISTS`` peer guard leaves a pair that already has a live primary
        exactly as it is, whether that primary is user-pinned or automatic —
        an arriving mapping's confidence is not a mandate to overrule a
        decision the owner already holds. ``reset`` deposes the pair's live
        primaries first, so the same guard is vacuously true and "promote only
        into a vacancy" and "promote" coincide.

        Under a savepoint, and raising: a failed election used to be caught,
        logged and reported as ``False``, which left the caller's transaction
        poisoned with nothing to tell it so.

        **Deduplicated first, by (owner, connector), first wins.** The primary
        partial unique admits one live primary per (user, owner, connector),
        and the promotion's guard is evaluated against the statement-start
        snapshot — it cannot see rows this same UPDATE is promoting. Two rows
        for one pair in one batch would therefore both pass the guard and
        collide.

        Returns the candidates promoted; a ``fill`` over an occupied pair
        promotes nothing and is not an error. A ``reset`` that promotes
        fewer pairs than it was asked to *is*: the deposition already ran, so
        releasing the savepoint would leave those pairs with no primary. The
        ``ValueError`` rolls the savepoint back with the incumbents intact —
        the named row is a stale-id cache entry, or was retired under the
        caller's feet.
        """
        if not candidates:
            return []
        deduped: dict[tuple[UUID, str], PrimaryCandidate] = {}
        for candidate in candidates:
            _ = deduped.setdefault(
                (candidate.owner_id, candidate.connector_name), candidate
            )
        elect = list(deduped.values())
        async with self.session.begin_nested():
            if mode == "reset":
                await self._reset_primaries(elect)
            promoted = await self._promote_into_vacancies(elect)
            if mode == "reset" and len(promoted) < len(elect):
                raise ValueError(
                    f"reset election promoted {len(promoted)} of {len(elect)} "
                    f"{self.shape.entity_kind} mapping(s): a named mapping is "
                    "not live or is a stale-id row and cannot be primary"
                )
            return promoted

    async def _reset_primaries(self, candidates: Sequence[PrimaryCandidate]) -> None:
        """Clear ``is_primary`` on every live mapping of these (owner, connector) pairs.

        Grouped by connector so each statement clears only the owners that
        actually appear under *that* connector. The cross-product of every
        owner and every connector in the batch would demote primaries no
        caller asked about and promote nothing back — the exact FM4d drift
        the promotion path exists to prevent.
        """
        cols = self.columns
        by_connector: dict[str, list[UUID]] = defaultdict(list)
        for candidate in candidates:
            by_connector[candidate.connector_name].append(candidate.owner_id)
        for connector_name, owner_ids in by_connector.items():
            _ = await self.session.execute(
                update(self.model_class)
                .where(
                    cols.owner_id.in_(owner_ids),
                    cols.connector_name == connector_name,
                    cols.live,
                )
                .values(is_primary=False)
            )

    def _no_live_primary(self) -> ColumnElement[bool]:
        """No live primary on the row's (user, owner, connector) pair — the vacancy guard."""
        cols = self.columns
        peer = MappingColumns.of(self.table.alias("peer").c, self.shape)
        return (
            ~select(peer.id)
            .where(
                peer.user_id == cols.user_id,
                peer.owner_id == cols.owner_id,
                peer.connector_name == cols.connector_name,
                peer.is_primary.is_(True),
                peer.live,
            )
            .correlate(self.table)
            .exists()
        )

    async def _promote_into_vacancies(
        self, candidates: Sequence[PrimaryCandidate]
    ) -> list[PrimaryCandidate]:
        """Fill a vacant primary slot for each pair, in one statement.

        ``candidates`` is one per (owner, connector) — :meth:`ensure_primaries`
        deduplicates before calling.

        **``RETURNING`` is the "promotion landed" signal**: the caller needs
        to know exactly which pairs moved, and with one statement for the whole
        batch there is no per-row count to read instead.
        """
        cols = self.columns
        promotion_values = values(
            column("owner_id", PGUUID(as_uuid=True)),
            column("connector_id", PGUUID(as_uuid=True)),
            name="promotion_values",
        ).data([(c.owner_id, c.connector_id) for c in candidates])

        result = await self.session.execute(
            update(self.model_class)
            .where(
                cols.owner_id == promotion_values.c.owner_id,
                cols.connector_id == promotion_values.c.connector_id,
                cols.live,
                cols.electable,
                self._no_live_primary(),
            )
            .values(is_primary=True)
            .returning(cols.owner_id, cols.connector_name, cols.connector_id)
            .execution_options(synchronize_session=False)
        )
        return list(starmap(PrimaryCandidate, result.tuples().all()))

    @db_operation("repair_missing_primaries")
    async def repair_missing_primaries(
        self, *, user_id: str, dry_run: bool = False
    ) -> list[PrimaryVacancyRepair]:
        """Fill every vacant primary slot this user's live mappings have left.

        **One election policy, one promotion path.** The winner is the
        highest-confidence, lowest-id live mapping of the pair — the total
        order every per-pair heal applies — chosen set-based so the repair is
        one query rather than one per vacancy. Promotion goes through
        :meth:`ensure_primaries` in ``fill`` mode, which keeps the peer guard:
        a pair that gained a primary between the two statements is left
        alone.

        Stale-id cache rows are never candidates, here or in any other
        election: a pair whose only live rows are stale-id ones has no live
        identity and is not a vacancy.
        """
        cols = self.columns
        vacancies = (
            select(
                cols.id,
                cols.owner_id,
                cols.connector_name,
                cols.connector_id,
                cols.confidence,
            )
            .where(
                cols.user_id == user_id,
                cols.live,
                cols.electable,
                self._no_live_primary(),
            )
            .distinct(cols.owner_id, cols.connector_name)
            .order_by(
                cols.owner_id,
                cols.connector_name,
                cols.confidence.desc(),
                cols.id.asc(),
            )
        )
        result = await self.session.execute(vacancies)
        elected = [
            PrimaryVacancyRepair(
                owner_id=owner_id,
                connector_name=connector_name,
                connector_id=connector_id,
                mapping_id=mapping_id,
                confidence=confidence,
            )
            for mapping_id, owner_id, connector_name, connector_id, confidence in result.tuples()
        ]
        if dry_run or not elected:
            return elected

        promoted = await self.ensure_primaries(
            [
                PrimaryCandidate(row.owner_id, row.connector_name, row.connector_id)
                for row in elected
            ],
            mode="fill",
        )
        if len(promoted) != len(elected):
            logger.warning(
                "Primary repair promoted fewer pairs than it elected",
                user_id=user_id,
                elected=len(elected),
                promoted=len(promoted),
            )
        return elected

    # ── events ───────────────────────────────────────────────────────

    def _resolution_recorder(self) -> ResolutionRecorderProtocol:
        """The identity write seam bound to this repository's transaction."""
        return ResolutionRecorder(self.session)

    async def record_assertion(self, assertion: MappingAssertion) -> None:
        """Emit the events an ``assert_mappings`` batch just earned.

        Every accept path funnels through an assert, so this is the only
        place that emits ``accepted``: no accept can reach the database
        without its event. A *touched* mapping earns nothing — re-encountering
        an unchanged decision is freshness, not a decision.

        Event data comes from ``assertion.written`` — the repository's own
        normalised rows — never from the caller's raw input: a batch may span
        users, and a row dropped by the manual-override filter never reaches
        ``prepared`` and so never reaches here either. ``entity_kind`` is the
        shape's; ``score`` is read out of the stored evidence.
        """
        if not assertion.written:
            return
        by_user: dict[str, list[ResolutionDecision]] = {}
        successor_owners: dict[UUID, tuple[str, str]] = {}
        for row in assertion.written:
            successor_owners[row.id] = (row.user_id, row.connector_name)
            by_user.setdefault(row.user_id, []).append(
                ResolutionDecision(
                    # A user-pinned mapping is an override, not an automatic
                    # accept, and the log has to be able to tell them apart.
                    event_type="manual_override"
                    if row.origin == "manual_override"
                    else "accepted",
                    entity_kind=self.shape.entity_kind,
                    connector_name=row.connector_name,
                    connector_track_id=row.connector_id,
                    track_id=row.owner_id,
                    resulting_mapping_id=row.id,
                    confidence=row.confidence,
                    score=final_score_of(row.confidence_evidence),
                    zone="accept",
                )
            )

        recorder = self._resolution_recorder()
        for user_id, decisions in by_user.items():
            _ = await recorder.record(decisions, user_id=user_id)
        await self._record_supersession_edges(
            recorder,
            assertion.superseded,
            successor_owners,
            reason=assertion.reason,
        )

    async def _record_supersession_edges(
        self,
        recorder: ResolutionRecorderProtocol,
        edges: Mapping[UUID, UUID],
        successor_owners: Mapping[UUID, tuple[str, str]],
        *,
        reason: SupersessionReason,
    ) -> None:
        """Emit one ``superseded`` event per edge; the seam groups by owner.

        A successor absent from ``successor_owners`` was dropped upstream and
        is skipped — there is no live row left to describe an event about.
        ``reason`` comes from the assertion that produced these edges: a
        relink asserts with ``manual``, and an event saying ``rematch`` about
        a row whose column says ``manual`` is the log contradicting the data
        it exists to explain. ``entity_kind`` is the shape's, as on every
        other event this repository emits.
        """
        supersession_edges = [
            SupersessionEdge(
                predecessor_id=predecessor,
                successor_id=successor,
                user_id=owner[0],
                connector_name=owner[1],
                entity_kind=self.shape.entity_kind,
            )
            for predecessor, successor in edges.items()
            if (owner := successor_owners.get(successor)) is not None
        ]
        if not supersession_edges:
            return
        _ = await recorder.record_supersessions(supersession_edges, reason=reason)


__all__ = [
    "STALE_ID_METHODS",
    "AssertedMappingRow",
    "MappingAssertion",
    "MappingColumns",
    "MappingRepository",
    "MappingShape",
]
