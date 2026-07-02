"""SSE kickoff for playlist-link sync (interface concern only).

Owns the request-time orchestration the ``POST /playlists/{id}/links/{link_id}/sync``
route handler used to carry inline: parse the sync direction, run the read-only
confirmation pre-flight synchronously (so a destructive-sync 409 is reachable
before any background work), then launch the background SSE operation.

Business logic lives in ``PreviewPlaylistSyncUseCase`` / ``SyncPlaylistLinkUseCase``;
this module only wires them to the shared SSE operation runner.
"""

from uuid import UUID

from src.application.runner import execute_use_case
from src.application.use_cases.preview_playlist_sync import (
    PreviewPlaylistSyncCommand,
    PreviewPlaylistSyncUseCase,
)
from src.application.use_cases.sync_playlist_link import (
    SyncPlaylistLinkCommand,
    SyncPlaylistLinkUseCase,
    to_operation_result,
)
from src.domain.entities.playlist_link import SyncDirection
from src.domain.exceptions import ConfirmationRequiredError
from src.interface.api.schemas.imports import OperationStartedResponse
from src.interface.api.services.progress import OperationBoundEmitter
from src.interface.api.services.sse_operations import launch_sse_operation


async def launch_playlist_link_sync(
    *,
    link_id: UUID,
    user_id: str,
    direction_override: str | None,
    confirm_token: str | None,
) -> OperationStartedResponse:
    """Confirm (synchronously) then launch a background playlist-link sync.

    Returns immediately with ``{operation_id, run_id}``; progress streams via
    the shared operations SSE endpoint. A destructive sync whose ``confirm_token``
    is missing or stale raises ``ConfirmationRequiredError`` (→ HTTP 409) before
    any background work is scheduled.
    """
    parsed_direction = SyncDirection(direction_override) if direction_override else None
    await _ensure_sync_confirmed(link_id, parsed_direction, user_id, confirm_token)

    async def _sync(emitter: OperationBoundEmitter) -> object:  # noqa: ARG001
        command = SyncPlaylistLinkCommand(
            user_id=user_id,
            link_id=link_id,
            direction_override=parsed_direction,
            confirmed=True,
        )
        result = await execute_use_case(
            lambda uow: SyncPlaylistLinkUseCase().execute(command, uow),
            user_id=user_id,
        )
        return to_operation_result(result)

    return await launch_sse_operation(
        user_id=user_id,
        operation_type="sync_playlist_link",
        coro_factory=_sync,
        name_prefix="playlist_sync",
    )


async def _ensure_sync_confirmed(
    link_id: UUID,
    direction_override: SyncDirection | None,
    user_id: str,
    confirm_token: str | None,
) -> None:
    """Raise ConfirmationRequiredError (→ 409) for an unconfirmed destructive sync.

    Runs the read-only preview synchronously so the destructive-guard 409 is
    reachable at request time — the old background path swallowed it into a
    generic error SSE event the client never saw. Compares the caller's
    ``confirm_token`` against the freshly-minted one: a missing or *stale* token
    (the plan changed since the user previewed it) re-prompts with the fresh
    token + counts; a matching token (or a non-destructive plan) proceeds.
    """
    command = PreviewPlaylistSyncCommand(
        user_id=user_id, link_id=link_id, direction_override=direction_override
    )
    preview = await execute_use_case(
        lambda uow: PreviewPlaylistSyncUseCase().execute(command, uow),
        user_id=user_id,
    )
    if preview.safety_flagged and confirm_token != preview.confirm_token:
        raise ConfirmationRequiredError(
            preview.safety_message or "Destructive sync requires confirmation",
            removals=preview.safety_removals,
            total=preview.safety_total,
            remaining=preview.safety_remaining,
            confirm_token=preview.confirm_token,
        )
