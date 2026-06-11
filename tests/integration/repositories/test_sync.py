"""Integration tests for SyncCheckpointRepository.

Covers get_or_create_sync_checkpoint's contract — returns the persisted row
when one exists, and a fresh *unsaved* checkpoint on miss (row-absence means
"never synced", so creation must not persist) — using the db_session fixture
with testcontainers PostgreSQL.
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities import SyncCheckpoint
from src.infrastructure.persistence.repositories.sync import SyncCheckpointRepository


class TestGetOrCreateSyncCheckpoint:
    """Non-persisting get-or-create semantics."""

    async def test_returns_persisted_checkpoint_when_exists(
        self, db_session: AsyncSession
    ) -> None:
        repo = SyncCheckpointRepository(db_session)
        saved = await repo.save_sync_checkpoint(
            SyncCheckpoint(
                user_id="default",
                service="spotify",
                entity_type="likes",
                last_timestamp=datetime.now(UTC),
                cursor="100",
            )
        )

        result = await repo.get_or_create_sync_checkpoint(
            user_id="default", service="spotify", entity_type="likes"
        )

        assert result.id == saved.id
        assert result.cursor == "100"

    async def test_miss_returns_fresh_checkpoint_with_requested_keys(
        self, db_session: AsyncSession
    ) -> None:
        repo = SyncCheckpointRepository(db_session)

        result = await repo.get_or_create_sync_checkpoint(
            user_id="default", service="lastfm", entity_type="plays"
        )

        assert result.user_id == "default"
        assert result.service == "lastfm"
        assert result.entity_type == "plays"
        assert result.last_timestamp is None
        assert result.cursor is None

    async def test_miss_does_not_persist(self, db_session: AsyncSession) -> None:
        repo = SyncCheckpointRepository(db_session)

        _ = await repo.get_or_create_sync_checkpoint(
            user_id="default", service="lastfm", entity_type="likes"
        )

        # Row-absence is the "never synced" signal — the miss must not write.
        assert (
            await repo.get_sync_checkpoint(
                user_id="default", service="lastfm", entity_type="likes"
            )
            is None
        )
