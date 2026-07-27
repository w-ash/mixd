"""Sync-checkpoint repository protocol.

Split from the former monolithic ``interfaces.py``.
"""

from collections.abc import Awaitable
from datetime import datetime, timedelta
from typing import Literal, Protocol

from src.domain.entities import (
    SyncCheckpoint,
)
from src.domain.entities.shared import JsonDict


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

    def try_claim_poll(
        self,
        user_id: str,
        service: str,
        entity_type: Literal["likes", "plays"],
        *,
        now: datetime,
        max_age: timedelta,
        claim_ttl: timedelta,
    ) -> Awaitable[bool]:
        """Atomically claim the right to poll this checkpoint. True if won.

        Single-flight across every process serving a trigger. This is a **lease**,
        not a lock: the claim is a timestamp reclaimed after ``claim_ttl``, in the
        same shape as ``schedules.started_at`` and its stuck-claim reaper.

        A transaction-scoped primitive cannot work here. ``FOR UPDATE SKIP
        LOCKED`` and ``pg_try_advisory_xact_lock`` both release at COMMIT, and the
        importer this guards commits per batch — the guard would evaporate partway
        through the very operation it protects.

        No fencing token accompanies the lease, deliberately. If a slow holder
        wakes after its TTL expired and a second poll already ran, the overlap is
        harmless: both read the same cursor-based window into a conflict-skipping,
        order-free, re-import-safe ledger.

        Claiming inserts the row when none exists, which is safe against the
        "row absence means never synced" convention: status reads key on
        ``last_timestamp``, which stays NULL until a sync actually succeeds.
        """
        ...

    def finish_poll(
        self,
        user_id: str,
        service: str,
        entity_type: Literal["likes", "plays"],
        *,
        polled_at: datetime | None,
        poll_state: JsonDict,
    ) -> Awaitable[None]:
        """Release the poll lease and record its outcome.

        Always clears the claim — callers invoke this from a ``finally``, so a
        crashed poll frees the lease immediately rather than waiting out the TTL.
        ``polled_at`` is None when the poll failed: the backoff state still
        advances, but the freshness gate must not move forward on a poll that
        fetched nothing, or a persistent failure would read as "recently checked".
        """
        ...
