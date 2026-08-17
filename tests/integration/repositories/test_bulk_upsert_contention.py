"""``bulk_upsert``'s per-row fallback must not answer lock contention.

Contention (55P03) is not a bad row: splitting the batch queues on the same
contended index once per row, swallows each failure, and returns a silently
partial result. These tests pin the split: contention is re-raised for the
savepoint-owning caller to retry wholesale; anything else still takes the
per-row fallback that salvages good rows.

Builds its own engine (two genuinely separate connections; the shared
``db_session`` fixture is one savepoint-wrapped session) — the
``test_mapping_supersession_concurrency.py`` pattern. Marked ``slow``.
"""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from pytest_asyncio import fixture as async_fixture
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.domain.entities.track import Artist, Track
from src.domain.repositories.errors import LOCK_NOT_AVAILABLE, postgres_sqlstate
from src.infrastructure.persistence.database.db_connection import create_session_factory
from src.infrastructure.persistence.database.db_models import DBTrack
from src.infrastructure.persistence.repositories.base_repo import BaseRepository
from src.infrastructure.persistence.repositories.mappers import BaseModelMapper

pytestmark = pytest.mark.slow

_USER = "bulk-contention-probe"


class _PlainMapper(BaseModelMapper[DBTrack, Track]):
    """Minimal mapper — base-class behaviour is under test, not track mapping."""

    @staticmethod
    async def to_domain(db_model: DBTrack) -> Track:
        return Track(id=db_model.id, title=db_model.title, artists=[Artist(name="t")])

    @staticmethod
    def to_db(domain_model: Track) -> DBTrack:
        return DBTrack(title=domain_model.title)

    @staticmethod
    def get_default_relationships() -> list[str]:
        return []


def _entity(isrc: str, title: str) -> dict[str, object]:
    return {
        "user_id": _USER,
        "title": title,
        "artists": {"names": ["Probe"]},
        "isrc": isrc,
    }


@async_fixture
async def concurrent_sessions(
    postgres_url: str,
    _init_test_schema: None,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A factory whose sessions really do run in separate transactions."""
    engine = create_async_engine(postgres_url)
    try:
        yield create_session_factory(engine)
    finally:
        # A fallback-path test may leave committed-by-flush rows behind if a
        # future regression stops rolling back; sweep the probe user either way.
        async with engine.begin() as conn:
            _ = await conn.execute(
                sa.text("DELETE FROM tracks WHERE user_id = :uid"), {"uid": _USER}
            )
        await engine.dispose()


async def _repo_session(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[AsyncSession, BaseRepository[DBTrack, Track]]:
    """A session with the test's deterministic lock budget, plus its repo.

    This engine skips the production pool listener that sets ``lock_timeout``,
    so the test sets its own — 200ms instead of production's 10s, making the
    55P03 deterministic and fast rather than racing real timers.
    """
    session = factory()
    _ = await session.execute(sa.text("SET lock_timeout = '200ms'"))
    repo = BaseRepository[DBTrack, Track](
        session=session, model_class=DBTrack, mapper=_PlainMapper()
    )
    return session, repo


class TestTransientContentionReraises:
    """The incident shape: an uncommitted competitor on the identity key."""

    async def test_contention_is_reraised_not_split_per_row(
        self,
        concurrent_sessions: async_sessionmaker[AsyncSession],
        capsys: pytest.CaptureFixture[str],
    ):
        """One 55P03, surfaced — not eaten by the fallback and returned as a
        partial count indistinguishable from success."""
        contended_isrc = f"TEST{uuid4().hex[:8].upper()}"

        async with concurrent_sessions() as blocker:
            # The competing writer: an uncommitted row claiming the key.
            blocker.add(
                DBTrack(
                    user_id=_USER,
                    title="held uncommitted",
                    artists={"names": ["Blocker"]},
                    isrc=contended_isrc,
                )
            )
            await blocker.flush()

            session, repo = await _repo_session(concurrent_sessions)
            try:
                with pytest.raises(DBAPIError) as raised:
                    _ = await repo.bulk_upsert(
                        [
                            _entity(contended_isrc, "contended"),
                            _entity(f"TEST{uuid4().hex[:8].upper()}", "innocent"),
                        ],
                        ["user_id", "isrc"],
                        False,
                    )
            finally:
                await session.rollback()
                await session.close()

        assert postgres_sqlstate(raised.value) == LOCK_NOT_AVAILABLE
        out = capsys.readouterr().out
        assert "transient contention" in out
        assert LOCK_NOT_AVAILABLE in out, "the WARNING has to name the SQLSTATE"
        assert "falling back to individual upserts" not in out

    async def test_nothing_is_written_when_contention_is_reraised(
        self,
        concurrent_sessions: async_sessionmaker[AsyncSession],
    ):
        """All-or-nothing: the batch rolled back and no per-row salvage ran,
        so the caller can retry the identical batch without double-counting."""
        contended_isrc = f"TEST{uuid4().hex[:8].upper()}"
        innocent_isrc = f"TEST{uuid4().hex[:8].upper()}"

        async with concurrent_sessions() as blocker:
            blocker.add(
                DBTrack(
                    user_id=_USER,
                    title="held uncommitted",
                    artists={"names": ["Blocker"]},
                    isrc=contended_isrc,
                )
            )
            await blocker.flush()

            session, repo = await _repo_session(concurrent_sessions)
            try:
                with pytest.raises(DBAPIError):
                    _ = await repo.bulk_upsert(
                        [
                            _entity(contended_isrc, "contended"),
                            _entity(innocent_isrc, "innocent"),
                        ],
                        ["user_id", "isrc"],
                        False,
                    )
                written = (
                    await session.execute(
                        sa
                        .select(sa.func.count())
                        .select_from(DBTrack)
                        .where(DBTrack.isrc == innocent_isrc)
                    )
                ).scalar_one()
                assert written == 0
            finally:
                await session.rollback()
                await session.close()


class TestNonTransientStillFallsBack:
    """The classification must narrow the fallback, not remove it."""

    async def test_a_bad_row_is_still_isolated_per_row(
        self,
        concurrent_sessions: async_sessionmaker[AsyncSession],
    ):
        """A constraint violation is about the rows, so the per-row loop still
        salvages the good ones — the behaviour the fallback has always owned."""
        good_isrc = f"TEST{uuid4().hex[:8].upper()}"
        poison = _entity(f"TEST{uuid4().hex[:8].upper()}", "poison")
        poison["title"] = None  # NOT NULL violation: a genuinely bad row

        session, repo = await _repo_session(concurrent_sessions)
        try:
            count = await repo.bulk_upsert(
                [poison, _entity(good_isrc, "good")],
                ["user_id", "isrc"],
                False,
            )
            assert count == 1, "the good row must be salvaged"
            written = (
                await session.execute(
                    sa
                    .select(sa.func.count())
                    .select_from(DBTrack)
                    .where(DBTrack.isrc == good_isrc)
                )
            ).scalar_one()
            assert written == 1
        finally:
            await session.rollback()
            await session.close()
