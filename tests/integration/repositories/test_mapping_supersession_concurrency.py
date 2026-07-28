"""Two concurrent ``assert_mappings`` on one live key must not lose a write.

The shared ``db_session`` fixture cannot express this: it is one session inside
one savepoint, so two "concurrent" writers would share a transaction and the
race would never happen. This module builds a dedicated engine and two real
sessions instead — the pattern ``rls_sessions`` uses in
``tests/integration/connectors/lastfm/test_lastfm_checkpoint_rls.py`` (no RLS
role needed here, only genuine transaction isolation).

What is pinned: whichever writer commits second must either supersede the
other's row or be rejected and retried — never overwrite it and never leave two
live rows on the same key. The retry path exists because the branch where a
conflicting row is superseded mid-statement is undocumented PostgreSQL
internals (memo §10.7).

Marked ``slow``: real commits against a dedicated engine, plus its own cleanup.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pytest_asyncio import fixture as async_fixture
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.infrastructure.persistence.database.db_connection import create_session_factory
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBTrack,
    DBTrackMapping,
)
from src.infrastructure.persistence.repositories.track.connector import (
    TrackMappingRepository,
)

pytestmark = pytest.mark.slow

_USER = "concurrency-probe"


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
        # These sessions COMMIT, so nothing rolls their rows back for us.
        async with engine.begin() as conn:
            _ = await conn.execute(
                sa.text("DELETE FROM track_mappings WHERE user_id = :uid"),
                {"uid": _USER},
            )
            _ = await conn.execute(
                sa.text("DELETE FROM tracks WHERE user_id = :uid"), {"uid": _USER}
            )
        await engine.dispose()


async def _seed(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[UUID, UUID, UUID]:
    """Create two candidate tracks and the connector track they contend over."""
    uid = str(uuid4())[:8]
    async with factory() as session:
        first = DBTrack(user_id=_USER, title=f"A {uid}", artists={"names": ["A"]})
        second = DBTrack(user_id=_USER, title=f"B {uid}", artists={"names": ["B"]})
        ct = DBConnectorTrack(
            connector_name="spotify",
            connector_track_identifier=f"sp_conc_{uid}",
            title=f"CT {uid}",
            artists={"names": ["A"]},
            raw_metadata={},
            last_updated=datetime.now(UTC),
        )
        session.add_all([first, second, ct])
        await session.commit()
        return first.id, second.id, ct.id


def _row(track_id: UUID, ct_id: UUID, confidence: int) -> dict[str, object]:
    return {
        "user_id": _USER,
        "track_id": track_id,
        "connector_track_id": ct_id,
        "connector_name": "spotify",
        "match_method": "isrc",
        "confidence": confidence,
    }


async def _assert_and_commit(
    factory: async_sessionmaker[AsyncSession], row: dict[str, object]
) -> None:
    async with factory() as session:
        await TrackMappingRepository(session).assert_mappings([row])
        await session.commit()


async def test_concurrent_assertions_leave_exactly_one_live_row(
    concurrent_sessions: async_sessionmaker[AsyncSession],
):
    track_a, track_b, ct_id = await _seed(concurrent_sessions)

    async with asyncio.TaskGroup() as tg:
        _ = tg.create_task(
            _assert_and_commit(concurrent_sessions, _row(track_a, ct_id, 70))
        )
        _ = tg.create_task(
            _assert_and_commit(concurrent_sessions, _row(track_b, ct_id, 95))
        )

    async with concurrent_sessions() as session:
        rows = (
            (
                await session.execute(
                    sa
                    .select(DBTrackMapping)
                    .where(DBTrackMapping.connector_track_id == ct_id)
                    .execution_options(include_superseded=True)
                )
            )
            .scalars()
            .all()
        )

    live = [row for row in rows if row.superseded_at is None]
    superseded = [row for row in rows if row.superseded_at is not None]

    assert len(live) == 1
    # No lost update: both decisions are on record whichever order won, and the
    # loser is retired against the winner rather than overwritten.
    assert len(superseded) == 1
    assert {row.track_id for row in rows} == {track_a, track_b}
    assert superseded[0].supersession_reason == "rematch"
    assert superseded[0].superseded_by_id == live[0].id
