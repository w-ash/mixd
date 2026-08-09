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

    def append_issues(
        self,
        run_id: UUID,
        *,
        user_id: str,
        issues: Sequence[JsonDict],
    ) -> Awaitable[None]:
        """Append issue dicts to the JSONB ``issues`` array in one statement.

        Batched: a partially-failed import records one issue per unresolved
        item, and those must not become N round-trips.
        """
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
        status: OperationStatus | None = None,
    ) -> Awaitable[tuple[list[OperationRun], tuple[datetime, UUID] | None]]:
        """List runs newest-first, keyset-paginated.

        ``(after_started_at, after_id)`` is the decoded cursor — both must
        be passed together. Returns ``(rows, next_page_key)`` where
        ``next_page_key`` is ``None`` on the last page. ``status`` filters to a
        single lifecycle state (e.g. ``"running"`` for operation-awareness).
        """
        ...

    def list_running_started_before(
        self,
        cutoff: datetime,
        *,
        limit: int,
    ) -> Awaitable[list[OperationRun]]:
        """Rows still ``running`` whose ``started_at`` precedes ``cutoff`` — all users.

        The one cross-user read on this table: the startup reaper
        (``operation_run_reaper``) and the pre-deploy busy gate ask a
        process-level question ("is anything in flight on this machine?"),
        not a tenant one. ``cutoff = now`` is the degenerate case meaning
        "every running row", which is how the busy gate counts in-flight
        work with the same query the reaper filters by age.

        Requires the connection role to bypass RLS (Neon's owner role has
        BYPASSRLS — migration 035 documents that posture): under a future
        non-bypass role this query would silently see only the ambient
        ``app.user_id``'s rows.
        """
        ...

    def count_running_started_since(self, cutoff: datetime) -> Awaitable[int]:
        """How many rows are still ``running`` with ``started_at >= cutoff`` — all users.

        The busy gate's half of the running-rows pair: a SQL ``count(*)`` with
        the age bound in the predicate, because the gate needs one integer and
        must see EVERY live run — a fetch-then-filter through
        :meth:`list_running_started_before` is capped by its ``limit``, so
        enough stale rows could truncate a live run out of the count and let a
        deploy land mid-import. The reaper keeps the list method (it needs the
        rows to finalize). Same cross-user/BYPASSRLS posture as
        :meth:`list_running_started_before`.
        """
        ...

    def count_running_started_before(self, cutoff: datetime) -> Awaitable[int]:
        """How many rows are still ``running`` with ``started_at < cutoff`` — all users.

        The complementary window to :meth:`count_running_started_since`: the
        busy gate excludes these rows as reaper-dead (the deploy it guards is
        the restart that reaps them), and this count is what lets the gate
        report the exclusion — ``stale_running_operation_runs`` in the health
        probe — instead of hiding it. Same cross-user/BYPASSRLS posture.
        """
        ...
