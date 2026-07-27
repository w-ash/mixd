"""Sync-checkpoint repository protocol.

Split from the former monolithic ``interfaces.py``.
"""

from collections.abc import Awaitable
from typing import Literal, Protocol

from src.domain.entities import (
    SyncCheckpoint,
)


class CheckpointRepositoryProtocol(Protocol):
    """Repository interface for sync checkpoint persistence operations.

    **``user_id`` must be the mixd user id — never a service-side account name.**
    ``sync_checkpoints`` carries a FORCE-RLS ``user_isolation`` policy keyed on
    that column (migrations 007 + 011) with no separate ``WITH CHECK``, so the
    policy gates writes as well as reads. A checkpoint stored under any other
    key is rejected on write and invisible on read — and because importers
    treat "no checkpoint" as "start from the default window", the failure is
    silent: the sync simply never resumes.

    That is not hypothetical. The Last.fm importer keyed on the Last.fm username
    and its checkpoint never persisted once; every incremental import re-scanned
    the same default window (fixed 2026-07-26). Identify the account some other
    way — the cursor, or service metadata — and keep this column for tenancy.
    """

    def get_sync_checkpoint(
        self, user_id: str, service: str, entity_type: Literal["likes", "plays"]
    ) -> Awaitable[SyncCheckpoint | None]:
        """Get sync checkpoint (``user_id`` = the mixd user; see class docstring)."""
        ...

    def get_or_create_sync_checkpoint(
        self, user_id: str, service: str, entity_type: Literal["likes", "plays"]
    ) -> Awaitable[SyncCheckpoint]:
        """Get the checkpoint, or a fresh unsaved one on miss (non-persisting)."""
        ...

    def save_sync_checkpoint(
        self, checkpoint: SyncCheckpoint
    ) -> Awaitable[SyncCheckpoint]:
        """Save sync checkpoint (upsert on ``user_id`` + service + entity_type)."""
        ...
