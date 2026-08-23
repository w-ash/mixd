"""Narrow ``update_extra_data`` writes on ``DatabaseTokenStorage``.

The blind load→mutate→``save_token`` shape rewrites every column, so a
cache write racing a refresh rotation could resurrect a dead refresh token
and kill the rotated grant. ``update_extra_data`` touches ONLY the
``extra_data`` column (plus ``account_name`` when explicitly asked), so
token columns written by a concurrent rotation always survive.

Same standalone-engine posture as ``test_token_refresh_single_flight``:
the storage opens its own short sessions off ``get_session()``, so these
tests point the global engine at the test container per test. Rows commit
for real; each test uses a unique user_id and deletes its row on teardown.
"""

import asyncio
from collections.abc import AsyncIterator
import os
from uuid import uuid4

import pytest

from src.infrastructure.connectors._shared.token_storage import StoredToken
from src.infrastructure.persistence.database.db_connection import (
    reset_engine_cache,
)
from src.infrastructure.persistence.repositories.token_refresh_lock import (
    single_flight_token_refresh,
)
from src.infrastructure.persistence.repositories.token_storage import (
    DatabaseTokenStorage,
)

SERVICE = "tidal"

SEED_TOKEN: StoredToken = {
    "access_token": "at-0",
    "refresh_token": "rt-0",
    "token_type": "Bearer",
    "extra_data": {"authorized_at": 1700000000},
}

ROTATED_TOKEN: StoredToken = {
    "access_token": "at-1",
    "refresh_token": "rt-1",
    "token_type": "Bearer",
}


@pytest.fixture
async def standalone_db(
    postgres_url: str, _init_test_schema: None
) -> AsyncIterator[None]:
    """Point the standalone ``get_session()`` path at the test container."""
    original = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = postgres_url
    reset_engine_cache()
    try:
        yield
    finally:
        reset_engine_cache()
        if original is not None:
            os.environ["DATABASE_URL"] = original


@pytest.fixture
def user_id() -> str:
    return f"ts-user-{uuid4().hex[:12]}"


@pytest.fixture
async def seeded_token(standalone_db: None, user_id: str) -> AsyncIterator[None]:
    """Seed one stored token; delete the committed row on teardown."""
    storage = DatabaseTokenStorage()
    await storage.save_token(SERVICE, user_id, dict(SEED_TOKEN))
    yield
    await storage.delete_token(SERVICE, user_id)


class TestUpdateExtraData:
    @pytest.mark.usefixtures("seeded_token")
    async def test_merges_updates_and_preserves_token_columns(
        self, user_id: str
    ) -> None:
        """The narrow update merges keys; every token column stays untouched."""
        storage = DatabaseTokenStorage()
        await storage.update_extra_data(SERVICE, user_id, {"favorites_count": 7})

        loaded = await storage.load_token(SERVICE, user_id)
        assert loaded is not None
        assert loaded.get("refresh_token") == "rt-0"
        assert loaded.get("access_token") == "at-0"
        extra = loaded.get("extra_data") or {}
        assert extra.get("favorites_count") == 7
        # Existing keys survive the merge — this is an update, not a replace.
        assert extra.get("authorized_at") == 1700000000

    @pytest.mark.usefixtures("standalone_db")
    async def test_noop_when_no_row_exists(self, user_id: str) -> None:
        """No stored token: the update writes nothing and creates nothing."""
        storage = DatabaseTokenStorage()
        await storage.update_extra_data(SERVICE, user_id, {"favorites_count": 7})
        assert await storage.load_token(SERVICE, user_id) is None

    @pytest.mark.usefixtures("seeded_token")
    async def test_account_name_updates_alongside_extra_data(
        self, user_id: str
    ) -> None:
        """The optional account_name write shares the same narrow UPDATE."""
        storage = DatabaseTokenStorage()
        await storage.update_extra_data(
            SERVICE, user_id, {"account_id": "acct-9"}, account_name="Wash"
        )

        loaded = await storage.load_token(SERVICE, user_id)
        assert loaded is not None
        assert loaded.get("account_name") == "Wash"
        assert (loaded.get("extra_data") or {}).get("account_id") == "acct-9"
        assert loaded.get("refresh_token") == "rt-0"

    @pytest.mark.usefixtures("seeded_token")
    async def test_concurrent_rotation_and_count_save_keeps_rotated_grant(
        self, user_id: str
    ) -> None:
        """A count save racing a refresh rotation never resurrects rt-0.

        The winner holds the single-flight lock across a simulated refresh
        POST while ``update_extra_data`` fires mid-flight — whatever the
        commit order, the rotated refresh token survives (the old blind
        upsert could write the stale pair back and kill the grant).
        """
        storage = DatabaseTokenStorage()

        async def rotating_flight() -> None:
            async with single_flight_token_refresh(
                SERVICE,
                user_id,
                current_refresh_token="rt-0",
                lock_timeout_seconds=10,
            ) as guard:
                assert guard.rotated_token is None
                await asyncio.sleep(0.2)  # hold the lock across the "POST"
                await guard.save(dict(ROTATED_TOKEN))

        async def count_save() -> None:
            await asyncio.sleep(0.05)  # land mid-flight
            await storage.update_extra_data(SERVICE, user_id, {"favorites_count": 42})

        async with asyncio.TaskGroup() as tg:
            _ = tg.create_task(rotating_flight())
            _ = tg.create_task(count_save())

        loaded = await storage.load_token(SERVICE, user_id)
        assert loaded is not None
        assert loaded.get("refresh_token") == "rt-1"
