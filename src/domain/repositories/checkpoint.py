"""Sync-checkpoint repository protocol.

Split from the former monolithic ``interfaces.py``.
"""

from collections.abc import Awaitable
from typing import Literal, Protocol

from src.domain.entities import (
    SyncCheckpoint,
)


class CheckpointRepositoryProtocol(Protocol):
    """Repository interface for sync checkpoint persistence operations."""

    def get_sync_checkpoint(
        self, user_id: str, service: str, entity_type: Literal["likes", "plays"]
    ) -> Awaitable[SyncCheckpoint | None]:
        """Get sync checkpoint."""
        ...

    def get_or_create_sync_checkpoint(
        self, user_id: str, service: str, entity_type: Literal["likes", "plays"]
    ) -> Awaitable[SyncCheckpoint]:
        """Get the checkpoint, or a fresh unsaved one on miss (non-persisting)."""
        ...

    def save_sync_checkpoint(
        self, checkpoint: SyncCheckpoint
    ) -> Awaitable[SyncCheckpoint]:
        """Save sync checkpoint."""
        ...
