"""An INSERT that omits ``user_id`` fails instead of minting a phantom tenant.

Migration ``056_drop_user_id_defaults`` dropped ``DEFAULT 'default'`` from every tenanted ``user_id``
column and the ORM lost ``server_default`` in the same change. The schema here
comes from ``metadata.create_all``, so this proves the ORM declaration — the
migration itself is checked by the manual round-trip in the release gate.
"""

from psycopg.errors import NotNullViolation
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.persistence.database.db_models import DBTrack


class TestUserIdHasNoDefault:
    """Every tenanted table refuses a row with no tenant."""

    async def test_track_insert_without_user_id_raises(self, db_session: AsyncSession):
        db_session.add(DBTrack(title="Orphan", artists={"names": ["Nobody"]}))

        with pytest.raises(IntegrityError) as exc_info:
            await db_session.flush()

        assert isinstance(exc_info.value.orig, NotNullViolation)
        assert 'column "user_id"' in str(exc_info.value.orig)

    async def test_no_user_id_column_carries_a_default(self, db_session: AsyncSession):
        rows = await db_session.execute(
            text(
                "SELECT table_name FROM information_schema.columns "
                "WHERE column_name = 'user_id' AND column_default IS NOT NULL "
                "AND table_schema = current_schema()"
            )
        )

        assert rows.scalars().all() == []
