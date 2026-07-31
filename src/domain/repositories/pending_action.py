"""Pending-action repository protocol (two-phase confirmed writes)."""

from collections.abc import Awaitable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from src.domain.entities.pending_action import PendingAction
from src.domain.entities.shared import JsonDict


class PendingActionRepositoryProtocol(Protocol):
    """Persistence for actions awaiting user confirmation.

    The ``pending_actions`` table has NO RLS policy (``chat_feedback`` /
    ``schedules`` precedent, migration 038): a caller must be able to see a
    foreign row's *owner* to tell "someone else's action" apart from
    "expired", and RLS invisibility would collapse the two. Every method
    therefore takes ``user_id`` explicitly and filters on it.

    Expiry is a caller-supplied ``cutoff`` rather than a constant here — the
    TTL is application policy, and passing it in keeps this protocol free of
    the clock.
    """

    def insert_evicting_expired(
        self,
        *,
        user_id: str,
        tool_name: str,
        tool_input: JsonDict,
        description: str,
        details: JsonDict,
        created_at: datetime,
        cutoff: datetime,
    ) -> Awaitable[PendingAction]:
        """Insert one action, first deleting any row older than ``cutoff``.

        Eviction rides along with the insert because these rows are
        short-lived and there is no background sweeper for them.
        """
        ...

    def claim_owned(
        self, action_id: UUID, user_id: str, cutoff: datetime
    ) -> Awaitable[PendingAction | None]:
        """Atomically retrieve-and-remove an unexpired action owned by ``user_id``.

        A single conditional ``DELETE … RETURNING`` so exactly one caller can
        win when two race on the same token. Returns ``None`` when nothing
        matched — expired, missing, or owned by someone else.
        """
        ...

    def delete_owned(self, action_id: UUID, user_id: str) -> Awaitable[bool]:
        """Remove an action owned by ``user_id``; ``True`` when a row went."""
        ...

    def get_owner(self, action_id: UUID) -> Awaitable[str | None]:
        """Owner of ``action_id`` regardless of expiry, or ``None`` if absent.

        Only used to turn a failed claim/cancel into the right error.
        """
        ...
