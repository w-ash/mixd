"""Whole-schema operational actions for self-hosted instances.

Owns the two things that have no expression outside infrastructure: reading
the table list off SQLAlchemy metadata, and issuing ``TRUNCATE … CASCADE``.
Which tables are spared is the caller's policy, not this module's.
"""

from collections.abc import Collection, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.persistence.database.db_models import DatabaseModel
from src.infrastructure.persistence.repositories.repo_decorator import db_operation


class AdminRepository:
    """Destructive whole-schema operations (``mixd admin reset``)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def data_tables(self, preserved: Collection[str]) -> Sequence[str]:
        """Every table in the schema except ``preserved``.

        Derived from ORM metadata so it auto-maintains as the schema evolves —
        a newly added table cannot be missed here.
        """
        skip = frozenset(preserved)
        return [
            t.name for t in DatabaseModel.metadata.tables.values() if t.name not in skip
        ]

    @db_operation("truncate_data_tables")
    async def truncate_data_tables(self, preserved: Collection[str]) -> Sequence[str]:
        """Truncate every table except ``preserved``; returns what was truncated.

        One statement with CASCADE rather than per-table deletes: the tables
        are mutually FK-referencing, so any ordering that worked today would
        break the next time a relationship is added.
        """
        tables = self.data_tables(preserved)
        # Identifiers come from ORM metadata, never from user input.
        await self._session.execute(
            text("TRUNCATE TABLE " + ", ".join(tables) + " CASCADE")
        )
        return tables


__all__ = ["AdminRepository"]
