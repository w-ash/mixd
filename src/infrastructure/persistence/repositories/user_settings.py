"""Database-backed user settings storage.

One JSONB row per user, reached through the UoW like any other repository.
Reads merge defaults over the stored blob so a row written before a setting
existed still answers with a complete object.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities.shared import JsonDict
from src.infrastructure.persistence.database.db_models import DBUserSettings
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)

# Settings key (distinct from user_id — allows multiple setting namespaces per user)
_DEFAULT_KEY = "default"

# Settings returned when no row exists yet
_DEFAULT_SETTINGS: JsonDict = {"theme_mode": "dark"}


class UserSettingsRepository:
    """Read and write user settings from the database."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @db_operation("load_user_settings")
    async def load(self, user_id: str) -> JsonDict:
        """Current settings for ``user_id``, defaults applied."""
        result = await self._session.execute(
            select(DBUserSettings.settings).where(
                DBUserSettings.user_id == user_id,
                DBUserSettings.key == _DEFAULT_KEY,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return dict(_DEFAULT_SETTINGS)
        return {**_DEFAULT_SETTINGS, **row}

    @db_operation("patch_user_settings")
    async def patch(self, updates: JsonDict, user_id: str) -> JsonDict:
        """Merge ``updates`` into the stored settings; returns the merged result.

        Read-then-upsert on one session, so the merge sees the same snapshot
        it writes back.
        """
        now = datetime.now(UTC)
        current = await self.load(user_id)
        merged: JsonDict = {**current, **updates}

        await self._session.execute(
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
        return merged


__all__ = ["UserSettingsRepository"]
