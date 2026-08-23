"""Single-flight token refresh under the Postgres advisory xact lock.

Deliberately not the shared ``db_session`` fixture: the guard opens its own
short sessions off the standalone ``get_session()`` path (the
``DatabaseTokenStorage`` seam — token refresh runs inside httpx2 auth flows,
outside any UoW), so these tests point the global engine at the test
container per test, mirroring ``api/conftest._test_db_env``. Rows commit for
real; each test uses a unique user_id and deletes its row on teardown.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
import os
from uuid import uuid4

from cryptography.fernet import Fernet
from pydantic import SecretStr
import pytest

from src.config import settings
from src.infrastructure.connectors._shared.token_storage import StoredToken
from src.infrastructure.persistence.database.db_connection import (
    reset_engine_cache,
)
from src.infrastructure.persistence.repositories.token_encryption import (
    _get_fernet,
)
from src.infrastructure.persistence.repositories.token_refresh_lock import (
    TokenRefreshContendedError,
    single_flight_token_refresh,
)
from src.infrastructure.persistence.repositories.token_storage import (
    DatabaseTokenStorage,
)

SERVICE = "tidal"
LOCK_TIMEOUT = 10.0

SEED_TOKEN: StoredToken = {
    "access_token": "at-0",
    "refresh_token": "rt-0",
    "token_type": "Bearer",
}

ROTATED_TOKEN: StoredToken = {
    "access_token": "at-1",
    "refresh_token": "rt-1",
    "token_type": "Bearer",
    "scope": "r_usr",
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
    return f"sf-user-{uuid4().hex[:12]}"


@pytest.fixture
async def seeded_token(standalone_db: None, user_id: str) -> AsyncIterator[None]:
    """Seed one stored token; delete the committed row on teardown."""
    storage = DatabaseTokenStorage()
    await storage.save_token(SERVICE, user_id, SEED_TOKEN)
    yield
    await storage.delete_token(SERVICE, user_id)


@pytest.fixture
def _enable_encryption(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Configure a real Fernet key so guard.save exercises encryption."""
    _get_fernet.cache_clear()
    monkeypatch.setattr(
        settings.security,
        "token_encryption_key",
        SecretStr(Fernet.generate_key().decode()),
    )
    yield
    _get_fernet.cache_clear()


class TestSingleFlightTokenRefresh:
    @pytest.mark.usefixtures("seeded_token")
    async def test_two_concurrent_entrants_one_post(self, user_id: str) -> None:
        """Exactly one refresh POST; the loser observes the rotated token."""
        post_count = 0
        loser_saw: list[str | None] = []

        async def entrant() -> None:
            nonlocal post_count
            async with single_flight_token_refresh(
                SERVICE,
                user_id,
                current_refresh_token="rt-0",
                lock_timeout_seconds=LOCK_TIMEOUT,
            ) as guard:
                if guard.rotated_token is not None:
                    loser_saw.append(guard.rotated_token.get("refresh_token"))
                    return
                post_count += 1
                # Hold the lock across the simulated refresh POST so the
                # second entrant is blocked mid-flight, not merely queued.
                await asyncio.sleep(0.2)
                await guard.save(ROTATED_TOKEN)

        async with asyncio.TaskGroup() as tg:
            _ = tg.create_task(entrant())
            _ = tg.create_task(entrant())

        assert post_count == 1
        assert loser_saw == ["rt-1"]

    @pytest.mark.usefixtures("seeded_token")
    async def test_exception_in_flight_releases_lock(self, user_id: str) -> None:
        """Rollback releases the lock; a later entrant proceeds unrotated."""

        class RefreshExploded(Exception):
            pass

        async def failing_flight() -> None:
            async with single_flight_token_refresh(
                SERVICE,
                user_id,
                current_refresh_token="rt-0",
                lock_timeout_seconds=LOCK_TIMEOUT,
            ) as guard:
                assert guard.rotated_token is None
                raise RefreshExploded

        with pytest.raises(RefreshExploded):
            await failing_flight()

        async with asyncio.timeout(5):
            async with single_flight_token_refresh(
                SERVICE,
                user_id,
                current_refresh_token="rt-0",
                lock_timeout_seconds=LOCK_TIMEOUT,
            ) as guard:
                # The failed flight saved nothing — this entrant still wins.
                assert guard.rotated_token is None
                await guard.save(ROTATED_TOKEN)

        loaded = await DatabaseTokenStorage().load_token(SERVICE, user_id)
        assert loaded is not None
        assert loaded.get("refresh_token") == "rt-1"

    @pytest.mark.usefixtures("_enable_encryption", "seeded_token")
    async def test_saved_token_round_trips_through_load_token(
        self, user_id: str
    ) -> None:
        """guard.save persists through the encryption seam; load_token decrypts."""
        async with single_flight_token_refresh(
            SERVICE,
            user_id,
            current_refresh_token="rt-0",
            lock_timeout_seconds=LOCK_TIMEOUT,
        ) as guard:
            assert guard.rotated_token is None
            await guard.save(ROTATED_TOKEN)

        loaded = await DatabaseTokenStorage().load_token(SERVICE, user_id)
        assert loaded is not None
        assert loaded.get("access_token") == "at-1"
        assert loaded.get("refresh_token") == "rt-1"
        assert loaded.get("scope") == "r_usr"

    @pytest.mark.usefixtures("seeded_token")
    async def test_stale_entrant_gets_rotated_token_without_posting(
        self, user_id: str
    ) -> None:
        """An entrant holding an already-rotated refresh token must skip its POST."""
        async with single_flight_token_refresh(
            SERVICE,
            user_id,
            current_refresh_token="rt-0",
            lock_timeout_seconds=LOCK_TIMEOUT,
        ) as guard:
            await guard.save(ROTATED_TOKEN)

        async with single_flight_token_refresh(
            SERVICE,
            user_id,
            current_refresh_token="rt-0",
            lock_timeout_seconds=LOCK_TIMEOUT,
        ) as guard:
            assert guard.rotated_token is not None
            assert guard.rotated_token.get("refresh_token") == "rt-1"

    @pytest.mark.usefixtures("seeded_token")
    async def test_non_rotating_winner_still_dedupes_the_loser(
        self, user_id: str
    ) -> None:
        """A refresh that does NOT rotate must still stop the loser's POST.

        The winner saves a token carrying the SAME refresh token — with only
        the refresh-token comparison the loser would re-POST that token (a
        replay to a rotating provider). The ``refreshed_at`` stamp written by
        ``guard.save`` marks the flight as completed either way.
        """
        non_rotating: StoredToken = {
            "access_token": "at-1",
            "refresh_token": "rt-0",  # unchanged — no rotation
            "token_type": "Bearer",
        }
        post_count = 0
        losses = 0

        async def entrant() -> None:
            nonlocal post_count, losses
            async with single_flight_token_refresh(
                SERVICE,
                user_id,
                current_refresh_token="rt-0",
                lock_timeout_seconds=LOCK_TIMEOUT,
            ) as guard:
                if guard.rotated_token is not None:
                    losses += 1
                    assert guard.rotated_token.get("access_token") == "at-1"
                    return
                post_count += 1
                await asyncio.sleep(0.2)  # hold the lock across the "POST"
                await guard.save(non_rotating)

        async with asyncio.TaskGroup() as tg:
            _ = tg.create_task(entrant())
            _ = tg.create_task(entrant())

        assert post_count == 1
        assert losses == 1

    @pytest.mark.usefixtures("seeded_token")
    async def test_slow_winner_times_out_waiter_with_typed_error(
        self, user_id: str
    ) -> None:
        """A waiter that outlives its lock timeout fails typed, not DBAPIError."""
        winner_holding = asyncio.Event()

        async def slow_winner() -> None:
            async with single_flight_token_refresh(
                SERVICE,
                user_id,
                current_refresh_token="rt-0",
                lock_timeout_seconds=LOCK_TIMEOUT,
            ) as guard:
                winner_holding.set()
                await asyncio.sleep(1.0)
                await guard.save(ROTATED_TOKEN)

        async def impatient_waiter() -> None:
            await winner_holding.wait()
            with pytest.raises(TokenRefreshContendedError):
                async with single_flight_token_refresh(
                    SERVICE,
                    user_id,
                    current_refresh_token="rt-0",
                    lock_timeout_seconds=0.2,
                ):
                    pass

        async with asyncio.TaskGroup() as tg:
            _ = tg.create_task(slow_winner())
            _ = tg.create_task(impatient_waiter())
