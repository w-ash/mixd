"""Database-backed user settings storage.

Uses standalone get_session() (not UoW) — settings operations are simple
single-row reads/upserts with no multi-table transaction needs.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.config import get_logger
from src.infrastructure.persistence.database.db_models import DBUserSettings

logger = get_logger(__name__)

# Single-user key. Multi-user (v1.0.0) would use user IDs.
_DEFAULT_KEY = "default"

# Settings returned when no row exists yet
_DEFAULT_SETTINGS: dict[str, Any] = {"theme_mode": "dark"}


class UserSettingsRepository:
    """Read and write user settings from the database.

    Creates its own short-lived session for each operation (same pattern
    as DatabaseTokenStorage).
    """

    async def load(self) -> dict[str, Any]:
        from src.infrastructure.persistence.database.db_connection import get_session

        async with get_session() as session:
            result = await session.execute(
                select(DBUserSettings.settings).where(
                    DBUserSettings.key == _DEFAULT_KEY
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return dict(_DEFAULT_SETTINGS)
            return {**_DEFAULT_SETTINGS, **row}

    async def patch(self, updates: dict[str, Any]) -> dict[str, Any]:
        from src.infrastructure.persistence.database.db_connection import get_session

        now = datetime.now(UTC)

        # Load current settings to merge
        current = await self.load()
        merged = {**current, **updates}

        stmt = (
            pg_insert(DBUserSettings)
            .values(
                key=_DEFAULT_KEY,
                settings=merged,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["key"],
                set_={"settings": merged, "updated_at": now},
            )
        )

        async with get_session() as session:
            await session.execute(stmt)

        return merged
