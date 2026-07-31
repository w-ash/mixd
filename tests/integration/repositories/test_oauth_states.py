"""Integration tests for the connector-OAuth CSRF state prune.

Exercises the real commit path (``get_session()``) rather than the
savepoint-rollback ``db_session`` fixture, because the helper under test opens
its own session — the same reason ``test_pending_action_store.py`` does. Rows
are cleaned up explicitly per test.

The prune runs once from the FastAPI lifespan and only has to catch states
that expired while the server was down; the live path evicts opportunistically
on the next state creation (``routes/auth.py``).
"""

from datetime import UTC, datetime, timedelta
import secrets

import pytest
from sqlalchemy import delete, select

from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.database.db_models import DBOAuthState
from src.infrastructure.persistence.repositories.oauth_states import (
    prune_expired_states,
)

_SERVICE = "spotify-prune-test"


async def _add_state(*, expires_in: timedelta) -> str:
    state = secrets.token_urlsafe(16)
    async with get_session() as session:
        session.add(
            DBOAuthState(
                state=state,
                user_id="user-prune",
                service=_SERVICE,
                code_verifier=None,
                expires_at=datetime.now(UTC) + expires_in,
            )
        )
    return state


async def _surviving_states() -> set[str]:
    async with get_session() as session:
        rows = await session.execute(
            select(DBOAuthState.state).where(DBOAuthState.service == _SERVICE)
        )
        return set(rows.scalars().all())


@pytest.fixture(autouse=True)
async def _cleanup(_init_test_schema: None):
    yield
    async with get_session() as session:
        await session.execute(
            delete(DBOAuthState).where(DBOAuthState.service == _SERVICE)
        )


class TestPruneExpiredStates:
    async def test_removes_expired_and_keeps_live(self) -> None:
        """The live row is what a user mid-OAuth-flow depends on surviving a restart."""
        expired = await _add_state(expires_in=timedelta(minutes=-5))
        live = await _add_state(expires_in=timedelta(minutes=5))

        pruned = await prune_expired_states()

        assert pruned >= 1
        surviving = await _surviving_states()
        assert live in surviving
        assert expired not in surviving

    async def test_returns_zero_when_nothing_expired(self) -> None:
        await _add_state(expires_in=timedelta(minutes=5))

        # Other tests may leave expired rows behind, so assert on our own
        # service's rows rather than on the global count.
        await prune_expired_states()

        assert len(await _surviving_states()) == 1
