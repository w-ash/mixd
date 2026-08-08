"""The bulk statement budget reaches PostgreSQL and reverts on COMMIT.

Deliberately not the shared ``db_session`` fixture: that one hands out a
session already inside a transaction and a savepoint, where the ``after_begin``
hook is skipped by design. These tests build sessions from the production
factory on a production engine, so both the connect-time 30s default and the
per-transaction override are the real ones.
"""

from collections.abc import AsyncIterator

import pytest
from pytest_asyncio import fixture as async_fixture
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config.constants import BusinessLimits
from src.infrastructure.persistence.database.db_connection import (
    create_db_engine,
    create_session_factory,
)
from src.infrastructure.persistence.database.user_context import (
    statement_timeout_context,
)

# PostgreSQL normalises interval GUCs on read: '300s' comes back as '5min'.
_BULK_TIMEOUT_AS_SHOWN = "5min"
_CONNECTION_DEFAULT_AS_SHOWN = "30s"


@async_fixture
async def bulk_sessions(
    postgres_url: str,
    _init_test_schema: None,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Production session factory on a production engine.

    ``create_db_engine`` installs the pool ``connect`` listener that applies
    the 30s session default, which is the baseline these tests assert against.
    """
    engine = create_db_engine(postgres_url)
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


async def _show_statement_timeout(session: AsyncSession) -> str:
    result = await session.execute(text("SHOW statement_timeout"))
    return result.scalar_one()


@pytest.mark.integration
class TestBulkStatementTimeoutBudget:
    """An opt-in budget that PgBouncer-safe transaction-local config can carry."""

    async def test_connection_default_applies_without_the_context(
        self, bulk_sessions: async_sessionmaker[AsyncSession]
    ) -> None:
        async with bulk_sessions() as session:
            assert await _show_statement_timeout(session) == (
                _CONNECTION_DEFAULT_AS_SHOWN
            )

    async def test_context_raises_the_budget_for_transactions_inside_it(
        self, bulk_sessions: async_sessionmaker[AsyncSession]
    ) -> None:
        with statement_timeout_context(BusinessLimits.BULK_IMPORT_STATEMENT_TIMEOUT):
            async with bulk_sessions() as session:
                assert await _show_statement_timeout(session) == (
                    _BULK_TIMEOUT_AS_SHOWN
                )

    async def test_budget_is_reapplied_after_each_commit(
        self, bulk_sessions: async_sessionmaker[AsyncSession]
    ) -> None:
        """Chunked importers commit repeatedly; every new transaction re-raises."""
        with statement_timeout_context(BusinessLimits.BULK_IMPORT_STATEMENT_TIMEOUT):
            async with bulk_sessions() as session:
                assert await _show_statement_timeout(session) == (
                    _BULK_TIMEOUT_AS_SHOWN
                )
                await session.commit()
                assert await _show_statement_timeout(session) == (
                    _BULK_TIMEOUT_AS_SHOWN
                )

    async def test_budget_reverts_to_the_connection_default_after_the_context(
        self, bulk_sessions: async_sessionmaker[AsyncSession]
    ) -> None:
        """Transaction-local means the pooled connection is not left mutated."""
        async with bulk_sessions() as session:
            with statement_timeout_context(
                BusinessLimits.BULK_IMPORT_STATEMENT_TIMEOUT
            ):
                assert await _show_statement_timeout(session) == (
                    _BULK_TIMEOUT_AS_SHOWN
                )
                await session.commit()

            assert await _show_statement_timeout(session) == (
                _CONNECTION_DEFAULT_AS_SHOWN
            )
