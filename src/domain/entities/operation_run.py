"""OperationRun domain entity.

Persistent audit row for every long-running SSE operation. The seam-level
recorder (``application/services/operation_run_recorder.py``) writes one
row at kickoff with ``status="running"`` and updates it on terminal
events with the final status, merged counts, and any accumulated issues.

Counts and issues are intentionally JSONB-shaped because each operation
type defines its own payload (failed track vs. conflict vs. rate-limit
skip). Normalize when a third consumer needs to query across types.
"""

from datetime import datetime
from typing import Final, Literal
from uuid import UUID, uuid7

from attrs import define, field

from .shared import JsonDict, empty_json_map, utc_now_factory

type OperationStatus = Literal["running", "complete", "error", "cancelled"]

# Operation types whose audit row carries enough to reconstruct a targeted
# "retry the failed items" run — connector config in ``request_params`` plus
# per-playlist ids in ``issues``. Domain business rule (per domain-purity): the
# single place that decides what is retryable.
_RETRYABLE_OPERATION_TYPES: Final[frozenset[str]] = frozenset({
    "import_connector_playlists"
})


def _empty_issues() -> list[JsonDict]:
    """Typed factory for the ``issues`` field default."""
    return []


@define(frozen=True, slots=True)
class OperationRun:
    """One row per SSE-backed operation run."""

    user_id: str
    operation_type: str
    started_at: datetime
    status: OperationStatus
    ended_at: datetime | None = None
    counts: JsonDict = field(factory=empty_json_map)
    issues: list[JsonDict] = field(factory=_empty_issues)
    # Parameters to re-invoke this operation (connector_name + sync_direction for
    # an import), so "Retry failed only" can rebuild the call from the row. Failed
    # item identifiers come from ``issues``; this carries only connector config
    # (strings) — never UUIDs or user_id (the retry route re-derives the owner).
    request_params: JsonDict = field(factory=empty_json_map)
    # The SSE queue key for this run, when launched via the SSE seam. Lets the
    # snapshot / active-operations endpoints resolve it and re-attach the stream.
    operation_id: str | None = None
    # Provenance: the schedule that fired this run, if any (None for runs the
    # user kicked off directly). ON DELETE SET NULL preserves history.
    triggered_by_schedule_id: UUID | None = None
    # Attribution: who initiated this run — "manual" (the user, default),
    # "assistant" (an AI-agent-launched background operation), or "schedule".
    # Surfaced in the run log so an agent-initiated op is visible without
    # trusting one's memory of the chat.
    initiated_by: str = "manual"
    created_at: datetime = field(factory=utc_now_factory)
    updated_at: datetime = field(factory=utc_now_factory)
    id: UUID = field(factory=uuid7)

    @property
    def failed_connector_identifiers(self) -> list[str]:
        """Connector-playlist ids of the failed items, read from ``issues``.

        The retry path re-invokes the import with exactly this subset.
        """
        return [
            str(issue["connector_playlist_identifier"])
            for issue in self.issues
            if issue.get("connector_playlist_identifier")
        ]

    @property
    def is_retryable(self) -> bool:
        """Whether a targeted "retry the failed items" run can be rebuilt from
        this row alone — the single source of truth for both the retry route's
        409 gate and the ``retryable`` flag the UI reads.
        """
        return (
            self.status == "error"
            and self.operation_type in _RETRYABLE_OPERATION_TYPES
            and bool(self.request_params.get("connector_name"))
            and bool(self.request_params.get("sync_direction"))
            and bool(self.failed_connector_identifiers)
        )
