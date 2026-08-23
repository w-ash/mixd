"""Guard-session behavior of ``single_flight_token_refresh`` (unit).

Covers the pieces that don't need Postgres: the per-transaction
``set_config('lock_timeout', ...)`` derived from the caller's
``lock_timeout_seconds``, and the mapping of a Postgres lock-timeout
failure (SQLSTATE 55P03) into the typed ``TokenRefreshContendedError``.
The blocking/dedup semantics live in
``tests/integration/repositories/test_token_refresh_single_flight.py``.
"""

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.exc import DBAPIError

from src.infrastructure.persistence.repositories import token_refresh_lock
from src.infrastructure.persistence.repositories.token_refresh_lock import (
    TokenRefreshContendedError,
    single_flight_token_refresh,
)


class _FakeResult:
    def scalar_one_or_none(self) -> None:
        return None


class _FakeSession:
    """Records executed statements; optionally explodes at the advisory lock."""

    def __init__(self, fail_on_lock: Exception | None = None) -> None:
        self.executed: list[tuple[str, object]] = []
        self.fail_on_lock = fail_on_lock

    async def execute(self, statement: object, params: object = None) -> _FakeResult:
        sql = str(statement)
        self.executed.append((sql, params))
        if "pg_advisory_xact_lock" in sql and self.fail_on_lock is not None:
            raise self.fail_on_lock
        return _FakeResult()


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch):
    session = _FakeSession()

    @asynccontextmanager
    async def fake_get_session():
        yield session

    monkeypatch.setattr(token_refresh_lock, "get_session", fake_get_session)
    return session


def _lock_not_available_error() -> DBAPIError:
    class _LockNotAvailable(Exception):
        sqlstate = "55P03"

    return DBAPIError("SELECT pg_advisory_xact_lock", None, _LockNotAvailable())


class TestLockTimeoutConfiguration:
    async def test_set_config_applies_caller_lock_timeout_before_locking(
        self, fake_session: _FakeSession
    ) -> None:
        """The caller's timeout lands as a transaction-local lock_timeout in ms."""
        async with single_flight_token_refresh(
            "tidal", "u1", current_refresh_token="rt", lock_timeout_seconds=15.5
        ):
            pass

        set_config_index = next(
            i
            for i, (sql, params) in enumerate(fake_session.executed)
            if "set_config" in sql and "lock_timeout" in sql
        )
        _, params = fake_session.executed[set_config_index]
        assert isinstance(params, dict)
        assert params["timeout_ms"] == "15500"
        lock_index = next(
            i
            for i, (sql, _) in enumerate(fake_session.executed)
            if "pg_advisory_xact_lock" in sql
        )
        assert set_config_index < lock_index

    async def test_lock_timeout_maps_to_typed_contended_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SQLSTATE 55P03 at the advisory lock surfaces as the typed error."""
        session = _FakeSession(fail_on_lock=_lock_not_available_error())

        @asynccontextmanager
        async def fake_get_session():
            yield session

        monkeypatch.setattr(token_refresh_lock, "get_session", fake_get_session)

        with pytest.raises(TokenRefreshContendedError):
            async with single_flight_token_refresh(
                "tidal", "u1", current_refresh_token="rt", lock_timeout_seconds=1.0
            ):
                pass

    async def test_other_dbapi_errors_propagate_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-lock-timeout database failure is not misread as contention."""

        class _SomethingElse(Exception):
            sqlstate = "57014"  # query_canceled

        error = DBAPIError("SELECT pg_advisory_xact_lock", None, _SomethingElse())
        session = _FakeSession(fail_on_lock=error)

        @asynccontextmanager
        async def fake_get_session():
            yield session

        monkeypatch.setattr(token_refresh_lock, "get_session", fake_get_session)

        with pytest.raises(DBAPIError):
            async with single_flight_token_refresh(
                "tidal", "u1", current_refresh_token="rt", lock_timeout_seconds=1.0
            ):
                pass
