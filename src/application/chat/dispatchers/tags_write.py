"""Write tool: ``manage_tags`` — two-phase-confirmed tag mutations.

Propose/commit pattern (mirrors ``save_workflow``): ``handle_manage_tags``
validates the operation's inputs and stores a pending action carrying the exact
commit parameters plus a human-readable ``changes`` list; nothing mutates until
the user confirms. ``exec_manage_tags`` reconstructs the use-case Command from
``action.details`` at confirm time — stamping ``source`` and ``tagged_at`` there,
so the tag record captures the confirmation moment — and runs the same use case
the web UI calls (identical RLS scoping and validation). The destructive
operations (delete, merge) carry a ``severity`` + ``warning`` the confirmation
card surfaces.

Tag names are identifier-like inputs the user typed, so they are committed
verbatim (never wrapped as ``<user_data>`` display text); they appear plainly in
the ``changes`` summary.
"""

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from uuid import UUID

from src.application.chat.dispatchers._common import (
    propose_action,
    require_choice,
    require_str,
    require_uuid,
    require_uuid_list,
)
from src.application.chat.pending_actions import PendingAction
from src.application.chat.protocols import ToolContext
from src.application.runner import execute_use_case
from src.application.use_cases.batch_tag_tracks import (
    BatchTagTracksCommand,
    BatchTagTracksUseCase,
)
from src.application.use_cases.tag_track import TagTrackCommand, TagTrackUseCase
from src.application.use_cases.tag_vocabulary import (
    DeleteTagCommand,
    DeleteTagUseCase,
    MergeTagsCommand,
    MergeTagsUseCase,
    RenameTagCommand,
    RenameTagUseCase,
)
from src.application.use_cases.untag_track import (
    UntagTrackCommand,
    UntagTrackUseCase,
)
from src.domain.entities.shared import JsonDict, JsonValue
from src.domain.entities.sourced_metadata import MetadataSource
from src.domain.exceptions import NotFoundError, ToolExecutionError
from src.domain.repositories.uow import UnitOfWorkProtocol

# Agent-initiated tag writes are manual edits — same source the CLI/web use.
_AGENT_SOURCE: MetadataSource = "manual"

_OPERATIONS = ("tag", "untag", "batch_tag", "rename", "merge", "delete")


MANAGE_TAGS_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": list(_OPERATIONS),
            "description": (
                "The tag mutation to perform. 'tag'/'untag': add or remove one "
                "tag on one track (need track_id + tag). 'batch_tag': add one "
                "tag to many tracks (need track_ids + tag). 'rename': rename a "
                "tag across all tracks (need source_tag + target_tag). 'merge': "
                "collapse source_tag into target_tag across all tracks "
                "(destructive; need source_tag + target_tag). 'delete': remove a "
                "tag from all tracks (destructive; need tag)."
            ),
        },
        "track_id": {
            "type": "string",
            "description": "tag/untag: UUID of the track (from a library query).",
        },
        "track_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "batch_tag: UUIDs of the tracks to tag.",
        },
        "tag": {
            "type": "string",
            "description": "tag/untag/delete: the tag name.",
        },
        "source_tag": {
            "type": "string",
            "description": "rename/merge: the existing tag to rename or merge from.",
        },
        "target_tag": {
            "type": "string",
            "description": "rename/merge: the new or destination tag name.",
        },
    },
    "required": ["operation"],
    "additionalProperties": False,
}


def _plural(count: int) -> str:
    return "" if count == 1 else "s"


async def handle_manage_tags(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    """Propose a tag mutation — nothing persists until the user confirms.

    Validates the operation's required inputs (missing/mistyped fields raise
    ``ToolExecutionError`` naming what is wrong so the model self-corrects in the
    same turn) and stores the exact commit parameters in ``details``. The two
    destructive operations add a ``severity``/``warning`` pair.
    """
    operation = require_choice(tool_input, "operation", _OPERATIONS)

    if operation == "tag":
        track_id = require_uuid(tool_input, "track_id")
        tag = require_str(tool_input, "tag")
        description = f"Tag 1 track as '{tag}'"
        details: JsonDict = {
            "operation": operation,
            "track_id": str(track_id),
            "tag": tag,
            "changes": [f"Add tag '{tag}' to 1 track"],
        }
    elif operation == "untag":
        track_id = require_uuid(tool_input, "track_id")
        tag = require_str(tool_input, "tag")
        description = f"Untag 1 track (remove '{tag}')"
        details = {
            "operation": operation,
            "track_id": str(track_id),
            "tag": tag,
            "changes": [f"Remove tag '{tag}' from 1 track"],
        }
    elif operation == "batch_tag":
        track_ids = require_uuid_list(tool_input, "track_ids")
        tag = require_str(tool_input, "tag")
        count = len(track_ids)
        description = f"Tag {count} track{_plural(count)} as '{tag}'"
        details = {
            "operation": operation,
            "track_ids": [str(tid) for tid in track_ids],
            "tag": tag,
            "changes": [f"Add tag '{tag}' to {count} track{_plural(count)}"],
        }
    elif operation == "rename":
        source_tag = require_str(tool_input, "source_tag")
        target_tag = require_str(tool_input, "target_tag")
        description = f"Rename tag '{source_tag}' to '{target_tag}'"
        details = {
            "operation": operation,
            "source_tag": source_tag,
            "target_tag": target_tag,
            "changes": [
                f"Rename tag '{source_tag}' to '{target_tag}' across all tracks"
            ],
        }
    elif operation == "merge":
        source_tag = require_str(tool_input, "source_tag")
        target_tag = require_str(tool_input, "target_tag")
        description = f"Merge tag '{source_tag}' into '{target_tag}'"
        details = {
            "operation": operation,
            "source_tag": source_tag,
            "target_tag": target_tag,
            "severity": "destructive",
            "warning": "collapses the source tag into the target across all tracks",
            "changes": [
                f"Merge tag '{source_tag}' into '{target_tag}' across all tracks"
            ],
        }
    else:  # delete
        tag = require_str(tool_input, "tag")
        description = f"Delete tag '{tag}'"
        details = {
            "operation": operation,
            "tag": tag,
            "severity": "destructive",
            "warning": "removes all associations of the tag",
            "changes": [f"Delete tag '{tag}' from all tracks"],
        }

    return propose_action(ctx, "manage_tags", tool_input, description, details)


async def _commit[TResult](
    factory: Callable[[UnitOfWorkProtocol], Awaitable[TResult]], user_id: str
) -> TResult:
    """Run a tag use case, mapping commit-time failures to actionable errors."""
    try:
        return await execute_use_case(factory, user_id=user_id)
    except NotFoundError as e:
        raise ToolExecutionError(
            "The change could not be applied — a track referenced by this tag "
            "operation no longer exists. Re-check the tracks and try again."
        ) from e
    except ValueError as e:
        raise ToolExecutionError(
            f"The tag operation failed validation at confirm time: {e}"
        ) from e


def _confirmed(action: PendingAction, operation: str, **extra: JsonValue) -> JsonDict:
    result: JsonDict = {
        "status": "confirmed",
        "operation": operation,
        "description": action.description,
    }
    result.update(extra)
    return result


async def exec_manage_tags(action: PendingAction, user_id: str) -> JsonValue:
    """Commit the proposed tag mutation via its use case.

    Reconstructs the Command from ``action.details`` and stamps ``source`` +
    ``tagged_at`` now (confirm time), so the tag/event records the moment the
    user approved rather than when the model proposed.
    """
    details = action.details
    operation = str(details["operation"])
    tagged_at = datetime.now(UTC)

    if operation == "tag":
        command = TagTrackCommand(
            user_id=user_id,
            track_id=UUID(str(details["track_id"])),
            raw_tag=str(details["tag"]),
            source=_AGENT_SOURCE,
            tagged_at=tagged_at,
        )
        tagged = await _commit(
            lambda uow: TagTrackUseCase().execute(command, uow), user_id
        )
        return _confirmed(action, operation, tag=tagged.tag, changed=tagged.changed)

    if operation == "untag":
        untag_command = UntagTrackCommand(
            user_id=user_id,
            track_id=UUID(str(details["track_id"])),
            raw_tag=str(details["tag"]),
            source=_AGENT_SOURCE,
            tagged_at=tagged_at,
        )
        untagged = await _commit(
            lambda uow: UntagTrackUseCase().execute(untag_command, uow), user_id
        )
        return _confirmed(action, operation, tag=untagged.tag, changed=untagged.changed)

    if operation == "batch_tag":
        raw_ids = details["track_ids"]
        track_ids = (
            [UUID(str(tid)) for tid in raw_ids] if isinstance(raw_ids, list) else []
        )
        batch_command = BatchTagTracksCommand(
            user_id=user_id,
            track_ids=track_ids,
            raw_tag=str(details["tag"]),
            source=_AGENT_SOURCE,
            tagged_at=tagged_at,
        )
        batched = await _commit(
            lambda uow: BatchTagTracksUseCase().execute(batch_command, uow), user_id
        )
        return _confirmed(
            action,
            operation,
            tag=batched.tag,
            requested=batched.requested,
            tagged=batched.tagged,
        )

    if operation == "rename":
        rename_command = RenameTagCommand(
            user_id=user_id,
            source=str(details["source_tag"]),
            target=str(details["target_tag"]),
        )
        renamed = await _commit(
            lambda uow: RenameTagUseCase().execute(rename_command, uow), user_id
        )
        return _confirmed(
            action,
            operation,
            source_tag=str(details["source_tag"]),
            target_tag=str(details["target_tag"]),
            affected_count=renamed.affected_count,
        )

    if operation == "merge":
        merge_command = MergeTagsCommand(
            user_id=user_id,
            source=str(details["source_tag"]),
            target=str(details["target_tag"]),
        )
        merged = await _commit(
            lambda uow: MergeTagsUseCase().execute(merge_command, uow), user_id
        )
        return _confirmed(
            action,
            operation,
            source_tag=str(details["source_tag"]),
            target_tag=str(details["target_tag"]),
            affected_count=merged.affected_count,
        )

    if operation == "delete":
        delete_command = DeleteTagCommand(user_id=user_id, tag=str(details["tag"]))
        deleted = await _commit(
            lambda uow: DeleteTagUseCase().execute(delete_command, uow), user_id
        )
        return _confirmed(
            action,
            operation,
            tag=str(details["tag"]),
            affected_count=deleted.affected_count,
        )

    raise ToolExecutionError(f"Unknown manage_tags operation {operation!r}")


SPECS: list[dict[str, object]] = [
    {
        "name": "manage_tags",
        "description": (
            "Call this to propose a tag change on the user's library. Pick an `operation`: "
            "'tag'/'untag' add or remove one tag on one track; 'batch_tag' adds "
            "one tag to many tracks; 'rename' renames a tag across every track; "
            "'merge' collapses one tag into another; 'delete' removes a tag "
            "entirely. Merge and delete are destructive. Every operation is a "
            "proposal — nothing changes until the user confirms on the card this "
            "returns. Look up real track_ids (library query) and tag names "
            "(list_tags) first; never guess them."
        ),
        "input_schema": MANAGE_TAGS_INPUT_SCHEMA,
        "dispatch": handle_manage_tags,
        "use_cases": (
            "TagTrackUseCase",
            "UntagTrackUseCase",
            "BatchTagTracksUseCase",
            "RenameTagUseCase",
            "MergeTagsUseCase",
            "DeleteTagUseCase",
        ),
        "kind": "write",
        "executor": exec_manage_tags,
    },
]
