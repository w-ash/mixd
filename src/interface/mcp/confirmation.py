"""In-band two-phase confirmation for MCP write tools.

Any ``kind: "write"`` tool called without ``confirm=true`` returns a structured
preview + ``confirm_token`` instead of executing; a second call with
``confirm=true`` and that token commits. This reuses the shared confirmation
machinery exactly as the in-app chat route does — no duplicate store, one
preview renderer:

- Preview  → ``execute_tool`` runs the write dispatcher's propose path, which
  stores a ``PendingAction`` in ``pending_action_store`` and returns the
  ``pending_confirmation`` payload. We reshape it to ``needs_confirmation`` with
  the ``action_id`` as the ``confirm_token``.
- Commit   → ``pending_action_store.claim`` (owner-checked, 5-min TTL) then
  ``execute_confirmed_action`` runs the same use case the web UI calls.

Guarantees the plan requires:
- **Expired/unknown token → a fresh preview**, never a stale commit — because the
  MCP client re-sends the full arguments on the confirm call, we can transparently
  re-propose.
- **Args drift → rejection** — the arguments on the confirm call must match the
  previewed action's stored input, else we refuse and hand back a fresh preview.

Long-running (``launches_operation``) writes are not routed here — they are not
exposed over MCP until the gated Tasks-extension epic.
"""

from uuid import UUID

from src.application.chat.pending_actions import pending_action_store
from src.application.chat.protocols import ToolContext
from src.application.chat.user_data import strip_user_data
from src.application.tools.registry import (
    ToolSpec,
    execute_confirmed_action,
    execute_tool,
)
from src.domain.entities.shared import JsonDict, JsonValue
from src.domain.exceptions import ActionExpiredError, ForbiddenError, ToolExecutionError


async def _preview(
    spec: ToolSpec, arguments: dict[str, JsonValue], ctx: ToolContext
) -> JsonDict:
    """Run the propose path and reshape it into a ``needs_confirmation`` payload."""
    proposal = await execute_tool(spec.name, arguments, ctx)
    if (
        not isinstance(proposal, dict)
        or proposal.get("status") != "pending_confirmation"
    ):
        # A write dispatcher must propose, never commit. If one ever returns a
        # non-proposal, surface it rather than silently masking a contract break.
        raise ToolExecutionError(f"{spec.name} did not return a confirmation proposal")
    return {
        "status": "needs_confirmation",
        "confirm_token": proposal["action_id"],
        "description": proposal["description"],
        "preview": proposal["details"],
    }


async def handle_write_call(
    spec: ToolSpec, arguments: dict[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    """Drive the two-phase confirmation for one MCP write-tool call.

    ``arguments`` still carries the injected ``confirm`` / ``confirm_token``
    fields; they are consumed here and never reach the dispatcher.
    """
    confirm = bool(arguments.pop("confirm", False))
    token = arguments.pop("confirm_token", None)

    if not confirm:
        return await _preview(spec, arguments, ctx)

    if not isinstance(token, str) or not token:
        raise ToolExecutionError(
            "confirm=true requires the confirm_token from a prior preview call."
        )
    try:
        action_id = UUID(token)
    except ValueError:
        # A malformed token can't name a real action — re-preview cleanly.
        return await _preview(spec, arguments, ctx)

    try:
        action = pending_action_store.claim(action_id, ctx.user_id)
    except ActionExpiredError:
        # Expired or unknown token → a fresh preview, never a stale commit.
        return await _preview(spec, arguments, ctx)
    except ForbiddenError as e:
        raise ToolExecutionError(str(e)) from e

    # Args drift: the confirm call must commit exactly what was previewed. The
    # dispatcher stored the (user-data-stripped) propose-time args as
    # ``tool_input``; compare the stripped confirm-time args against it. A
    # mismatch is a rejection (not a silent re-preview) so a client can never
    # believe it confirmed B while A commits — the claimed token is already
    # consumed, so it must re-preview to proceed.
    clean_args = strip_user_data(dict(arguments))
    if clean_args != action.tool_input:
        raise ToolExecutionError(
            "Arguments changed since the preview; nothing was committed. Call "
            "again without confirm to get a fresh preview of the new arguments."
        )

    return await execute_confirmed_action(action, ctx.user_id)
