"""Integration tests for the startup operation-run reaper against real rows.

Pins the SQL half the unit tests mock: the staleness condition selects only
over-age ``running`` rows (fresh ``running`` and terminal rows untouched),
the sweep crosses user boundaries in one tick, and the tick is idempotent —
on rows it already reaped and on an empty table.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.operation_run_reaper import (
    PROCESS_DIED_ERROR_MESSAGE,
    reap_dead_runs,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from src.infrastructure.persistence.repositories.operation_run import (
    OperationRunRepository,
)
from tests.fixtures import make_operation_run


def _stale_started_at() -> datetime:
    return datetime.now(UTC) - timedelta(hours=13)


class TestReaperTick:
    async def test_stale_reaped_fresh_and_terminal_untouched(
        self, db_session: AsyncSession
    ) -> None:
        repo = OperationRunRepository(db_session)
        stale = await repo.create(
            make_operation_run(user_id="alice", started_at=_stale_started_at())
        )
        fresh = await repo.create(
            make_operation_run(
                user_id="alice",
                started_at=datetime.now(UTC) - timedelta(minutes=5),
            )
        )
        finished = await repo.create(
            make_operation_run(
                user_id="alice",
                status="complete",
                started_at=datetime.now(UTC) - timedelta(hours=20),
                ended_at=datetime.now(UTC) - timedelta(hours=19),
            )
        )

        reaped = await reap_dead_runs(get_unit_of_work(db_session))

        assert reaped == 1
        stale_row = await repo.get_by_id_for_user(stale.id, user_id="alice")
        assert stale_row is not None
        assert stale_row.status == "error"
        assert stale_row.ended_at is not None
        assert stale_row.counts == {"error_message": PROCESS_DIED_ERROR_MESSAGE}
        assert len(stale_row.issues) == 1
        assert "process died" in str(stale_row.issues[0]["message"])

        fresh_row = await repo.get_by_id_for_user(fresh.id, user_id="alice")
        assert fresh_row is not None
        assert fresh_row.status == "running"
        assert fresh_row.issues == []

        finished_row = await repo.get_by_id_for_user(finished.id, user_id="alice")
        assert finished_row is not None
        assert finished_row.status == "complete"
        assert finished_row.issues == []

    async def test_reaps_across_users_in_one_tick(
        self, db_session: AsyncSession
    ) -> None:
        # The reaper answers a process-level question, so its query carries no
        # user predicate — both tenants' dead rows fall in one tick.
        repo = OperationRunRepository(db_session)
        for user_id in ("alice", "bob"):
            await repo.create(
                make_operation_run(user_id=user_id, started_at=_stale_started_at())
            )

        reaped = await reap_dead_runs(get_unit_of_work(db_session))

        assert reaped == 2

    async def test_tick_is_idempotent_on_already_reaped_rows(
        self, db_session: AsyncSession
    ) -> None:
        repo = OperationRunRepository(db_session)
        stale = await repo.create(
            make_operation_run(user_id="alice", started_at=_stale_started_at())
        )

        first = await reap_dead_runs(get_unit_of_work(db_session))
        second = await reap_dead_runs(get_unit_of_work(db_session))

        assert first == 1
        assert second == 0
        row = await repo.get_by_id_for_user(stale.id, user_id="alice")
        assert row is not None
        assert len(row.issues) == 1

    async def test_tick_on_empty_table_is_a_no_op(
        self, db_session: AsyncSession
    ) -> None:
        assert await reap_dead_runs(get_unit_of_work(db_session)) == 0
        assert await reap_dead_runs(get_unit_of_work(db_session)) == 0
