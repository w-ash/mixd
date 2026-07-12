"""Interface-layer ``OperationLauncher`` for confirmed long-running chat tools.

Epic 3's five long tools (``run_workflow``, ``import_connector_playlists``,
``apply_playlist_assignments``, ``sync_playlist_link``, ``import_data``) carry no
application executor: launching a background SSE operation needs the operation
registry, progress broker, and background dispatch, all of which live in the
interface layer. So the application registry calls this launcher on confirmation
(``execute_confirmed_action(..., operation_launcher=launch_chat_operation)``); it
maps the claimed action's ``tool_name`` + ``details`` (stored by
``application/chat/dispatchers/long_ops.py``) to the *same* interface launcher
the matching REST route uses, and returns the ``{operation_id, run_id}`` handle
the chat panel subscribes to over ``GET /operations/{id}/progress``.

The returned envelope is intentionally uniform across all five tools —
``{"status": "operation_started", "operation_id", "run_id", "description"}`` — so
the frontend ``ToolResultCard`` can dispatch a progress card on
``summary.status == "operation_started"`` regardless of which tool launched.
"""

from collections.abc import Awaitable, Callable, Sequence
from uuid import UUID

from src.application.chat.pending_actions import PendingAction
from src.application.services.progress_broker import get_progress_broker
from src.application.use_cases.apply_playlist_assignments import (
    run_apply_playlist_assignments,
)
from src.application.use_cases.import_connector_playlist_as_canonical import (
    run_import_connector_playlists_as_canonical,
    to_operation_result,
)
from src.application.use_cases.import_play_history import ImportMode, run_import
from src.application.use_cases.sync_likes import run_spotify_likes_import
from src.domain.entities.playlist import SPOTIFY_CONNECTOR
from src.domain.entities.playlist_link import SyncDirection
from src.domain.entities.shared import ConnectorPlaylistIdentifier, JsonDict
from src.domain.exceptions import ToolExecutionError
from src.interface.api.services.playlist_sync import launch_playlist_link_sync
from src.interface.api.services.progress import OperationBoundEmitter
from src.interface.api.services.sse_operations import launch_sse_operation
from src.interface.api.services.workflow_execution import launch_workflow_run

# One launcher: reads the confirmed action's details, kicks off the background
# operation, and returns the ``(operation_id, run_id)`` handle for the envelope.
type _LaunchFn = Callable[[JsonDict, str], Awaitable[tuple[str, str | None]]]


# --- details narrowing (details were validated by long_ops.py at propose time,
# but JsonValue still needs narrowing to concrete types for the launcher calls) -


def _require_str(details: JsonDict, key: str) -> str:
    value = details.get(key)
    if not isinstance(value, str) or not value:
        raise ToolExecutionError(f"Missing '{key}' in confirmed action details")
    return value


def _opt_str(details: JsonDict, key: str) -> str | None:
    value = details.get(key)
    return value if isinstance(value, str) and value else None


def _str_list(details: JsonDict, key: str) -> list[str]:
    raw = details.get(key)
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise ToolExecutionError(f"'{key}' in confirmed action details must be a list")
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ToolExecutionError(f"'{key}' must contain only strings")
        out.append(item)
    return out


def _opt_int(details: JsonDict, key: str) -> int | None:
    value = details.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _flag(details: JsonDict, key: str) -> bool:
    return details.get(key) is True


# --- per-tool launchers (each mirrors its REST route's wiring) --------------


async def _launch_run_workflow(
    details: JsonDict, user_id: str
) -> tuple[str, str | None]:
    """Mirror ``POST /workflows/{id}/run`` (``launch_workflow_run``)."""

    workflow_id = UUID(_require_str(details, "workflow_id"))
    resp = await launch_workflow_run(workflow_id, user_id)
    return resp.operation_id, str(resp.run_id)


async def _launch_sync_playlist_link(
    details: JsonDict, user_id: str
) -> tuple[str, str | None]:
    """Mirror ``POST /playlists/{id}/links/{link_id}/sync`` (``launch_playlist_link_sync``).

    Threads ``confirm_token`` + ``direction_override`` so a destructive sync
    whose token is stale/missing still raises ``ConfirmationRequiredError`` here.
    """

    link_id = UUID(_require_str(details, "link_id"))
    resp = await launch_playlist_link_sync(
        link_id=link_id,
        user_id=user_id,
        direction_override=_opt_str(details, "direction_override"),
        confirm_token=_opt_str(details, "confirm_token"),
        initiated_by="assistant",
    )
    return resp.operation_id, resp.run_id


async def _launch_import_connector_playlists(
    details: JsonDict, user_id: str
) -> tuple[str, str | None]:
    """Mirror ``POST /connectors/{service}/playlists/import``."""
    connector_name = _require_str(details, "connector_name")
    identifiers = [
        ConnectorPlaylistIdentifier(x) for x in _str_list(details, "identifiers")
    ]
    sync_direction = SyncDirection(_opt_str(details, "sync_direction") or "pull")
    force = _flag(details, "force")

    async def _import(emitter: OperationBoundEmitter) -> object:
        result = await run_import_connector_playlists_as_canonical(
            user_id=user_id,
            connector_name=connector_name,
            connector_playlist_identifiers=identifiers,
            sync_direction=sync_direction,
            force=force,
            progress_emitter=emitter,
            progress_broker=get_progress_broker(),
            parent_operation_id=emitter.operation_id,
            run_id=emitter.run_id,
        )
        return to_operation_result(result)

    resp = await launch_sse_operation(
        user_id=user_id,
        operation_type="import_connector_playlists",
        coro_factory=_import,
        initiated_by="assistant",
        request_params={
            "connector_name": connector_name,
            "sync_direction": sync_direction.value,
        },
    )
    return resp.operation_id, resp.run_id


async def _launch_apply_playlist_assignments(
    details: JsonDict, user_id: str
) -> tuple[str, str | None]:
    """Mirror ``POST /playlist-assignments/apply-bulk``."""
    connector_name = _opt_str(details, "connector_name") or SPOTIFY_CONNECTOR
    raw_ids = details.get("assignment_ids")
    assignment_ids = (
        [UUID(x) for x in _str_list(details, "assignment_ids")]
        if raw_ids is not None
        else None
    )

    async def _apply(emitter: OperationBoundEmitter) -> object:
        return await run_apply_playlist_assignments(
            user_id=user_id,
            connector_name=connector_name,
            assignment_ids=assignment_ids,
            progress_emitter=emitter,
        )

    resp = await launch_sse_operation(
        user_id=user_id,
        operation_type="apply_assignments_bulk",
        coro_factory=_apply,
        name_prefix="apply_bulk",
        initiated_by="assistant",
    )
    return resp.operation_id, resp.run_id


async def _launch_import_data(
    details: JsonDict, user_id: str
) -> tuple[str, str | None]:
    """Mirror ``POST /imports/lastfm/history`` and ``POST /imports/spotify/likes``.

    Spotify listening *history* is deliberately out of scope from chat (its REST
    route ingests an uploaded GDPR export file the chat channel can't provide),
    so it never reaches the enum — ``import_data``'s propose-time ``require_choice``
    rejects it before an action is ever stored.
    """
    source = _require_str(details, "source")
    limit = _opt_int(details, "limit")
    force = _flag(details, "force")

    if source == "lastfm_history":
        # ``force`` = re-import from the beginning (full) vs resume a checkpoint
        # (incremental) — the same semantics the details contract documents.
        mode: ImportMode = "full" if force else "incremental"
        username = _opt_str(details, "username")

        async def _lastfm(emitter: OperationBoundEmitter) -> object:
            return await run_import(
                user_id=user_id,
                service="lastfm",
                mode=mode,
                limit=limit,
                username=username,
                progress_emitter=emitter,
            )

        resp = await launch_sse_operation(
            user_id=user_id,
            operation_type="import_lastfm_history",
            coro_factory=_lastfm,
            initiated_by="assistant",
        )
        return resp.operation_id, resp.run_id

    if source == "spotify_likes":

        async def _likes(emitter: OperationBoundEmitter) -> object:
            return await run_spotify_likes_import(
                user_id=user_id,
                limit=limit,
                force=force,
                progress_emitter=emitter,
            )

        resp = await launch_sse_operation(
            user_id=user_id,
            operation_type="import_spotify_likes",
            coro_factory=_likes,
            initiated_by="assistant",
        )
        return resp.operation_id, resp.run_id

    # Unreachable in practice — the propose-time enum admits only the two sources
    # above — but kept as a loud guard against a future misclassified spec.
    raise ToolExecutionError(f"Unknown import source: {source}")


_LAUNCHERS: dict[str, _LaunchFn] = {
    "run_workflow": _launch_run_workflow,
    "sync_playlist_link": _launch_sync_playlist_link,
    "import_connector_playlists": _launch_import_connector_playlists,
    "apply_playlist_assignments": _launch_apply_playlist_assignments,
    "import_data": _launch_import_data,
}


async def launch_chat_operation(action: PendingAction, user_id: str) -> JsonDict:
    """Launch the background operation a confirmed long-running chat action names.

    Implements ``application.chat.protocols.OperationLauncher``. Injected into
    ``execute_confirmed_action`` by the FastAPI chat route; unknown ``tool_name``
    raises so a misclassified spec fails loudly rather than silently no-op'ing.
    """
    launcher = _LAUNCHERS.get(action.tool_name)
    if launcher is None:
        raise ToolExecutionError(
            f"'{action.tool_name}' is not a launchable chat operation"
        )
    operation_id, run_id = await launcher(action.details, user_id)
    return {
        "status": "operation_started",
        "operation_id": operation_id,
        "run_id": run_id,
        "description": action.description,
    }
