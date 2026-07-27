"""Repository for synchronization checkpoints."""

from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import or_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities import SyncCheckpoint
from src.domain.entities.shared import JsonDict
from src.infrastructure.persistence.database.db_models import DBSyncCheckpoint
from src.infrastructure.persistence.repositories.base_repo import BaseRepository
from src.infrastructure.persistence.repositories.mappers import SimpleMapperFactory
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)

# Use SimpleMapperFactory to eliminate boilerplate - this replaces ~30 lines of repetitive code
SyncCheckpointMapper = SimpleMapperFactory.create(
    DBSyncCheckpoint,
    SyncCheckpoint,
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

    @db_operation("get_or_create_sync_checkpoint")
    async def get_or_create_sync_checkpoint(
        self,
        user_id: str,
        service: str,
        entity_type: Literal["likes", "plays"],
    ) -> SyncCheckpoint:
        """Get the checkpoint, or a fresh unsaved one if none exists.

        Non-persisting on miss: the fresh checkpoint is only written when the
        caller's sync completes and saves it — a row must not exist before the
        first successful sync (checkpoint-status reads treat row-absence as
        "never synced").
        """
        existing = await self.get_sync_checkpoint(
            user_id=user_id, service=service, entity_type=entity_type
        )
        return existing or SyncCheckpoint(
            user_id=user_id, service=service, entity_type=entity_type
        )

    @db_operation("save_sync_checkpoint")
    async def save_sync_checkpoint(
        self,
        checkpoint: SyncCheckpoint,
    ) -> SyncCheckpoint:
        """Save or update a sync checkpoint.

        ``create_attrs`` deliberately lists only the three sync-cursor fields.
        ``BaseRepository.upsert``'s UPDATE arm writes exactly the keys named here
        (``base_repo._upsert_two_phase``), so the polling columns are structurally
        safe from an importer save — not merely by convention. Adding a poll
        column here would let every import clobber the backoff state.
        """
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
                "remote_total": checkpoint.remote_total,
            },
        )

    @db_operation("try_claim_poll")
    async def try_claim_poll(
        self,
        user_id: str,
        service: str,
        entity_type: Literal["likes", "plays"],
        *,
        now: datetime,
        max_age: timedelta,
        claim_ttl: timedelta,
    ) -> bool:
        """Atomically claim the poll lease. True if this caller won it.

        ONE statement by necessity. ``BaseRepository.upsert`` is a two-phase
        find-then-UPDATE-or-INSERT, and the gap between its SELECT and its write
        is exactly where two concurrent triggers would both decide they had won.

        The conflict predicate fuses the two questions — "is the lease free?" and
        "is this checkpoint actually stale?" — so a caller that raced past a
        just-finished poll is rejected by the same statement that would have
        granted it the claim. A returned row means the claim was won; no row means
        another caller holds a live lease, or the checkpoint was polled too
        recently to be worth repeating.
        """
        claim_cutoff = now - claim_ttl
        stale_cutoff = now - max_age
        table = DBSyncCheckpoint

        stmt = (
            pg_insert(table)
            .values(
                user_id=user_id,
                service=service,
                entity_type=entity_type,
                poll_claimed_at=now,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                # The UNIQUE constraint on these columns is unnamed, so target it
                # by columns rather than by a name we would have to guess.
                index_elements=["user_id", "service", "entity_type"],
                set_={"poll_claimed_at": now, "updated_at": now},
                where=or_(
                    table.poll_claimed_at.is_(None),
                    table.poll_claimed_at < claim_cutoff,
                )
                & or_(
                    table.last_polled_at.is_(None),
                    table.last_polled_at < stale_cutoff,
                ),
            )
            .returning(table.id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    @db_operation("finish_poll")
    async def finish_poll(
        self,
        user_id: str,
        service: str,
        entity_type: Literal["likes", "plays"],
        *,
        polled_at: datetime | None,
        poll_state: JsonDict,
    ) -> None:
        """Release the poll lease and persist the resulting backoff state.

        The claim is always cleared. ``last_polled_at`` moves only when
        ``polled_at`` is given (a successful poll) — advancing the freshness gate
        after a failure would make a persistently broken connector look recently
        checked, silencing the very surface meant to reveal it.
        """
        values: dict[str, object] = {
            "poll_claimed_at": None,
            "poll_state": poll_state,
            "updated_at": datetime.now(UTC),
        }
        if polled_at is not None:
            values["last_polled_at"] = polled_at

        await self.session.execute(
            update(DBSyncCheckpoint)
            .where(
                DBSyncCheckpoint.user_id == user_id,
                DBSyncCheckpoint.service == service,
                DBSyncCheckpoint.entity_type == entity_type,
            )
            .values(**values)
        )
