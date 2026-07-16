"""Integration tests for the durable Postgres pending-action store (v0.9.5).

Exercises the real commit path (`get_session()`), not the savepoint-rollback
``db_session`` fixture — the production store opens its own sessions, and the
multi-machine guarantee under test *is* the cross-session/cross-instance
visibility of committed rows. Rows are cleaned up explicitly per test.

Covers the v0.9.5 contract: a proposal created via one store instance is
claimable via another (two Fly machines), a claim is single-use even under
concurrency, TTL expiry re-previews, and the owner check distinguishes
Forbidden from Expired.
"""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update

from src.application.chat.pending_actions import (
    PendingAction,
    PostgresPendingActionStore,
)
from src.domain.exceptions import ActionExpiredError, ForbiddenError
from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.database.db_models import DBPendingAction


@pytest.fixture
async def store(_init_test_schema: None):
    """A store on the test database, with full table cleanup afterwards."""
    yield PostgresPendingActionStore()
    async with get_session() as session:
        await session.execute(delete(DBPendingAction))


async def _create(store: PostgresPendingActionStore, user_id: str = "user-a"):
    return await store.create(
        user_id=user_id,
        tool_name="manage_tags",
        tool_input={"operation": "tag", "tag": "mood:chill"},
        description="Tag 1 track with mood:chill",
        details={"changes": ["tag 'mood:chill' on 1 track"]},
    )


async def _backdate(action: PendingAction, minutes: int) -> None:
    async with get_session() as session:
        await session.execute(
            update(DBPendingAction)
            .where(DBPendingAction.id == action.action_id)
            .values(created_at=action.created_at - timedelta(minutes=minutes))
        )


class TestCrossInstanceClaim:
    """The two-phase guarantee across machines: create on A, claim on B."""

    async def test_claim_on_second_instance_commits_once(
        self, store: PostgresPendingActionStore
    ) -> None:
        other_machine = PostgresPendingActionStore()
        action = await _create(store)

        claimed = await other_machine.claim(action.action_id, "user-a")

        assert claimed.action_id == action.action_id
        assert claimed.tool_name == "manage_tags"
        assert claimed.tool_input == {"operation": "tag", "tag": "mood:chill"}
        assert claimed.details == {"changes": ["tag 'mood:chill' on 1 track"]}

    async def test_second_claim_rejected(
        self, store: PostgresPendingActionStore
    ) -> None:
        action = await _create(store)
        await store.claim(action.action_id, "user-a")

        with pytest.raises(ActionExpiredError):
            await PostgresPendingActionStore().claim(action.action_id, "user-a")

    async def test_concurrent_claims_exactly_one_wins(
        self, store: PostgresPendingActionStore
    ) -> None:
        action = await _create(store)
        outcomes: list[str] = []

        async def try_claim() -> None:
            try:
                await PostgresPendingActionStore().claim(action.action_id, "user-a")
                outcomes.append("won")
            except ActionExpiredError:
                outcomes.append("expired")

        async with asyncio.TaskGroup() as tg:
            _ = tg.create_task(try_claim())
            _ = tg.create_task(try_claim())

        assert sorted(outcomes) == ["expired", "won"]


class TestTtlExpiry:
    async def test_expired_action_claims_as_expired(
        self, store: PostgresPendingActionStore
    ) -> None:
        action = await _create(store)
        await _backdate(action, minutes=10)

        with pytest.raises(ActionExpiredError):
            await store.claim(action.action_id, "user-a")

    async def test_create_evicts_expired_rows(
        self, store: PostgresPendingActionStore
    ) -> None:
        stale = await _create(store)
        await _backdate(stale, minutes=10)

        await _create(store)  # opportunistic eviction runs here

        async with get_session() as session:
            remaining = await session.scalar(
                select(DBPendingAction.id).where(DBPendingAction.id == stale.action_id)
            )
        assert remaining is None

    async def test_unknown_action_id_is_expired(
        self, store: PostgresPendingActionStore
    ) -> None:
        with pytest.raises(ActionExpiredError):
            await store.claim(uuid4(), "user-a")


class TestOwnerChecks:
    async def test_claim_by_other_user_forbidden_and_not_consumed(
        self, store: PostgresPendingActionStore
    ) -> None:
        action = await _create(store, user_id="user-a")

        with pytest.raises(ForbiddenError):
            await store.claim(action.action_id, "user-b")

        # The failed foreign claim must not consume the owner's action.
        claimed = await store.claim(action.action_id, "user-a")
        assert claimed.action_id == action.action_id

    async def test_cancel_by_other_user_forbidden(
        self, store: PostgresPendingActionStore
    ) -> None:
        action = await _create(store, user_id="user-a")

        with pytest.raises(ForbiddenError):
            await store.cancel(action.action_id, "user-b")

        claimed = await store.claim(action.action_id, "user-a")
        assert claimed.action_id == action.action_id


class TestCancel:
    async def test_cancel_removes_action(
        self, store: PostgresPendingActionStore
    ) -> None:
        action = await _create(store)
        await store.cancel(action.action_id, "user-a")

        with pytest.raises(ActionExpiredError):
            await store.claim(action.action_id, "user-a")

    async def test_cancel_is_idempotent(
        self, store: PostgresPendingActionStore
    ) -> None:
        action = await _create(store)
        await store.cancel(action.action_id, "user-a")
        await store.cancel(action.action_id, "user-a")  # no error


class TestJsonbRoundTrip:
    async def test_nested_payloads_survive(
        self, store: PostgresPendingActionStore
    ) -> None:
        details = {
            "mode": "update",
            "changes": ["renamed", {"field": "name", "from": "A", "to": "B"}],
            "task_count": 4,
            "nested": {"definition": {"tasks": [{"id": "t1", "params": None}]}},
        }
        action = await store.create(
            user_id="user-a",
            tool_name="save_workflow",
            tool_input={"workflow_id": "w1"},
            description="Update workflow",
            details=details,
        )

        claimed = await store.claim(action.action_id, "user-a")
        assert claimed.details == details
        assert claimed.tool_input == {"workflow_id": "w1"}
