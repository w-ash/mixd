"""Integration tests for the chunk probe's round-trip accounting.

Pins the facts the v0.10.2.11 statement-consolidation work rests on: that the
probe counts real ``cursor.execute()`` calls, that transaction begin costs one,
and that a savepoint around real work costs two more. If a SQLAlchemy upgrade
changes any of that, this fails loudly rather than silently reshaping every
statement budget downstream.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from src.config.telemetry import current_probe, measure_chunk, operation_scope
from src.infrastructure.persistence.database.db_connection import (
    _statement_starts,
    create_session_factory,
)
from src.infrastructure.persistence.unit_of_work import DatabaseUnitOfWork


@pytest.fixture
def engine(_test_engine: AsyncEngine) -> AsyncEngine:
    """The shared test engine, for a session built by the production factory."""
    return _test_engine


class TestStatementProbe:
    async def test_counts_transaction_begin_and_the_statement(
        self, engine: AsyncEngine
    ):
        """A first statement costs two round trips: the RLS set_config, then itself.

        Uses a production session factory rather than ``db_session``: that
        fixture's session opens its own SAVEPOINT on first use, and whether the
        ``after_begin`` hook is registered there depends on whether an earlier
        test on the worker built a production factory — so its count is
        order-dependent. A production factory always registers the hook.
        """
        factory = create_session_factory(engine)
        async with factory() as session, measure_chunk() as probe:
            _ = await session.execute(text("SELECT 1"))

        assert probe.statements == 2
        assert probe.db_ns > 0
        assert probe.wall_ns >= probe.db_ns

    async def test_savepoint_adds_two_statements(self, db_session):
        uow = DatabaseUnitOfWork(db_session)
        _ = await db_session.execute(text("SELECT 1"))  # transaction already open

        async with measure_chunk() as probe:
            async with uow.savepoint():
                _ = await db_session.execute(text("SELECT 2"))

        assert probe.statements == 3  # SAVEPOINT + SELECT + RELEASE

    async def test_empty_savepoint_is_elided(self, db_session):
        """SQLAlchemy emits nothing for a savepoint that does no work."""
        uow = DatabaseUnitOfWork(db_session)
        _ = await db_session.execute(text("SELECT 1"))

        async with measure_chunk() as probe:
            async with uow.savepoint():
                pass

        assert probe.statements == 0

    async def test_attributes_statements_to_outermost_operation(self, db_session):
        _ = await db_session.execute(text("SELECT 1"))  # absorb transaction begin

        async with measure_chunk() as probe:
            with operation_scope("outer"):
                _ = await db_session.execute(text("SELECT 1"))
                with operation_scope("inner"):
                    _ = await db_session.execute(text("SELECT 2"))

        assert probe.by_operation["outer"][0] == 2
        assert "inner" not in probe.by_operation

    async def test_unscoped_statements_bucket_to_bare(self, db_session):
        _ = await db_session.execute(text("SELECT 1"))

        async with measure_chunk() as probe:
            _ = await db_session.execute(text("SELECT 1"))

        assert probe.by_operation["_bare"][0] == 1

    async def test_a_failed_statement_leaves_no_start_stamp(self, db_session):
        """``after_cursor_execute`` never fires for it; ``handle_error`` must clean up."""
        uow = DatabaseUnitOfWork(db_session)
        _ = await db_session.execute(text("SELECT 1"))

        with pytest.raises(DBAPIError):
            async with uow.savepoint():
                await db_session.execute(text("SELECT * FROM no_such_table"))

        assert _statement_starts == {}

    async def test_no_probe_outside_a_span(self, db_session):
        _ = await db_session.execute(text("SELECT 1"))

        assert current_probe() is None
