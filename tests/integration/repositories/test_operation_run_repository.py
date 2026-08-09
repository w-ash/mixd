"""Integration tests for OperationRunRepository (v0.7.7).

Covers the audit-log contract:
- create + read-back preserves all fields including JSONB shapes
- ``update_status`` sets terminal fields and merges ``counts`` (JSONB ``||``)
- ``append_issues`` is additive across calls and batches (JSONB array concat)
- ``get_by_id_for_user`` returns None for non-owner (no existence leak)
- ``list_for_user`` is user-scoped, sorts started_at DESC with id tiebreaker,
  and paginates correctly via the ``(after_started_at, after_id)`` keyset.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid7

from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.persistence.repositories.operation_run import (
    OperationRunRepository,
)
from tests.fixtures import make_operation_run


class TestCreateAndRead:
    """Insert + read-back round-trip."""

    async def test_create_preserves_all_fields(self, db_session: AsyncSession) -> None:
        repo = OperationRunRepository(db_session)
        run = make_operation_run(
            operation_type="import_spotify_playlists",
            counts={"playlists": 3, "tracks": 200},
            issues=[{"track_id": "abc", "reason": "region_locked"}],
            request_params={"connector_name": "spotify", "sync_direction": "pull"},
        )

        created = await repo.create(run)

        assert created.id == run.id
        assert created.operation_type == "import_spotify_playlists"
        assert created.status == "running"
        assert created.counts == {"playlists": 3, "tracks": 200}
        assert created.issues == [{"track_id": "abc", "reason": "region_locked"}]
        # request_params round-trips so retry can re-invoke from the row alone.
        assert created.request_params == {
            "connector_name": "spotify",
            "sync_direction": "pull",
        }

    async def test_create_default_jsonb_shapes(self, db_session: AsyncSession) -> None:
        """No-arg run gets empty dict for counts and empty list for issues."""
        repo = OperationRunRepository(db_session)
        run = make_operation_run()

        created = await repo.create(run)

        assert created.counts == {}
        assert created.issues == []
        assert created.request_params == {}

    async def test_initiated_by_round_trips(self, db_session: AsyncSession) -> None:
        """An assistant-initiated run persists and reads back its attribution."""
        repo = OperationRunRepository(db_session)
        run = make_operation_run(initiated_by="assistant")

        created = await repo.create(run)
        fetched = await repo.get_by_id_for_user(created.id, user_id=created.user_id)

        assert created.initiated_by == "assistant"
        assert fetched is not None
        assert fetched.initiated_by == "assistant"

    async def test_initiated_by_defaults_to_manual(
        self, db_session: AsyncSession
    ) -> None:
        """A run created without an explicit attribution reads back as manual."""
        repo = OperationRunRepository(db_session)
        run = make_operation_run()

        created = await repo.create(run)

        assert created.initiated_by == "manual"

    async def test_get_by_id_for_user_returns_match(
        self, db_session: AsyncSession
    ) -> None:
        repo = OperationRunRepository(db_session)
        run = make_operation_run(user_id="alice")
        await repo.create(run)

        fetched = await repo.get_by_id_for_user(run.id, user_id="alice")

        assert fetched is not None
        assert fetched.id == run.id

    async def test_get_by_id_for_user_returns_none_for_non_owner(
        self, db_session: AsyncSession
    ) -> None:
        """Critical: 404 for non-owner avoids row-existence leak."""
        repo = OperationRunRepository(db_session)
        run = make_operation_run(user_id="alice")
        await repo.create(run)

        fetched = await repo.get_by_id_for_user(run.id, user_id="bob")

        assert fetched is None

    async def test_get_by_id_for_user_returns_none_for_missing(
        self, db_session: AsyncSession
    ) -> None:
        repo = OperationRunRepository(db_session)
        fetched = await repo.get_by_id_for_user(uuid7(), user_id="alice")
        assert fetched is None


class TestUpdateStatus:
    """Terminal-state finalization."""

    async def test_update_status_sets_terminal_fields(
        self, db_session: AsyncSession
    ) -> None:
        repo = OperationRunRepository(db_session)
        run = make_operation_run(user_id="alice", status="running")
        await repo.create(run)

        ended = datetime.now(UTC)
        await repo.update_status(
            run.id,
            user_id="alice",
            status="complete",
            ended_at=ended,
            counts={"tracks_imported": 42},
        )

        updated = await repo.get_by_id_for_user(run.id, user_id="alice")
        assert updated is not None
        assert updated.status == "complete"
        assert updated.ended_at is not None
        assert updated.counts == {"tracks_imported": 42}

    async def test_update_status_merges_counts(self, db_session: AsyncSession) -> None:
        """JSONB || preserves prior counts and overwrites only colliding keys."""
        repo = OperationRunRepository(db_session)
        run = make_operation_run(
            user_id="alice",
            counts={"playlists_seen": 5, "tracks_imported": 10},
        )
        await repo.create(run)

        await repo.update_status(
            run.id,
            user_id="alice",
            status="complete",
            ended_at=datetime.now(UTC),
            counts={"tracks_imported": 50, "issues_count": 2},
        )

        updated = await repo.get_by_id_for_user(run.id, user_id="alice")
        assert updated is not None
        # playlists_seen retained; tracks_imported overwritten; issues_count added.
        assert updated.counts == {
            "playlists_seen": 5,
            "tracks_imported": 50,
            "issues_count": 2,
        }


class TestAppendIssues:
    """Issue accumulation via JSONB array concat."""

    async def test_append_issues_is_additive(self, db_session: AsyncSession) -> None:
        repo = OperationRunRepository(db_session)
        run = make_operation_run(user_id="alice")
        await repo.create(run)

        await repo.append_issues(
            run.id,
            user_id="alice",
            issues=[{"track_id": "abc", "reason": "no_match"}],
        )
        await repo.append_issues(
            run.id,
            user_id="alice",
            issues=[{"track_id": "def", "reason": "rate_limit"}],
        )

        updated = await repo.get_by_id_for_user(run.id, user_id="alice")
        assert updated is not None
        assert updated.issues == [
            {"track_id": "abc", "reason": "no_match"},
            {"track_id": "def", "reason": "rate_limit"},
        ]

    async def test_batch_append_preserves_order(self, db_session: AsyncSession) -> None:
        # A partial import appends its headline + one issue per unresolved track
        # in a single UPDATE; the row must read back in the order it was sent.
        repo = OperationRunRepository(db_session)
        run = make_operation_run(user_id="alice")
        await repo.create(run)
        batch: list[dict[str, object]] = [
            {"message": "2 of 100 plays failed import"},
            {"track": "A - B", "reason": "track_resolution_failed"},
            {"track": "C - D", "reason": "track_resolution_failed"},
        ]

        await repo.append_issues(run.id, user_id="alice", issues=batch)

        updated = await repo.get_by_id_for_user(run.id, user_id="alice")
        assert updated is not None
        assert updated.issues == batch

    async def test_empty_batch_is_a_no_op(self, db_session: AsyncSession) -> None:
        repo = OperationRunRepository(db_session)
        run = make_operation_run(user_id="alice")
        await repo.create(run)

        await repo.append_issues(run.id, user_id="alice", issues=[])

        updated = await repo.get_by_id_for_user(run.id, user_id="alice")
        assert updated is not None
        assert updated.issues == []


class TestListForUser:
    """User scoping, ordering, and keyset pagination."""

    async def test_user_scoped(self, db_session: AsyncSession) -> None:
        repo = OperationRunRepository(db_session)
        await repo.create(make_operation_run(user_id="alice"))
        await repo.create(make_operation_run(user_id="bob"))

        rows, _ = await repo.list_for_user(user_id="alice")

        assert len(rows) == 1
        assert all(r.user_id == "alice" for r in rows)

    async def test_orders_newest_first(self, db_session: AsyncSession) -> None:
        repo = OperationRunRepository(db_session)
        now = datetime.now(UTC)
        # Older first, newer last in insertion order, but list returns newest first.
        await repo.create(
            make_operation_run(user_id="alice", started_at=now - timedelta(hours=2))
        )
        middle = await repo.create(
            make_operation_run(user_id="alice", started_at=now - timedelta(hours=1))
        )
        await repo.create(make_operation_run(user_id="alice", started_at=now))

        rows, _ = await repo.list_for_user(user_id="alice")

        assert len(rows) == 3
        assert rows[0].started_at >= rows[1].started_at >= rows[2].started_at
        assert rows[1].id == middle.id

    async def test_filter_by_operation_types(self, db_session: AsyncSession) -> None:
        repo = OperationRunRepository(db_session)
        await repo.create(
            make_operation_run(
                user_id="alice", operation_type="import_spotify_playlists"
            )
        )
        await repo.create(
            make_operation_run(user_id="alice", operation_type="workflow_run")
        )

        rows, _ = await repo.list_for_user(
            user_id="alice", operation_types=["import_spotify_playlists"]
        )

        assert len(rows) == 1
        assert rows[0].operation_type == "import_spotify_playlists"

    async def test_pagination_returns_next_key_when_more(
        self, db_session: AsyncSession
    ) -> None:
        repo = OperationRunRepository(db_session)
        now = datetime.now(UTC)
        for i in range(5):
            await repo.create(
                make_operation_run(
                    user_id="alice", started_at=now - timedelta(minutes=i)
                )
            )

        page1, next_key = await repo.list_for_user(user_id="alice", limit=2)

        assert len(page1) == 2
        assert next_key is not None
        # next_key carries the (started_at, id) of the LAST row in this page.
        assert next_key == (page1[-1].started_at, page1[-1].id)

    async def test_pagination_no_next_key_on_last_page(
        self, db_session: AsyncSession
    ) -> None:
        repo = OperationRunRepository(db_session)
        for _ in range(2):
            await repo.create(make_operation_run(user_id="alice"))

        rows, next_key = await repo.list_for_user(user_id="alice", limit=10)

        assert len(rows) == 2
        assert next_key is None

    async def test_pagination_continues_with_keyset(
        self, db_session: AsyncSession
    ) -> None:
        """Page 2 starts after page 1's last row — no overlap, no skip."""
        repo = OperationRunRepository(db_session)
        now = datetime.now(UTC)
        runs = []
        for i in range(5):
            run = await repo.create(
                make_operation_run(
                    user_id="alice", started_at=now - timedelta(minutes=i)
                )
            )
            runs.append(run)

        page1, next_key = await repo.list_for_user(user_id="alice", limit=2)
        assert next_key is not None
        page2, _ = await repo.list_for_user(
            user_id="alice",
            limit=2,
            after_started_at=next_key[0],
            after_id=next_key[1],
        )

        page1_ids = {r.id for r in page1}
        page2_ids = {r.id for r in page2}
        assert page1_ids.isdisjoint(page2_ids)
        assert len(page1) == 2
        assert len(page2) == 2


class TestListRunningStartedBefore:
    """The reaper/busy-gate query: cross-user, running-only, cutoff-bounded."""

    async def test_returns_only_running_rows_older_than_cutoff(
        self, db_session: AsyncSession
    ) -> None:
        repo = OperationRunRepository(db_session)
        now = datetime.now(UTC)
        stale = await repo.create(
            make_operation_run(user_id="alice", started_at=now - timedelta(hours=13))
        )
        # Fresh running row: still inside the bound, must survive.
        await repo.create(
            make_operation_run(user_id="alice", started_at=now - timedelta(minutes=5))
        )
        # Terminal row older than the cutoff: not running, must survive.
        await repo.create(
            make_operation_run(
                user_id="alice",
                status="error",
                started_at=now - timedelta(hours=20),
                ended_at=now - timedelta(hours=19),
            )
        )

        rows = await repo.list_running_started_before(
            now - timedelta(hours=12), limit=10
        )

        assert [r.id for r in rows] == [stale.id]

    async def test_crosses_user_boundaries(self, db_session: AsyncSession) -> None:
        # Deliberately NOT user-scoped: the reaper and the deploy busy gate ask
        # about the whole process, and rows carry their own user_id for the
        # per-row finalize writes.
        repo = OperationRunRepository(db_session)
        now = datetime.now(UTC)
        for user_id in ("alice", "bob"):
            await repo.create(
                make_operation_run(
                    user_id=user_id, started_at=now - timedelta(hours=13)
                )
            )

        rows = await repo.list_running_started_before(now, limit=10)

        assert {r.user_id for r in rows} == {"alice", "bob"}

    async def test_cutoff_now_counts_every_running_row(
        self, db_session: AsyncSession
    ) -> None:
        # The busy gate's degenerate case: a run started seconds ago counts.
        repo = OperationRunRepository(db_session)
        await repo.create(make_operation_run(user_id="alice"))

        rows = await repo.list_running_started_before(datetime.now(UTC), limit=10)

        assert len(rows) == 1

    async def test_oldest_first_within_limit(self, db_session: AsyncSession) -> None:
        repo = OperationRunRepository(db_session)
        now = datetime.now(UTC)
        oldest = await repo.create(
            make_operation_run(user_id="alice", started_at=now - timedelta(hours=30))
        )
        await repo.create(
            make_operation_run(user_id="alice", started_at=now - timedelta(hours=13))
        )

        rows = await repo.list_running_started_before(
            now - timedelta(hours=12), limit=1
        )

        assert [r.id for r in rows] == [oldest.id]


class TestCountRunningStartedSince:
    """The busy gate's count: running-only, cross-user, age bound in SQL.

    A SQL ``count(*)`` with no batch cap — the fetch-then-filter it replaced
    was bounded by the reaper's ``limit``, so enough stale rows could truncate
    a live run out of the count and let a deploy land mid-import.
    """

    async def test_counts_only_running_rows_since_cutoff(
        self, db_session: AsyncSession
    ) -> None:
        repo = OperationRunRepository(db_session)
        now = datetime.now(UTC)
        # Live cross-user runs: both counted.
        await repo.create(
            make_operation_run(user_id="alice", started_at=now - timedelta(minutes=30))
        )
        await repo.create(
            make_operation_run(user_id="bob", started_at=now - timedelta(minutes=5))
        )
        # Phantom older than the cutoff: excluded by the SQL predicate.
        await repo.create(
            make_operation_run(user_id="alice", started_at=now - timedelta(hours=13))
        )
        # Fresh but terminal: not running, excluded.
        await repo.create(
            make_operation_run(
                user_id="alice",
                status="complete",
                started_at=now - timedelta(minutes=1),
                ended_at=now,
            )
        )

        count = await repo.count_running_started_since(now - timedelta(hours=12))

        assert count == 2

    async def test_row_started_exactly_at_cutoff_counts(
        self, db_session: AsyncSession
    ) -> None:
        # The bound is inclusive (>=): a run exactly REAP_AGE_BOUND old is
        # still the gate's problem, not yet the reaper's.
        repo = OperationRunRepository(db_session)
        cutoff = datetime.now(UTC) - timedelta(hours=12)
        await repo.create(make_operation_run(user_id="alice", started_at=cutoff))

        assert await repo.count_running_started_since(cutoff) == 1

    async def test_empty_table_counts_zero(self, db_session: AsyncSession) -> None:
        repo = OperationRunRepository(db_session)

        assert (
            await repo.count_running_started_since(
                datetime.now(UTC) - timedelta(hours=12)
            )
            == 0
        )
