"""Repository for playlist assignment persistence."""

from collections.abc import Mapping, Sequence
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities.playlist_assignment import (
    PlaylistAssignment,
    PlaylistAssignmentMember,
)
from src.infrastructure.persistence.database.db_models import (
    DBPlaylistAssignment,
    DBPlaylistAssignmentMember,
)
from src.infrastructure.persistence.repositories.base_repo import (
    BaseRepository,
    SimpleMapperFactory,
)
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)

PlaylistAssignmentMapper = SimpleMapperFactory.create(
    DBPlaylistAssignment, PlaylistAssignment
)
PlaylistAssignmentMemberMapper = SimpleMapperFactory.create(
    DBPlaylistAssignmentMember, PlaylistAssignmentMember
)


class PlaylistAssignmentRepository(
    BaseRepository[DBPlaylistAssignment, PlaylistAssignment]
):
    """Repository for playlist assignments + their member snapshots."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(
            session=session,
            model_class=DBPlaylistAssignment,
            mapper=PlaylistAssignmentMapper(),
        )
        self._member_mapper = PlaylistAssignmentMemberMapper()

    @db_operation("list_for_user")
    async def list_for_user(self, *, user_id: str) -> list[PlaylistAssignment]:
        return await self.find_by([self.model_class.user_id == user_id])

    @db_operation("list_for_ids")
    async def list_for_ids(
        self, assignment_ids: Sequence[UUID], *, user_id: str
    ) -> list[PlaylistAssignment]:
        if not assignment_ids:
            return []
        return await self.find_by([
            self.model_class.user_id == user_id,
            self.model_class.id.in_(assignment_ids),
        ])

    @db_operation("list_for_connector_playlist")
    async def list_for_connector_playlist(
        self, connector_playlist_id: UUID, *, user_id: str
    ) -> list[PlaylistAssignment]:
        return await self.find_by([
            self.model_class.user_id == user_id,
            self.model_class.connector_playlist_id == connector_playlist_id,
        ])

    @db_operation("list_for_connector_playlist_ids")
    async def list_for_connector_playlist_ids(
        self, connector_playlist_ids: Sequence[UUID], *, user_id: str
    ) -> dict[UUID, list[PlaylistAssignment]]:
        if not connector_playlist_ids:
            return {}
        rows = await self.find_by([
            self.model_class.user_id == user_id,
            self.model_class.connector_playlist_id.in_(connector_playlist_ids),
        ])
        grouped: dict[UUID, list[PlaylistAssignment]] = {}
        for row in rows:
            grouped.setdefault(row.connector_playlist_id, []).append(row)
        return grouped

    @db_operation("create_assignments")
    async def create_assignments(
        self, assignments: Sequence[PlaylistAssignment], *, user_id: str
    ) -> list[PlaylistAssignment]:
        """Insert assignments. Duplicates on (connector_playlist_id, action_type,
        action_value) are silently skipped via ON CONFLICT DO NOTHING."""
        if not assignments:
            return []

        entities: list[dict[str, object]] = [
            {
                "id": a.id,
                "user_id": user_id,
                "connector_playlist_id": a.connector_playlist_id,
                "action_type": a.action_type,
                "action_value": a.action_value,
            }
            for a in assignments
        ]
        entities = self._deduplicate_batch(
            entities,
            ["connector_playlist_id", "action_type", "action_value"],
            label="create_assignments",
        )
        self._add_timestamps(entities)

        async with self.session.begin_nested():
            stmt = (
                pg_insert(self.model_class)
                .values(entities)
                .on_conflict_do_nothing(
                    index_elements=[
                        self.model_class.connector_playlist_id,
                        self.model_class.action_type,
                        self.model_class.action_value,
                    ],
                )
                .returning(self.model_class.id)
            )
            result = await self.session.execute(stmt)
            inserted_ids = {row[0] for row in result.all()}

        return [a for a in assignments if a.id in inserted_ids]

    @db_operation("delete_assignment")
    async def delete_assignment(self, assignment_id: UUID, *, user_id: str) -> bool:
        stmt = (
            delete(self.model_class)
            .where(
                self.model_class.id == assignment_id,
                self.model_class.user_id == user_id,
            )
            .returning(self.model_class.id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    @db_operation("get_members_for_assignments")
    async def get_members_for_assignments(
        self, assignment_ids: Sequence[UUID], *, user_id: str
    ) -> dict[UUID, list[PlaylistAssignmentMember]]:
        """Batch-load member snapshots for many assignments in one query.

        Returns ``{assignment_id: [members]}`` — assignments with no members
        are absent from the result.
        """
        if not assignment_ids:
            return {}
        stmt = select(DBPlaylistAssignmentMember).where(
            DBPlaylistAssignmentMember.assignment_id.in_(assignment_ids),
            DBPlaylistAssignmentMember.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        grouped: dict[UUID, list[PlaylistAssignmentMember]] = {}
        for row in result.scalars().all():
            member = await self._member_mapper.to_domain(row)
            grouped.setdefault(member.assignment_id, []).append(member)
        return grouped

    @db_operation("replace_members_for_assignments")
    async def replace_members_for_assignments(
        self,
        snapshots: Mapping[UUID, Sequence[PlaylistAssignmentMember]],
        *,
        user_id: str,
    ) -> int:
        """Batch-replace member snapshots for many assignments.

        ONE bulk DELETE over all assignment_ids, then ONE bulk INSERT of the
        flattened member set. Returns the total members written.
        """
        if not snapshots:
            return 0

        await self.session.execute(
            delete(DBPlaylistAssignmentMember).where(
                DBPlaylistAssignmentMember.assignment_id.in_(snapshots.keys()),
                DBPlaylistAssignmentMember.user_id == user_id,
            )
        )

        flattened: list[dict[str, object]] = [
            {
                "id": m.id,
                "user_id": user_id,
                "assignment_id": m.assignment_id,
                "track_id": m.track_id,
                "synced_at": m.synced_at,
            }
            for members in snapshots.values()
            for m in members
        ]

        if not flattened:
            return 0

        flattened = self._deduplicate_batch(
            flattened,
            ["assignment_id", "track_id"],
            label="replace_members_for_assignments",
        )
        self._add_timestamps(flattened)
        await self.session.execute(
            pg_insert(DBPlaylistAssignmentMember).values(flattened)
        )
        return len(flattened)
