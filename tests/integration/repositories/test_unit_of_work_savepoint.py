"""Integration tests for ``DatabaseUnitOfWork.savepoint``.

Pins the v0.10.2.2 transaction-semantics fix: a statement failing inside a
savepoint scope must leave the enclosing transaction usable, so
continue-on-error item loops (inward resolvers) survive one bad item instead
of cascading every later statement into ``InFailedSqlTransaction``.
"""

from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from src.infrastructure.persistence.database.models import DBTrack
from src.infrastructure.persistence.unit_of_work import DatabaseUnitOfWork
from tests.fixtures import TEST_USER_ID


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
        """A clean exit releases the savepoint: its write stays in the transaction."""
        uow = DatabaseUnitOfWork(db_session)
        title = f"TEST_savepoint_{uuid4().hex[:8]}"

        async with uow.savepoint():
            db_session.add(
                DBTrack(title=title, artists={"names": ["A"]}, user_id=TEST_USER_ID)
            )
            await db_session.flush()

        count = (
            await db_session.execute(
                select(func.count()).select_from(DBTrack).where(DBTrack.title == title)
            )
        ).scalar_one()
        assert count == 1
