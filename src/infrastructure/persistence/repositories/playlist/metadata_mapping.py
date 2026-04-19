"""Repository for playlist metadata mapping persistence."""

from collections.abc import Mapping, Sequence
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities.playlist_metadata_mapping import (
    PlaylistMappingMember,
    PlaylistMetadataMapping,
)
from src.infrastructure.persistence.database.db_models import (
    DBPlaylistMappingMember,
    DBPlaylistMetadataMapping,
)
from src.infrastructure.persistence.repositories.base_repo import (
    BaseRepository,
    SimpleMapperFactory,
)
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)

PlaylistMetadataMappingMapper = SimpleMapperFactory.create(
    DBPlaylistMetadataMapping, PlaylistMetadataMapping
)
PlaylistMappingMemberMapper = SimpleMapperFactory.create(
    DBPlaylistMappingMember, PlaylistMappingMember
)


class PlaylistMetadataMappingRepository(
    BaseRepository[DBPlaylistMetadataMapping, PlaylistMetadataMapping]
):
    """Repository for playlist metadata mappings + their member snapshots."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(
            session=session,
            model_class=DBPlaylistMetadataMapping,
            mapper=PlaylistMetadataMappingMapper(),
        )
        self._member_mapper = PlaylistMappingMemberMapper()

    @db_operation("list_for_user")
    async def list_for_user(self, *, user_id: str) -> list[PlaylistMetadataMapping]:
        return await self.find_by([self.model_class.user_id == user_id])

    @db_operation("list_for_connector_playlist")
    async def list_for_connector_playlist(
        self, connector_playlist_id: UUID, *, user_id: str
    ) -> list[PlaylistMetadataMapping]:
        return await self.find_by([
            self.model_class.user_id == user_id,
            self.model_class.connector_playlist_id == connector_playlist_id,
        ])

    @db_operation("find_by_id")
    async def find_by_id(
        self, mapping_id: UUID, *, user_id: str
    ) -> PlaylistMetadataMapping | None:
        stmt = select(self.model_class).where(
            self.model_class.id == mapping_id,
            self.model_class.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return await self.mapper.to_domain(row)

    @db_operation("create_mappings")
    async def create_mappings(
        self, mappings: Sequence[PlaylistMetadataMapping], *, user_id: str
    ) -> list[PlaylistMetadataMapping]:
        """Insert mappings. Duplicates on (connector_playlist_id, action_type,
        action_value) are silently skipped via ON CONFLICT DO NOTHING."""
        if not mappings:
            return []

        entities: list[dict[str, object]] = [
            {
                "id": m.id,
                "user_id": user_id,
                "connector_playlist_id": m.connector_playlist_id,
                "action_type": m.action_type,
                "action_value": m.action_value,
            }
            for m in mappings
        ]
        entities = self._deduplicate_batch(
            entities,
            ["connector_playlist_id", "action_type", "action_value"],
            label="create_mappings",
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

        return [m for m in mappings if m.id in inserted_ids]

    @db_operation("delete_mapping")
    async def delete_mapping(self, mapping_id: UUID, *, user_id: str) -> bool:
        stmt = (
            delete(self.model_class)
            .where(
                self.model_class.id == mapping_id,
                self.model_class.user_id == user_id,
            )
            .returning(self.model_class.id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    @db_operation("get_members")
    async def get_members(
        self, mapping_id: UUID, *, user_id: str
    ) -> list[PlaylistMappingMember]:
        stmt = select(DBPlaylistMappingMember).where(
            DBPlaylistMappingMember.mapping_id == mapping_id,
            DBPlaylistMappingMember.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        return [
            await self._member_mapper.to_domain(row) for row in result.scalars().all()
        ]

    @db_operation("get_members_for_mappings")
    async def get_members_for_mappings(
        self, mapping_ids: Sequence[UUID], *, user_id: str
    ) -> dict[UUID, list[PlaylistMappingMember]]:
        """Batch-load member snapshots for many mappings in one query.

        Returns ``{mapping_id: [members]}`` — mappings with no members
        are absent from the result.
        """
        if not mapping_ids:
            return {}
        stmt = select(DBPlaylistMappingMember).where(
            DBPlaylistMappingMember.mapping_id.in_(mapping_ids),
            DBPlaylistMappingMember.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        grouped: dict[UUID, list[PlaylistMappingMember]] = {}
        for row in result.scalars().all():
            member = await self._member_mapper.to_domain(row)
            grouped.setdefault(member.mapping_id, []).append(member)
        return grouped

    @db_operation("replace_members")
    async def replace_members(
        self,
        mapping_id: UUID,
        members: Sequence[PlaylistMappingMember],
        *,
        user_id: str,
    ) -> list[PlaylistMappingMember]:
        """DELETE all existing members for this mapping, INSERT the new set."""
        await self.session.execute(
            delete(DBPlaylistMappingMember).where(
                DBPlaylistMappingMember.mapping_id == mapping_id,
                DBPlaylistMappingMember.user_id == user_id,
            )
        )

        if not members:
            return []

        entities: list[dict[str, object]] = [
            {
                "id": m.id,
                "user_id": user_id,
                "mapping_id": m.mapping_id,
                "track_id": m.track_id,
                "synced_at": m.synced_at,
            }
            for m in members
        ]
        entities = self._deduplicate_batch(
            entities, ["mapping_id", "track_id"], label="replace_members"
        )
        self._add_timestamps(entities)
        await self.session.execute(pg_insert(DBPlaylistMappingMember).values(entities))

        return list(members)

    @db_operation("replace_members_for_mappings")
    async def replace_members_for_mappings(
        self,
        snapshots: Mapping[UUID, Sequence[PlaylistMappingMember]],
        *,
        user_id: str,
    ) -> int:
        """Batch-replace member snapshots for many mappings.

        ONE bulk DELETE over all mapping_ids, then ONE bulk INSERT of the
        flattened member set. Returns the total members written.
        """
        if not snapshots:
            return 0

        await self.session.execute(
            delete(DBPlaylistMappingMember).where(
                DBPlaylistMappingMember.mapping_id.in_(snapshots.keys()),
                DBPlaylistMappingMember.user_id == user_id,
            )
        )

        flattened: list[dict[str, object]] = [
            {
                "id": m.id,
                "user_id": user_id,
                "mapping_id": m.mapping_id,
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
            ["mapping_id", "track_id"],
            label="replace_members_for_mappings",
        )
        self._add_timestamps(flattened)
        await self.session.execute(pg_insert(DBPlaylistMappingMember).values(flattened))
        return len(flattened)
