"""Integration tests for ScheduleRepository (v0.8.2 scheduling).

Exercises real SQL behavior the unit suite can't reach: the per-user CRUD
filters, the cross-tenant scheduler poll, the optimistic claim under genuine
concurrency (two committed sessions on separate connections), and the FK
cascade/SET-NULL semantics that protect run history.

The CHECK constraints live only in migration 025 — ``init_db()`` builds the
schema via ``metadata.create_all`` and does NOT exercise them — so these tests
verify behavior the ORM models DO carry (FKs, partial-unique indexes), not the
CHECKs (those are verified by a real migration run before tagging).
"""

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from uuid import uuid4, uuid7

from attrs import evolve
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.schedule import Schedule
from src.domain.exceptions import (
    NotFoundError,
    ScheduleAlreadyExistsError,
    ScheduleInvariantError,
)
from src.infrastructure.persistence.database.db_models import (
    DBWorkflow,
    DBWorkflowRun,
)
from src.infrastructure.persistence.repositories.schedule import ScheduleRepository

pytestmark = pytest.mark.integration

# A whole-minute UTC instant used as next_run_at; the claim guard compares
# next_run_at for exact equality, so whole-minute precision matters.
_DUE = datetime(2026, 1, 1, 6, 30, tzinfo=UTC)
_LATER = datetime(2026, 1, 2, 6, 30, tzinfo=UTC)


def _sync_schedule(
    user_id: str,
    *,
    next_run_at: datetime = _DUE,
    sync_target: str = "lastfm:plays",
    status: str = "enabled",
    started_at: datetime | None = None,
) -> Schedule:
    """Build a sync-target schedule (no workflow row needed)."""
    return Schedule(
        user_id=user_id,
        sync_target=sync_target,
        hour=6,
        minute=30,
        next_run_at=next_run_at,
        status=status,  # pyright: ignore[reportArgumentType]  # narrowed literal in tests
        started_at=started_at,
    )


class TestScheduleCrud:
    """Per-user CRUD — every method filters by user_id (no RLS on this table)."""

    async def test_create_and_get_for_target(self, db_session: AsyncSession) -> None:
        repo = ScheduleRepository(db_session)
        created = await repo.create(_sync_schedule("user-a"))

        assert created.target_type == "sync"
        found = await repo.get_for_target(user_id="user-a", sync_target="lastfm:plays")
        assert found is not None
        assert found.id == created.id

    async def test_duplicate_target_raises(self, db_session: AsyncSession) -> None:
        repo = ScheduleRepository(db_session)
        await repo.create(_sync_schedule("user-a"))
        with pytest.raises(ScheduleAlreadyExistsError):
            await repo.create(_sync_schedule("user-a"))

    async def test_same_target_different_user_allowed(
        self, db_session: AsyncSession
    ) -> None:
        repo = ScheduleRepository(db_session)
        await repo.create(_sync_schedule("user-a"))
        # Partial-unique is (user_id, sync_target) — another user is fine.
        other = await repo.create(_sync_schedule("user-b"))
        assert other.user_id == "user-b"

    async def test_get_by_id_is_user_scoped(self, db_session: AsyncSession) -> None:
        repo = ScheduleRepository(db_session)
        created = await repo.create(_sync_schedule("user-a"))

        assert await repo.get_by_id_for_user(created.id, user_id="user-a") is not None
        # Wrong owner → None (route maps to 404 without leaking existence).
        assert await repo.get_by_id_for_user(created.id, user_id="user-b") is None

    async def test_update_cross_user_raises_not_found(
        self, db_session: AsyncSession
    ) -> None:
        repo = ScheduleRepository(db_session)
        created = await repo.create(_sync_schedule("user-a"))
        disabled = Schedule(
            user_id="user-a",
            sync_target="lastfm:plays",
            hour=6,
            minute=30,
            next_run_at=_DUE,
            status="disabled",
            id=created.id,
        )
        with pytest.raises(NotFoundError):
            await repo.update_schedule(disabled, user_id="user-b")

    async def test_update_preserves_scheduler_owned_columns(
        self, db_session: AsyncSession
    ) -> None:
        # A CRUD edit must NOT clobber the claim/run-bookkeeping columns — those
        # belong solely to the guarded mark_schedule_* path. Regression for the
        # claim-corruption race (a cadence edit landing mid-dispatch could clear
        # started_at and resurrect a claimed schedule).
        repo = ScheduleRepository(db_session)
        created = await repo.create(_sync_schedule("u1", started_at=None))
        # Scheduler claims the row — a dispatch is now in flight (started_at set).
        assert await repo.mark_schedule_started(
            created.id, expected_next_run_at=_DUE, now=_DUE
        )
        # A concurrent edit carries STALE bookkeeping (started_at=None, bogus counts).
        stale = Schedule(
            user_id="u1",
            sync_target="lastfm:plays",
            hour=7,
            minute=0,
            next_run_at=_LATER,
            id=created.id,
            started_at=None,
            run_count=99,
            consecutive_failures=42,
        )
        await repo.update_schedule(stale, user_id="u1")

        refreshed = await repo.get_by_id_for_user(created.id, user_id="u1")
        assert refreshed is not None
        # User-owned cadence fields applied …
        assert refreshed.hour == 7
        assert refreshed.next_run_at == _LATER
        # … but scheduler-owned columns are untouched: the claim survives and the
        # counters are not rolled back to the stale entity's values.
        assert refreshed.started_at is not None
        assert refreshed.run_count == 0
        assert refreshed.consecutive_failures == 0

    async def test_delete_is_user_scoped(self, db_session: AsyncSession) -> None:
        repo = ScheduleRepository(db_session)
        created = await repo.create(_sync_schedule("user-a"))

        assert await repo.delete_for_user(created.id, user_id="user-b") is False
        assert await repo.delete_for_user(created.id, user_id="user-a") is True
        assert await repo.get_by_id_for_user(created.id, user_id="user-a") is None

    async def test_list_for_user_scoped(self, db_session: AsyncSession) -> None:
        repo = ScheduleRepository(db_session)
        await repo.create(_sync_schedule("user-a", sync_target="lastfm:plays"))
        await repo.create(_sync_schedule("user-a", sync_target="spotify:likes"))
        await repo.create(_sync_schedule("user-b", sync_target="lastfm:plays"))

        a_rows = await repo.list_for_user(user_id="user-a")
        assert {s.sync_target for s in a_rows} == {"lastfm:plays", "spotify:likes"}
        assert all(s.user_id == "user-a" for s in a_rows)


class TestFindDueSchedules:
    """Cross-tenant poll — reads every user's due, enabled, unclaimed rows."""

    async def test_excludes_disabled_and_future(self, db_session: AsyncSession) -> None:
        repo = ScheduleRepository(db_session)
        await repo.create(_sync_schedule("u1", sync_target="lastfm:plays"))
        await repo.create(
            _sync_schedule("u1", sync_target="spotify:likes", status="disabled")
        )
        await repo.create(
            _sync_schedule("u1", sync_target="lastfm:likes", next_run_at=_LATER)
        )

        due = await repo.find_due_schedules(_DUE)
        assert {s.sync_target for s in due} == {"lastfm:plays"}

    async def test_spans_users(self, db_session: AsyncSession) -> None:
        repo = ScheduleRepository(db_session)
        await repo.create(_sync_schedule("u1"))
        await repo.create(_sync_schedule("u2"))

        due = await repo.find_due_schedules(_DUE)
        assert {s.user_id for s in due} >= {"u1", "u2"}

    async def test_excludes_already_claimed(self, db_session: AsyncSession) -> None:
        repo = ScheduleRepository(db_session)
        await repo.create(_sync_schedule("u1", started_at=_DUE))
        due = await repo.find_due_schedules(_DUE)
        assert all(s.user_id != "u1" for s in due)


class TestMarkTransitions:
    """Claim / complete / fail / reaper transitions."""

    async def test_completed_advances_resets_releases(
        self, db_session: AsyncSession
    ) -> None:
        repo = ScheduleRepository(db_session)
        created = await repo.create(_sync_schedule("u1", started_at=_DUE))
        run_id = uuid7()

        won = await repo.mark_schedule_completed(
            created.id,
            next_run_at=_LATER,
            last_run_at=_DUE,
            last_run_status="completed",
            last_run_id=run_id,
        )
        assert won is True

        refreshed = await repo.get_by_id_for_user(created.id, user_id="u1")
        assert refreshed is not None
        assert refreshed.started_at is None
        assert refreshed.next_run_at == _LATER
        assert refreshed.consecutive_failures == 0
        assert refreshed.run_count == 1
        assert refreshed.last_run_id == run_id

    async def test_failed_increments_and_releases(
        self, db_session: AsyncSession
    ) -> None:
        repo = ScheduleRepository(db_session)
        created = await repo.create(_sync_schedule("u1", started_at=_DUE))

        won = await repo.mark_schedule_failed(
            created.id,
            next_run_at=_LATER,
            last_run_at=_DUE,
            last_error="boom",
        )
        assert won is True

        refreshed = await repo.get_by_id_for_user(created.id, user_id="u1")
        assert refreshed is not None
        assert refreshed.started_at is None
        assert refreshed.consecutive_failures == 1
        assert refreshed.last_error == "boom"
        assert refreshed.run_count == 0  # failures don't count as runs

    async def test_completed_noops_when_not_claimed(
        self, db_session: AsyncSession
    ) -> None:
        repo = ScheduleRepository(db_session)
        # started_at IS NULL → the guard makes the terminal write a no-op,
        # so a reaped-then-late-completing run can't resurrect state.
        created = await repo.create(_sync_schedule("u1", started_at=None))
        won = await repo.mark_schedule_completed(
            created.id, next_run_at=_LATER, last_run_at=_DUE, last_run_status="ok"
        )
        assert won is False

    async def test_skipped_advances_without_counting_a_run(
        self, db_session: AsyncSession
    ) -> None:
        repo = ScheduleRepository(db_session)
        created = await repo.create(
            _sync_schedule("u1", started_at=_DUE),
        )

        won = await repo.mark_schedule_skipped(
            created.id,
            next_run_at=_LATER,
            last_run_at=_DUE,
            last_run_status="skipped_already_running",
        )
        assert won is True

        refreshed = await repo.get_by_id_for_user(created.id, user_id="u1")
        assert refreshed is not None
        assert refreshed.started_at is None  # claim released
        assert refreshed.next_run_at == _LATER  # advanced
        assert refreshed.run_count == 0  # NOT counted as a run
        assert refreshed.consecutive_failures == 0  # NOT a failure
        assert refreshed.last_run_status == "skipped_already_running"

    async def test_skipped_reset_failures_clears_streak(
        self, db_session: AsyncSession
    ) -> None:
        # After failures, a skipped_already_running fire (workflow is demonstrably
        # healthy) should clear the streak so the banner doesn't linger (#6).
        repo = ScheduleRepository(db_session)
        created = await repo.create(_sync_schedule("u1", started_at=_DUE))
        await repo.mark_schedule_failed(
            created.id, next_run_at=_DUE, last_run_at=_DUE, last_error="boom"
        )
        # Re-claim for the next fire, then skip-with-reset.
        assert await repo.mark_schedule_started(
            created.id, expected_next_run_at=_DUE, now=_DUE
        )
        won = await repo.mark_schedule_skipped(
            created.id,
            next_run_at=_LATER,
            last_run_at=_DUE,
            last_run_status="skipped_already_running",
            reset_failures=True,
        )
        assert won is True

        refreshed = await repo.get_by_id_for_user(created.id, user_id="u1")
        assert refreshed is not None
        assert refreshed.consecutive_failures == 0  # streak cleared
        assert refreshed.last_error is None

    async def test_skipped_without_reset_preserves_streak(
        self, db_session: AsyncSession
    ) -> None:
        # A plain skip (missed window / reaped) must NOT touch the streak.
        repo = ScheduleRepository(db_session)
        created = await repo.create(_sync_schedule("u1", started_at=_DUE))
        await repo.mark_schedule_failed(
            created.id, next_run_at=_DUE, last_run_at=_DUE, last_error="boom"
        )
        assert await repo.mark_schedule_started(
            created.id, expected_next_run_at=_DUE, now=_DUE
        )
        await repo.mark_schedule_skipped(
            created.id, next_run_at=_LATER, last_run_at=_DUE, last_run_status="reaped"
        )

        refreshed = await repo.get_by_id_for_user(created.id, user_id="u1")
        assert refreshed is not None
        assert refreshed.consecutive_failures == 1  # preserved

    async def test_disabled_releases_and_stops_polling(
        self, db_session: AsyncSession
    ) -> None:
        # An orphaned-target schedule is disabled, releasing the claim, so the due
        # poll no longer returns it (#10).
        repo = ScheduleRepository(db_session)
        created = await repo.create(_sync_schedule("u1", started_at=_DUE))

        won = await repo.mark_schedule_disabled(created.id, last_error="gone")
        assert won is True

        refreshed = await repo.get_by_id_for_user(created.id, user_id="u1")
        assert refreshed is not None
        assert refreshed.status == "disabled"
        assert refreshed.started_at is None  # claim released
        assert refreshed.last_error == "gone"
        # Dropped from the due poll.
        due = await repo.find_due_schedules(_LATER)
        assert created.id not in {s.id for s in due}

    async def test_get_by_id_cross_tenant_sees_mid_dispatch_cadence_edit(
        self, db_session: AsyncSession
    ) -> None:
        # The scheduler's terminal write recomputes next_run_at from a FRESH read
        # so a cadence edited mid-dispatch isn't clobbered (#1). get_by_id is the
        # cross-tenant (no user_id) read backing that recompute: after a claim, an
        # owner cadence edit must be visible through it (the claim stays held —
        # update_schedule never clears started_at).
        repo = ScheduleRepository(db_session)
        created = await repo.create(_sync_schedule("u1", started_at=None))
        assert await repo.mark_schedule_started(
            created.id, expected_next_run_at=_DUE, now=_DUE
        )

        # Owner edits the cadence (new hour + recomputed next_run_at) while claimed.
        edit = evolve(created, hour=9, next_run_at=_LATER)
        await repo.update_schedule(edit, user_id="u1")

        fresh = await repo.get_by_id(created.id)  # cross-tenant, no user filter
        assert fresh.id == created.id
        assert fresh.hour == 9  # the new cadence, not the captured one
        assert fresh.next_run_at == _LATER
        assert fresh.started_at == _DUE  # claim still held across the edit

    async def test_list_stuck_started(self, db_session: AsyncSession) -> None:
        repo = ScheduleRepository(db_session)
        # Claimed an hour before "now" with a 30-min timeout → stuck.
        claimed_at = datetime(2026, 1, 1, 5, 0, tzinfo=UTC)
        now = datetime(2026, 1, 1, 6, 0, tzinfo=UTC)
        stuck = await repo.create(_sync_schedule("u1", started_at=claimed_at))
        await repo.create(_sync_schedule("u2", started_at=None))  # not claimed

        rows = await repo.list_stuck_started(1800, now=now)
        assert [s.id for s in rows] == [stuck.id]


class TestConcurrentClaim:
    """The load-bearing optimistic-claim guard under real concurrency."""

    @pytest.fixture
    async def two_db_sessions(
        self, _test_engine
    ) -> AsyncGenerator[tuple[AsyncSession, AsyncSession]]:
        """Two committed sessions on SEPARATE connections + TEST_%% cleanup.

        The claim race can't use the savepoint-isolated ``db_session`` — that
        keeps writes in one uncommitted transaction invisible to a second
        connection. Here both sessions commit real rows so the guarded UPDATE
        races at the database; a ``finally`` deletes the TEST_-namespaced rows
        the savepoint can't roll back.
        """
        conn1 = await _test_engine.connect()
        conn2 = await _test_engine.connect()
        s1 = AsyncSession(bind=conn1, expire_on_commit=False, autoflush=False)
        s2 = AsyncSession(bind=conn2, expire_on_commit=False, autoflush=False)
        try:
            yield s1, s2
        finally:
            await s1.rollback()
            await s2.rollback()
            await s1.close()
            await s2.close()
            async with _test_engine.begin() as cleanup:
                await cleanup.execute(
                    text("DELETE FROM schedules WHERE user_id LIKE 'TEST_%'")
                )
            await conn1.close()
            await conn2.close()

    async def test_exactly_one_claimer_wins(
        self, two_db_sessions: tuple[AsyncSession, AsyncSession]
    ) -> None:
        s1, s2 = two_db_sessions
        user = f"TEST_{uuid4().hex[:8]}"

        # Create + commit on s1 so both connections see the due row.
        repo1 = ScheduleRepository(s1)
        created = await repo1.create(_sync_schedule(user))
        await s1.commit()

        repo2 = ScheduleRepository(s2)

        async def claim(repo: ScheduleRepository, session: AsyncSession) -> bool:
            won = await repo.mark_schedule_started(
                created.id, expected_next_run_at=_DUE, now=_DUE
            )
            await session.commit()
            return won

        results = await asyncio.gather(claim(repo1, s1), claim(repo2, s2))
        assert sum(results) == 1, "exactly one concurrent claimer must win"

    async def test_poll_lock_excludes_concurrent_tick(
        self, two_db_sessions: tuple[AsyncSession, AsyncSession]
    ) -> None:
        # The per-tick transaction-level poll lock: while one tick's transaction
        # holds it, a concurrent tick can't — so only one replica scans per tick.
        s1, s2 = two_db_sessions
        repo1, repo2 = ScheduleRepository(s1), ScheduleRepository(s2)

        assert await repo1.try_acquire_poll_lock() is True
        # s1 hasn't committed, so the xact lock is still held → s2 is excluded.
        assert await repo2.try_acquire_poll_lock() is False

        # Ending s1's transaction releases the lock; s2 can then win.
        await s1.commit()
        assert await repo2.try_acquire_poll_lock() is True
        await s2.commit()


class TestForeignKeyBehavior:
    """FK cascade (workflow→schedule) and SET-NULL (schedule→run history)."""

    async def _make_workflow(self, session: AsyncSession) -> DBWorkflow:
        wf = DBWorkflow(name="test-wf", definition={"nodes": []})
        session.add(wf)
        await session.flush()
        return wf

    async def test_workflow_delete_cascades_schedule(
        self, db_session: AsyncSession
    ) -> None:
        wf = await self._make_workflow(db_session)
        repo = ScheduleRepository(db_session)
        sched = await repo.create(
            Schedule(user_id="u1", workflow_id=wf.id, hour=6, next_run_at=_DUE)
        )

        await db_session.execute(
            text("DELETE FROM workflows WHERE id = :id"), {"id": wf.id}
        )
        # Schedule went with it (ON DELETE CASCADE on workflow_id).
        assert await repo.get_by_id_for_user(sched.id, user_id="u1") is None

    async def test_schedule_delete_preserves_run_history(
        self, db_session: AsyncSession
    ) -> None:
        wf = await self._make_workflow(db_session)
        repo = ScheduleRepository(db_session)
        sched = await repo.create(
            Schedule(user_id="u1", workflow_id=wf.id, hour=6, next_run_at=_DUE)
        )

        run = DBWorkflowRun(
            workflow_id=wf.id,
            definition_snapshot={"nodes": []},
            triggered_by_schedule_id=sched.id,
        )
        db_session.add(run)
        await db_session.flush()

        run_id = run.id
        assert await repo.delete_for_user(sched.id, user_id="u1") is True

        # Read the raw column straight from the DB — the ORM identity map holds
        # a stale `run` (expire_on_commit=False) whose cached FK value would mask
        # the DB's ON DELETE SET NULL. The run row itself must survive.
        row = (
            await db_session.execute(
                text(
                    "SELECT id, triggered_by_schedule_id FROM workflow_runs "
                    "WHERE id = :id"
                ),
                {"id": run_id},
            )
        ).one()
        assert row.id == run_id  # run preserved
        assert row.triggered_by_schedule_id is None  # back-pointer orphaned


class TestCheckConstraintMapping:
    """CHECK constraints live only in migration 025 (not __table_args__), so the
    create_all-built test DB lacks them. Apply the constraint the entity does NOT
    guard (status) and verify the repo maps the resulting IntegrityError to
    ScheduleInvariantError (→ 422) instead of leaking a raw 500."""

    async def test_check_violation_maps_to_invariant_error(
        self, db_session: AsyncSession
    ) -> None:
        # Mirror migration 025's status CHECK onto the create_all schema.
        await db_session.execute(
            text(
                "ALTER TABLE schedules ADD CONSTRAINT ck_schedules_valid_status "
                "CHECK (status IN ('enabled', 'disabled'))"
            )
        )
        repo = ScheduleRepository(db_session)
        # status="paused" passes the domain entity (it doesn't range-check status)
        # but violates the DB CHECK — the exact defense-in-depth gap this maps.
        bad = evolve(_sync_schedule("u1"), status="paused")  # pyright: ignore[reportArgumentType]
        with pytest.raises(ScheduleInvariantError):
            await repo.create(bad)
