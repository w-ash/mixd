"""OperationRun repository — audit log for SSE-backed operations."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities.operation_run import OperationRun, OperationStatus
from src.domain.entities.shared import JsonDict
from src.infrastructure.persistence.database.db_models import DBOperationRun
from src.infrastructure.persistence.repositories.base_repo import (
    BaseRepository,
    SimpleMapperFactory,
)
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)

OperationRunMapper = SimpleMapperFactory.create(DBOperationRun, OperationRun)


class OperationRunRepository(BaseRepository[DBOperationRun, OperationRun]):
    """Repository for ``operation_runs`` audit rows.

    Keyset-paginated by ``(started_at, id)`` descending. The repository
    returns raw next-page-key tuples; the route layer encodes/decodes the
    opaque ``PageCursor`` (matches the track-listing pattern in
    ``track/core.py``). This keeps infrastructure free of application-
    layer imports.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(
            session=session,
            model_class=DBOperationRun,
            mapper=OperationRunMapper(),
        )

    @db_operation("create_operation_run")
    async def create(self, run: OperationRun) -> OperationRun:
        """Insert a new run row at operation kickoff."""
        db_row = DBOperationRun(
            id=run.id,
            user_id=run.user_id,
            operation_type=run.operation_type,
            started_at=run.started_at,
            ended_at=run.ended_at,
            status=run.status,
            counts=dict(run.counts),
            issues=list(run.issues),
        )
        self.session.add(db_row)
        await self.session.flush()
        return await OperationRunMapper.to_domain(db_row)

    @db_operation("update_operation_run_status")
    async def update_status(
        self,
        run_id: UUID,
        *,
        user_id: str,
        status: OperationStatus,
        ended_at: datetime | None,
        counts: JsonDict | None = None,
    ) -> None:
        """Set the terminal status, ended_at, and merge counts.

        Counts are merged at the SQL level via JSONB concat (``||``) so
        partial counts emitted during the run aren't clobbered by the
        terminal call.
        """
        values: dict[str, object] = {
            "status": status,
            "ended_at": ended_at,
        }
        if counts is not None:
            values["counts"] = self.model_class.counts.op("||")(counts)
        stmt = (
            update(self.model_class)
            .where(
                self.model_class.id == run_id,
                self.model_class.user_id == user_id,
            )
            .values(**values)
        )
        await self.session.execute(stmt)

    @db_operation("append_operation_run_issue")
    async def append_issue(
        self,
        run_id: UUID,
        *,
        user_id: str,
        issue: JsonDict,
    ) -> None:
        """Append one issue to the JSONB ``issues`` array."""
        stmt = (
            update(self.model_class)
            .where(
                self.model_class.id == run_id,
                self.model_class.user_id == user_id,
            )
            .values(issues=self.model_class.issues.op("||")([issue]))
        )
        await self.session.execute(stmt)

    @db_operation("get_operation_run_by_id")
    async def get_by_id_for_user(
        self, run_id: UUID, *, user_id: str
    ) -> OperationRun | None:
        """Return the run if it exists AND is owned by ``user_id``, else None.

        Returning ``None`` rather than raising ``NotFoundError`` lets the
        route layer answer 404 for both not-found and not-owner without
        leaking row existence (per the v0.7.7 plan).
        """
        stmt = select(self.model_class).where(
            self.model_class.id == run_id,
            self.model_class.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        db_row = result.scalar_one_or_none()
        if db_row is None:
            return None
        return await OperationRunMapper.to_domain(db_row)

    @db_operation("list_operation_runs_for_user")
    async def list_for_user(
        self,
        *,
        user_id: str,
        limit: int = 20,
        after_started_at: datetime | None = None,
        after_id: UUID | None = None,
        operation_types: Sequence[str] | None = None,
    ) -> tuple[list[OperationRun], tuple[datetime, UUID] | None]:
        """List runs newest-first, keyset-paginated by ``(started_at, id)``.

        Returns ``(rows, next_page_key)`` where ``next_page_key`` is None
        on the last page. Fetches ``limit + 1`` rows to detect "more".
        """
        stmt = select(self.model_class).where(self.model_class.user_id == user_id)

        if operation_types is not None:
            stmt = stmt.where(self.model_class.operation_type.in_(operation_types))

        if after_started_at is not None and after_id is not None:
            # Keyset paginate by (started_at, id) descending. The OR form is
            # equivalent to row-value comparison ((a, b) < (x, y)) and
            # generates the same index plan, but types cleanly without
            # coercing literals through ``tuple_()``.
            stmt = stmt.where(
                or_(
                    self.model_class.started_at < after_started_at,
                    and_(
                        self.model_class.started_at == after_started_at,
                        self.model_class.id < after_id,
                    ),
                )
            )

        stmt = stmt.order_by(
            self.model_class.started_at.desc(), self.model_class.id.desc()
        ).limit(limit + 1)

        result = await self.session.execute(stmt)
        db_rows = list(result.scalars().all())

        has_more = len(db_rows) > limit
        if has_more:
            db_rows = db_rows[:limit]

        runs = [await OperationRunMapper.to_domain(r) for r in db_rows]

        next_page_key: tuple[datetime, UUID] | None = None
        if has_more and runs:
            last = runs[-1]
            next_page_key = (last.started_at, last.id)
        return runs, next_page_key


__all__ = ["OperationRunRepository"]
