"""Database-backed user settings storage.

Uses standalone get_session() (not UoW) — settings operations are simple
single-row reads/upserts with no multi-table transaction needs.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.config import get_logger
from src.domain.entities.shared import JsonDict
from src.infrastructure.persistence.database.db_models import DBUserSettings

logger = get_logger(__name__)

# Settings key (distinct from user_id — allows multiple setting namespaces per user)
_DEFAULT_KEY = "default"

# Settings returned when no row exists yet
_DEFAULT_SETTINGS: JsonDict = {"theme_mode": "dark"}


class UserSettingsRepository:
    """Read and write user settings from the database.

    Creates its own short-lived session for each operation (same pattern
    as DatabaseTokenStorage).
    """

    async def load(self, user_id: str) -> JsonDict:
        from src.infrastructure.persistence.database.db_connection import get_session

        async with get_session() as session:
            result = await session.execute(
                select(DBUserSettings.settings).where(
                    DBUserSettings.user_id == user_id,
                    DBUserSettings.key == _DEFAULT_KEY,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return dict(_DEFAULT_SETTINGS)
            return {**_DEFAULT_SETTINGS, **row}

    async def patch(self, updates: JsonDict, user_id: str) -> JsonDict:
        from src.infrastructure.persistence.database.db_connection import get_session

        now = datetime.now(UTC)

        # Load current settings to merge
        current = await self.load(user_id)
        merged: JsonDict = {**current, **updates}

        stmt = (
            pg_insert(DBUserSettings)
            .values(
                user_id=user_id,
                key=_DEFAULT_KEY,
                settings=merged,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["user_id", "key"],
                set_={"settings": merged, "updated_at": now},
            )
        )

        async with get_session() as session:
            await session.execute(stmt)

        return merged
