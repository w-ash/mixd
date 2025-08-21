"""Repository for synchronization checkpoints."""

from typing import Literal

from attrs import define
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities import SyncCheckpoint
from src.infrastructure.persistence.database.db_models import DBSyncCheckpoint
from src.infrastructure.persistence.repositories.base_repo import (
    BaseModelMapper,
    BaseRepository,
)
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class SyncCheckpointMapper(BaseModelMapper[DBSyncCheckpoint, SyncCheckpoint]):
    """Maps between DBSyncCheckpoint and SyncCheckpoint domain models."""

    @staticmethod
    async def to_domain(db_model: DBSyncCheckpoint) -> SyncCheckpoint:
        """Convert database checkpoint to domain model."""
        if not db_model:
            return None

        return SyncCheckpoint(
            user_id=db_model.user_id,
            service=db_model.service,
            entity_type=db_model.entity_type,
            last_timestamp=db_model.last_timestamp,
            cursor=db_model.cursor,
            id=db_model.id,
        )

    @staticmethod
    def to_db(domain_model: SyncCheckpoint) -> DBSyncCheckpoint:
        """Convert domain checkpoint to database model."""
        return DBSyncCheckpoint(
            id=domain_model.id,
            user_id=domain_model.user_id,
            service=domain_model.service,
            entity_type=domain_model.entity_type,
            last_timestamp=domain_model.last_timestamp,
            cursor=domain_model.cursor,
        )


class SyncCheckpointRepository(BaseRepository[DBSyncCheckpoint, SyncCheckpoint]):
    """Repository for sync checkpoint operations."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with session and mapper."""
        super().__init__(
            session=session,
            model_class=DBSyncCheckpoint,
            mapper=SyncCheckpointMapper(),
        )

    @db_operation("get_sync_checkpoint")
    async def get_sync_checkpoint(
        self,
        user_id: str,
        service: str,
        entity_type: Literal["likes", "plays"],
    ) -> SyncCheckpoint | None:
        """Get synchronization checkpoint for incremental operations."""
        return await self.find_one_by({
            "user_id": user_id,
            "service": service,
            "entity_type": entity_type,
        })

    @db_operation("save_sync_checkpoint")
    async def save_sync_checkpoint(
        self,
        checkpoint: SyncCheckpoint,
    ) -> SyncCheckpoint:
        """Save or update a sync checkpoint."""
        # Use upsert to handle both creation and updates
        return await self.upsert(
            lookup_attrs={
                "user_id": checkpoint.user_id,
                "service": checkpoint.service,
                "entity_type": checkpoint.entity_type,
            },
            create_attrs={
                "last_timestamp": checkpoint.last_timestamp,
                "cursor": checkpoint.cursor,
            },
        )

    @db_operation("hard_delete_sync_checkpoint")
    async def hard_delete(self, id_: int) -> int:
        """Hard delete a sync checkpoint (alias for delete since soft delete is removed)."""
        return await self.delete(id_)
