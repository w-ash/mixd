"""Track repository for preference operations.

Batch-first: all writes and multi-row reads operate on sequences.
Single-item callers pass a one-element list.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities.preference import (
    PreferenceEvent,
    PreferenceState,
    TrackPreference,
)
from src.infrastructure.persistence.database.db_models import (
    DBTrackPreference,
    DBTrackPreferenceEvent,
)
from src.infrastructure.persistence.repositories.base_repo import (
    BaseRepository,
    SimpleMapperFactory,
)
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)

TrackPreferenceMapper = SimpleMapperFactory.create(
    DBTrackPreference,
    TrackPreference,
)

TrackPreferenceEventMapper = SimpleMapperFactory.create(
    DBTrackPreferenceEvent,
    PreferenceEvent,
)


class TrackPreferenceRepository(BaseRepository[DBTrackPreference, TrackPreference]):
    """Repository for track preference operations."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(
            session=session,
            model_class=DBTrackPreference,
            mapper=TrackPreferenceMapper(),
        )
        self._event_mapper = TrackPreferenceEventMapper()

    @db_operation("get_preferences")
    async def get_preferences(
        self, track_ids: Sequence[UUID], *, user_id: str
    ) -> dict[UUID, TrackPreference]:
        """Get preferences for a set of tracks. Returns {track_id: preference}."""
        if not track_ids:
            return {}
        prefs = await self.find_by([
            self.model_class.track_id.in_(track_ids),
            self.model_class.user_id == user_id,
        ])
        return {p.track_id: p for p in prefs}

    @db_operation("set_preferences")
    async def set_preferences(
        self, preferences: Sequence[TrackPreference], *, user_id: str
    ) -> list[TrackPreference]:
        """Upsert preferences. UNIQUE on (user_id, track_id)."""
        if not preferences:
            return []
        entities: list[dict[str, object]] = [
            {
                "user_id": user_id,
                "track_id": p.track_id,
                "state": p.state,
                "source": p.source,
                "preferred_at": p.preferred_at,
            }
            for p in preferences
        ]
        return await self.bulk_upsert(
            entities=entities,
            lookup_keys=["user_id", "track_id"],
        )

    @db_operation("remove_preferences")
    async def remove_preferences(
        self, track_ids: Sequence[UUID], *, user_id: str
    ) -> int:
        """Remove preferences for a set of tracks. Returns the count removed."""
        if not track_ids:
            return 0
        stmt = (
            delete(self.model_class)
            .where(
                self.model_class.track_id.in_(track_ids),
                self.model_class.user_id == user_id,
            )
            .returning(self.model_class.id)
        )
        result = await self.session.execute(stmt)
        return len(result.all())

    @db_operation("add_events")
    async def add_events(
        self, events: Sequence[PreferenceEvent], *, user_id: str
    ) -> list[PreferenceEvent]:
        """Append preference change events."""
        if not events:
            return []
        db_events = [
            DBTrackPreferenceEvent(
                id=e.id,
                user_id=user_id,
                track_id=e.track_id,
                old_state=e.old_state,
                new_state=e.new_state,
                source=e.source,
                preferred_at=e.preferred_at,
            )
            for e in events
        ]
        self.session.add_all(db_events)
        await self.session.flush()
        return [await self._event_mapper.to_domain(m) for m in db_events]

    @db_operation("list_by_state")
    async def list_by_state(
        self,
        state: PreferenceState,
        *,
        user_id: str,
        limit: int = 50,
    ) -> list[TrackPreference]:
        """List preferences filtered by state, ordered by preferred_at desc."""
        return await self.find_by(
            [
                self.model_class.state == state,
                self.model_class.user_id == user_id,
            ],
            order_by=("preferred_at", False),  # DESC
            limit=limit,
        )

    @db_operation("count_by_state")
    async def count_by_state(self, *, user_id: str) -> dict[PreferenceState, int]:
        """Count preferences grouped by state."""
        stmt = (
            select(
                self.model_class.state,
                func.count(self.model_class.id),
            )
            .where(self.model_class.user_id == user_id)
            .group_by(self.model_class.state)
        )
        result = await self.session.execute(stmt)
        return {row[0]: row[1] for row in result.all()}

    @db_operation("list_by_preferred_at")
    async def list_by_preferred_at(
        self,
        *,
        user_id: str,
        before: datetime | None = None,
        after: datetime | None = None,
        limit: int = 50,
    ) -> list[TrackPreference]:
        """List preferences within a date range, ordered by preferred_at desc."""
        conditions = [self.model_class.user_id == user_id]
        if before is not None:
            conditions.append(self.model_class.preferred_at < before)
        if after is not None:
            conditions.append(self.model_class.preferred_at >= after)
        return await self.find_by(
            conditions,
            order_by=("preferred_at", False),  # DESC
            limit=limit,
        )
