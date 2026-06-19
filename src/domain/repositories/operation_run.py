"""Operation-run audit repository protocol.

Split from the former monolithic ``interfaces.py``.
"""

from collections.abc import Awaitable, Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from src.domain.entities.operation_run import OperationRun, OperationStatus
from src.domain.entities.shared import JsonDict


class OperationRunRepositoryProtocol(Protocol):
    """Repository interface for ``OperationRun`` audit-log persistence.

    Keyset-paginated by ``(started_at, id)`` descending. Returns raw
    next-page-key tuples; the application/interface layer encodes the
    opaque cursor for the wire (matches the track-listing pattern in
    ``TrackListingPage.next_page_key``).
    """

    def create(self, run: OperationRun) -> Awaitable[OperationRun]:
        """Insert a new run row at operation kickoff."""
        ...

    def update_status(
        self,
        run_id: UUID,
        *,
        user_id: str,
        status: OperationStatus,
        ended_at: datetime | None,
        counts: JsonDict | None = None,
    ) -> Awaitable[None]:
        """Set the terminal status, ``ended_at``, and merge ``counts``.

        Counts merge at the SQL level (JSONB ``||``) so partial counts
        emitted during the run aren't clobbered by the terminal call.
        """
        ...

    def append_issue(
        self,
        run_id: UUID,
        *,
        user_id: str,
        issue: JsonDict,
    ) -> Awaitable[None]:
        """Append one issue dict to the JSONB ``issues`` array."""
        ...

    def get_by_id_for_user(
        self,
        run_id: UUID,
        *,
        user_id: str,
    ) -> Awaitable[OperationRun | None]:
        """Return the run if it exists AND is owned by ``user_id``.

        Returns ``None`` (rather than raising ``NotFoundError``) so the
        route can answer 404 for both not-found and not-owner without
        leaking row existence.
        """
        ...

    def list_for_user(
        self,
        *,
        user_id: str,
        limit: int = 20,
        after_started_at: datetime | None = None,
        after_id: UUID | None = None,
        operation_types: Sequence[str] | None = None,
    ) -> Awaitable[tuple[list[OperationRun], tuple[datetime, UUID] | None]]:
        """List runs newest-first, keyset-paginated.

        ``(after_started_at, after_id)`` is the decoded cursor — both must
        be passed together. Returns ``(rows, next_page_key)`` where
        ``next_page_key`` is ``None`` on the last page.
        """
        ...
