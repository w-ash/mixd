"""``merge_tracks`` — the two-phase write tool that folds one track into another.

Destructive and single-action (no operation discriminator). ``handle_merge_tracks``
*proposes*: it validates the two ids, builds a human-readable confirmation card
carrying a destructive ``warning``, and stores a pending action — nothing merges
yet. After the user confirms, the registry routes the claimed action to
``exec_merge_tracks``, which reconstructs the Command from ``details`` and runs
the merge through ``execute_use_case`` exactly as the web UI does, so RLS scoping
and validation are identical to a human doing it.
"""

from collections.abc import Mapping
from uuid import UUID

from src.application.chat.dispatchers._common import (
    project_track,
    propose_action,
    require_uuid,
)
from src.application.chat.pending_actions import PendingAction
from src.application.chat.protocols import ToolContext
from src.application.runner import execute_use_case
from src.application.use_cases.merge_tracks import (
    MergeTrackAndFetchDetailsUseCase,
    MergeTracksCommand,
)
from src.domain.entities.shared import JsonDict, JsonValue
from src.domain.exceptions import NotFoundError, ToolExecutionError

MERGE_TRACKS_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "winner_id": {
            "type": "string",
            "description": (
                "UUID of the canonical track to keep. All references (plays, "
                "likes, playlist entries, mappings) from the loser move onto it."
            ),
        },
        "loser_id": {
            "type": "string",
            "description": (
                "UUID of the duplicate track to fold into the winner. It and its "
                "identity cease to exist independently after the merge."
            ),
        },
    },
    "required": ["winner_id", "loser_id"],
    "additionalProperties": False,
}


async def handle_merge_tracks(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    """Propose merging the loser track into the winner — nothing merges yet.

    Validates both ids up front so an unparseable id can never sit in a pending
    action, then stores a destructive confirmation card. The mutation runs only
    through ``exec_merge_tracks`` after the user confirms.
    """
    winner_id = require_uuid(tool_input, "winner_id")
    loser_id = require_uuid(tool_input, "loser_id")
    if winner_id == loser_id:
        raise ToolExecutionError(
            "winner_id and loser_id must be different tracks — a track cannot be "
            "merged into itself."
        )

    details: JsonDict = {
        "operation": "merge",
        "winner_id": str(winner_id),
        "loser_id": str(loser_id),
        "changes": [
            f"Track {loser_id} is folded into track {winner_id}",
            f"All plays, likes, playlist entries, and mappings move to {winner_id}",
            f"Track {loser_id} is soft-deleted and ceases to exist independently",
        ],
        "severity": "destructive",
        "warning": (
            "the loser track and its identity are merged into the winner and "
            "cease to exist independently"
        ),
    }
    description = f"Merge track {loser_id} into {winner_id}"
    return propose_action(ctx, "merge_tracks", tool_input, description, details)


async def exec_merge_tracks(action: PendingAction, user_id: str) -> JsonValue:
    """Commit the proposed merge, returning the winner's refreshed detail view.

    Routes through ``MergeTrackAndFetchDetailsUseCase`` so the confirmed result
    carries the merged winner. A track deleted between propose and confirm, or a
    now-invalid pair, surfaces as an actionable error instead of a raw failure.
    """
    d = action.details
    command = MergeTracksCommand(
        user_id=user_id,
        winner_id=UUID(str(d["winner_id"])),
        loser_id=UUID(str(d["loser_id"])),
    )
    try:
        result = await execute_use_case(
            lambda uow: MergeTrackAndFetchDetailsUseCase().execute(command, uow),
            user_id=user_id,
        )
    except NotFoundError as e:
        raise ToolExecutionError(
            "One of the tracks no longer exists — it may have been merged or "
            "deleted since this merge was proposed. Re-check the tracks and try "
            "again."
        ) from e
    except ValueError as e:
        raise ToolExecutionError(f"The merge is no longer valid: {e}") from e

    return {"status": "confirmed", "merged_track": project_track(result.track)}


SPECS: list[dict[str, object]] = [
    {
        "name": "merge_tracks",
        "description": (
            "Call this to propose merging two duplicate canonical tracks into one. Pass "
            "winner_id (the track to keep) and loser_id (the duplicate to fold "
            "in) — both required UUIDs. All plays, likes, playlist entries, and "
            "connector mappings move onto the winner and the loser is "
            "soft-deleted. This is destructive and irreversible: it only "
            "proposes a confirmation card, nothing merges until the user "
            "confirms."
        ),
        "input_schema": MERGE_TRACKS_INPUT_SCHEMA,
        "dispatch": handle_merge_tracks,
        "use_cases": ("MergeTracksUseCase", "MergeTrackAndFetchDetailsUseCase"),
        "kind": "write",
        "executor": exec_merge_tracks,
    },
]
