"""Regression tests for BaseRepository's concurrent-session retry.

``BaseRepository.execute_select_one`` runs under the shared tenacity policy in
``repositories/_shared/retry_policies.py``. These tests pin the three behaviours
that policy owns:

- SQLAlchemy's "concurrent operations are not permitted" guard gets exactly one
  retry, and the retried attempt really re-runs the query against the database;
- a second concurrent-session failure is terminal, and the database exception —
  not tenacity's ``RetryError`` — reaches the caller;
- any other error fails fast on the first attempt.

The injected failure is not a hand-written string: ``_concurrent_session_error``
provokes SQLAlchemy's own guard and captures what it raises, so a wording change
in a future SQLAlchemy release fails these tests rather than silently disabling
the retry.
"""

from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.orm.session import SessionTransactionState

from src.domain.entities.track import Artist, Track
from src.infrastructure.persistence.database.db_models import DBTrack
from src.infrastructure.persistence.repositories._shared.retry_policies import (
    CONCURRENT_SESSION_MARKER,
)
from src.infrastructure.persistence.repositories.base_repo import BaseRepository
from src.infrastructure.persistence.repositories.mappers import BaseModelMapper


def _concurrent_session_error() -> InvalidRequestError:
    """Capture SQLAlchemy's own concurrent-session error, verbatim.

    ``PROVISIONING_CONNECTION`` is the state a session sits in while one
    coroutine is opening its connection; a second coroutine touching the session
    then trips the guard. In production that needs a race — here the state is set
    directly so the exception (message, type, and error code) is the real one and
    the test is deterministic. The engine URL is never connected to.
    """
    engine = create_engine("postgresql+psycopg://unused:unused@127.0.0.1:1/unused")
    try:
        session = Session(bind=engine)
        transaction = session.begin()
        transaction._state = SessionTransactionState.PROVISIONING_CONNECTION
        with pytest.raises(InvalidRequestError) as excinfo:
            _ = session.execute(select(1))
        return excinfo.value
    finally:
        engine.dispose()


class _PlainMapper(BaseModelMapper[DBTrack, Track]):
    """Minimal mapper — ``execute_select_one`` returns DB models, not domain ones."""

    @staticmethod
    async def to_domain(db_model: DBTrack) -> Track:
        return Track(id=db_model.id, title=db_model.title, artists=[Artist(name="t")])

    @staticmethod
    def to_db(domain_model: Track) -> DBTrack:
        return DBTrack(title=domain_model.title)

    @staticmethod
    def get_default_relationships() -> list[str]:
        return []


@pytest.fixture
def track_repo(db_session: AsyncSession) -> BaseRepository[DBTrack, Track]:
    """BaseRepository wired straight to DBTrack — base-class behaviour under test."""
    return BaseRepository[DBTrack, Track](
        session=db_session,
        model_class=DBTrack,
        mapper=_PlainMapper(),
    )


async def _insert_track(db_session: AsyncSession, title: str) -> None:
    """Insert a track row inside the test's savepoint."""
    track = DBTrack(title=title, artists={"names": ["test"]}, duration_ms=200000)
    track.mappings = []
    track.metrics = []
    track.likes = []
    track.plays = []
    track.playlist_tracks = []
    db_session.add(track)
    await db_session.flush()


class TestConcurrentSessionRetry:
    """The retryable case: SQLAlchemy's concurrent-session guard."""

    async def test_retry_reruns_the_query_against_the_database(
        self,
        db_session: AsyncSession,
        track_repo: BaseRepository[DBTrack, Track],
    ):
        title = f"TEST_retry_{uuid4().hex[:8]}"
        await _insert_track(db_session, title)

        attempts: list[int] = []
        real_query = BaseRepository._execute_query_one

        async def flaky(self: BaseRepository[DBTrack, Track], stmt: object):
            attempts.append(len(attempts) + 1)
            if len(attempts) == 1:
                raise _concurrent_session_error()
            return await real_query(self, stmt)

        with patch.object(BaseRepository, "_execute_query_one", flaky):
            result = await track_repo.execute_select_one(
                select(DBTrack).where(DBTrack.title == title)
            )

        assert attempts == [1, 2]
        assert result is not None
        assert result.title == title

    async def test_second_failure_is_terminal_and_reraises_the_db_error(
        self,
        track_repo: BaseRepository[DBTrack, Track],
    ):
        attempts: list[int] = []

        async def always_concurrent(
            self: BaseRepository[DBTrack, Track], stmt: object
        ) -> None:
            attempts.append(len(attempts) + 1)
            raise _concurrent_session_error()

        with (
            patch.object(BaseRepository, "_execute_query_one", always_concurrent),
            pytest.raises(InvalidRequestError) as excinfo,
        ):
            _ = await track_repo.execute_select_one(select(DBTrack))

        # Two attempts total, and the caller sees the database error itself —
        # ``reraise=True`` keeps tenacity's RetryError out of the call stack.
        assert attempts == [1, 2]
        assert CONCURRENT_SESSION_MARKER in str(excinfo.value)


class TestNonRetryableErrors:
    """Everything else fails fast — the policy must not retry blindly."""

    async def test_other_invalid_request_error_is_not_retried(
        self,
        track_repo: BaseRepository[DBTrack, Track],
    ):
        attempts: list[int] = []

        async def closed_transaction(
            self: BaseRepository[DBTrack, Track], stmt: object
        ) -> None:
            attempts.append(len(attempts) + 1)
            raise InvalidRequestError("This transaction is closed")

        with (
            patch.object(BaseRepository, "_execute_query_one", closed_transaction),
            pytest.raises(InvalidRequestError, match="transaction is closed"),
        ):
            _ = await track_repo.execute_select_one(select(DBTrack))

        assert attempts == [1]


class TestMarkerTracksSQLAlchemy:
    """The policy matches on a message SQLAlchemy owns; pin the two together."""

    def test_policy_marker_matches_sqlalchemy_guard_message(self):
        assert CONCURRENT_SESSION_MARKER in str(_concurrent_session_error())
