"""Write tool: ``set_preferences`` — two-phase-confirmed preference mutations.

Propose/commit pattern (mirrors ``save_workflow``): ``handle_set_preferences``
validates the operation's inputs and stores a pending action carrying the exact
commit parameters plus a human-readable ``changes`` list; nothing mutates until
the user confirms. ``exec_set_preferences`` reconstructs the use-case Command
from ``action.details`` at confirm time — stamping ``source`` and ``preferred_at``
there, so the preference record captures the confirmation moment — and runs the
same use case the web UI calls (identical RLS scoping and validation).

Two operations: ``set`` writes (or, with a null state, clears) one track's
preference; ``sync_from_likes`` derives preferences from imported likes.
"""

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from uuid import UUID

from src.application.chat.dispatchers._common import (
    propose_action,
    require_choice,
    require_uuid,
)
from src.application.chat.pending_actions import PendingAction
from src.application.chat.protocols import ToolContext
from src.application.runner import execute_use_case
from src.application.use_cases.set_track_preference import (
    SetTrackPreferenceCommand,
    SetTrackPreferenceUseCase,
)
from src.application.use_cases.sync_preferences_from_likes import (
    SyncPreferencesFromLikesCommand,
    SyncPreferencesFromLikesUseCase,
)
from src.domain.entities.preference import PreferenceState
from src.domain.entities.shared import JsonDict, JsonValue
from src.domain.entities.sourced_metadata import MetadataSource
from src.domain.exceptions import NotFoundError, ToolExecutionError
from src.domain.repositories.uow import UnitOfWorkProtocol

# Agent-initiated preference writes are manual edits — same source the UI uses.
_AGENT_SOURCE: MetadataSource = "manual"

_OPERATIONS = ("set", "sync_from_likes")
_STATES = ("hmm", "nah", "yah", "star")


SET_PREFERENCES_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": list(_OPERATIONS),
            "description": (
                "The preference change to perform. 'set': write one track's "
                "preference (need track_id; pass state, or omit/null to clear "
                "it). 'sync_from_likes': derive preferences from already-imported "
                "likes (Spotify like -> yah, Last.fm love -> star), skipping "
                "tracks whose manual preference should win."
            ),
        },
        "track_id": {
            "type": "string",
            "description": "set: UUID of the track (from a library query).",
        },
        "state": {
            "type": "string",
            "enum": list(_STATES),
            "description": (
                "set: the preference to record — hmm (undecided), nah "
                "(rejected), yah (approved), or star (highly curated). Omit or "
                "pass null to clear an existing preference."
            ),
        },
    },
    "required": ["operation"],
    "additionalProperties": False,
}


def _coerce_state(value: object) -> PreferenceState:
    """Narrow a raw value to a ``PreferenceState`` literal without ``cast``."""
    match value:
        case "hmm":
            return "hmm"
        case "nah":
            return "nah"
        case "yah":
            return "yah"
        case "star":
            return "star"
        case _:
            raise ToolExecutionError(
                "'state' must be one of hmm, nah, yah, star (or omit to clear), "
                f"got {value!r}"
            )


async def handle_set_preferences(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    """Propose a preference change — nothing persists until the user confirms.

    Validates required inputs (missing/mistyped fields raise
    ``ToolExecutionError`` naming what is wrong so the model self-corrects in the
    same turn) and stores the exact commit parameters in ``details``.
    """
    operation = require_choice(tool_input, "operation", _OPERATIONS)

    if operation == "set":
        track_id = require_uuid(tool_input, "track_id")
        raw_state = tool_input.get("state")
        state: PreferenceState | None = (
            None if raw_state is None else _coerce_state(raw_state)
        )
        if state is None:
            description = "Clear the preference on 1 track"
            change = "Remove the preference from 1 track"
        else:
            description = f"Set preference '{state}' on 1 track"
            change = f"Set preference to '{state}' on 1 track"
        details: JsonDict = {
            "operation": operation,
            "track_id": str(track_id),
            "state": state,
            "changes": [change],
        }
    else:  # sync_from_likes
        description = "Sync preferences from imported likes"
        details = {
            "operation": operation,
            "changes": [
                "Create or upgrade preferences from Spotify likes and Last.fm "
                "loves (manual preferences preserved)"
            ],
        }

    return propose_action(ctx, "set_preferences", tool_input, description, details)


async def _commit[TResult](
    factory: Callable[[UnitOfWorkProtocol], Awaitable[TResult]], user_id: str
) -> TResult:
    """Run a preference use case, mapping commit-time failures to errors."""
    try:
        return await execute_use_case(factory, user_id=user_id)
    except NotFoundError as e:
        raise ToolExecutionError(
            "The change could not be applied — the track no longer exists. It "
            "may have been removed since the change was proposed."
        ) from e
    except ValueError as e:
        raise ToolExecutionError(
            f"The preference change failed validation at confirm time: {e}"
        ) from e


async def exec_set_preferences(action: PendingAction, user_id: str) -> JsonValue:
    """Commit the proposed preference change via its use case.

    Reconstructs the Command from ``action.details`` and stamps ``source`` +
    ``preferred_at`` now (confirm time), so the preference records the moment the
    user approved rather than when the model proposed.
    """
    details = action.details
    operation = str(details["operation"])

    if operation == "set":
        raw_state = details.get("state")
        state: PreferenceState | None = (
            None if raw_state is None else _coerce_state(raw_state)
        )
        command = SetTrackPreferenceCommand(
            user_id=user_id,
            track_id=UUID(str(details["track_id"])),
            state=state,
            source=_AGENT_SOURCE,
            preferred_at=datetime.now(UTC),
        )
        result = await _commit(
            lambda uow: SetTrackPreferenceUseCase().execute(command, uow), user_id
        )
        return {
            "status": "confirmed",
            "operation": operation,
            "description": action.description,
            "track_id": str(result.track_id),
            "state": result.state,
            "changed": result.changed,
        }

    if operation == "sync_from_likes":
        sync_command = SyncPreferencesFromLikesCommand(user_id=user_id)
        synced = await _commit(
            lambda uow: SyncPreferencesFromLikesUseCase().execute(sync_command, uow),
            user_id,
        )
        return {
            "status": "confirmed",
            "operation": operation,
            "description": action.description,
            "created": synced.created,
            "upgraded": synced.upgraded,
            "skipped": synced.skipped,
        }

    raise ToolExecutionError(f"Unknown set_preferences operation {operation!r}")


SPECS: list[dict[str, object]] = [
    {
        "name": "set_preferences",
        "description": (
            "Call this to propose a preference change on the user's library. Pick an "
            "`operation`: 'set' records one track's preference (hmm/nah/yah/star, "
            "or omit state to clear it — needs track_id); 'sync_from_likes' "
            "derives preferences from already-imported Spotify likes and Last.fm "
            "loves. Every operation is a proposal — nothing changes until the "
            "user confirms on the card this returns. Look up the real track_id "
            "(library query) before a 'set'; never guess it."
        ),
        "input_schema": SET_PREFERENCES_INPUT_SCHEMA,
        "dispatch": handle_set_preferences,
        "use_cases": (
            "SetTrackPreferenceUseCase",
            "SyncPreferencesFromLikesUseCase",
        ),
        "kind": "write",
        "executor": exec_set_preferences,
    },
]
