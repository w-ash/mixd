"""Integration tests for OperationRunRepository (v0.7.7).

Covers the audit-log contract:
- create + read-back preserves all fields including JSONB shapes
- ``update_status`` sets terminal fields and merges ``counts`` (JSONB ``||``)
- ``append_issue`` is additive across calls (JSONB array concat)
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
        )

        created = await repo.create(run)

        assert created.id == run.id
        assert created.operation_type == "import_spotify_playlists"
        assert created.status == "running"
        assert created.counts == {"playlists": 3, "tracks": 200}
        assert created.issues == [{"track_id": "abc", "reason": "region_locked"}]

    async def test_create_default_jsonb_shapes(self, db_session: AsyncSession) -> None:
        """No-arg run gets empty dict for counts and empty list for issues."""
        repo = OperationRunRepository(db_session)
        run = make_operation_run()

        created = await repo.create(run)

        assert created.counts == {}
        assert created.issues == []

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


class TestAppendIssue:
    """Issue accumulation via JSONB array concat."""

    async def test_append_issue_is_additive(self, db_session: AsyncSession) -> None:
        repo = OperationRunRepository(db_session)
        run = make_operation_run(user_id="alice")
        await repo.create(run)

        await repo.append_issue(
            run.id,
            user_id="alice",
            issue={"track_id": "abc", "reason": "no_match"},
        )
        await repo.append_issue(
            run.id,
            user_id="alice",
            issue={"track_id": "def", "reason": "rate_limit"},
        )

        updated = await repo.get_by_id_for_user(run.id, user_id="alice")
        assert updated is not None
        assert updated.issues == [
            {"track_id": "abc", "reason": "no_match"},
            {"track_id": "def", "reason": "rate_limit"},
        ]


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
