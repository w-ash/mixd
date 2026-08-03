"""Integration tests for ``DatabaseUnitOfWork.savepoint``.

Pins the v0.10.2.2 transaction-semantics fix: a statement failing inside a
savepoint scope must leave the enclosing transaction usable, so
continue-on-error item loops (inward resolvers) survive one bad item instead
of cascading every later statement into ``InFailedSqlTransaction``.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from src.infrastructure.persistence.unit_of_work import DatabaseUnitOfWork


class TestSavepoint:
    async def test_failure_inside_savepoint_keeps_transaction_usable(self, db_session):
        uow = DatabaseUnitOfWork(db_session)

        with pytest.raises(DBAPIError):
            async with uow.savepoint():
                await db_session.execute(text("SELECT * FROM no_such_table"))

        # Without the savepoint this SELECT would raise InFailedSqlTransaction.
        value = (await db_session.execute(text("SELECT 1"))).scalar_one()
        assert value == 1

    async def test_successful_savepoint_preserves_writes(self, db_session):
        uow = DatabaseUnitOfWork(db_session)

        async with uow.savepoint():
            _ = await db_session.execute(text("SELECT 1"))

        value = (await db_session.execute(text("SELECT 2"))).scalar_one()
        assert value == 2
