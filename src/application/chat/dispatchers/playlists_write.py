"""Two-phase write tools for canonical playlists and their entries.

Two tools, each selected by an ``operation`` discriminator:

- ``manage_playlist`` — ``create`` a new empty playlist, ``update`` (rename /
  redescribe) an existing one, or ``delete`` one (destructive).
- ``manage_playlist_entries`` — ``add`` tracks, ``remove`` entries, ``reorder``
  the whole entry list, or ``repair`` unresolved entries.

Each ``handle_*`` dispatcher *proposes*: it coerces the per-operation fields,
builds a human-readable confirmation card (with a destructive ``warning`` for
``delete``), and stores a pending action — nothing mutates yet. After the user
confirms, the registry routes the claimed action to the matching ``exec_*``,
which reconstructs the Command from ``action.details`` and runs the same use
case the web UI calls through ``execute_use_case`` — so RLS scoping and
validation are identical to a human doing it.

Two of the canonical-playlist constructors (``create``/``update``) require a
``MetricConfigProvider``. The executors import the concrete
``MetricConfigProviderImpl`` function-scoped (the sanctioned application→
infrastructure bridge, mirroring ``sync_playlist_link.py``) so the module's
import graph stays inward-only.
"""

from collections.abc import Mapping
from uuid import UUID

from src.application.chat.dispatchers._common import (
    commit,
    opt_int,
    opt_str,
    project_playlist,
    propose_action,
    require_choice,
    require_str,
    require_uuid,
    require_uuid_list,
)
from src.application.chat.pending_actions import PendingAction
from src.application.chat.protocols import ToolContext
from src.application.use_cases.add_playlist_tracks import (
    AddPlaylistTracksCommand,
    AddPlaylistTracksUseCase,
)
from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistCommand,
    CreateCanonicalPlaylistUseCase,
)
from src.application.use_cases.delete_canonical_playlist import (
    DeleteCanonicalPlaylistCommand,
    DeleteCanonicalPlaylistUseCase,
)
from src.application.use_cases.remove_playlist_entries import (
    RemovePlaylistEntriesCommand,
    RemovePlaylistEntriesUseCase,
)
from src.application.use_cases.reorder_playlist_entries import (
    ReorderPlaylistEntriesCommand,
    ReorderPlaylistEntriesUseCase,
)
from src.application.use_cases.repair_unresolved_entries import (
    RepairUnresolvedEntriesCommand,
    RepairUnresolvedEntriesUseCase,
)
from src.application.use_cases.update_canonical_playlist import (
    UpdateCanonicalPlaylistCommand,
    UpdateCanonicalPlaylistUseCase,
)
from src.domain.entities.shared import JsonDict, JsonValue
from src.domain.exceptions import ToolExecutionError

# Shared commit-time failure messages for the playlist and entry use cases.
_PLAYLIST_NOT_FOUND = (
    "The playlist no longer exists — it may have been deleted since this action "
    "was proposed. Re-check the playlist and try again."
)
_PLAYLIST_INVALID_PREFIX = "The playlist operation is no longer valid"
_ENTRY_NOT_FOUND = (
    "The playlist, an entry, or a track no longer exists — it may have changed "
    "since this action was proposed. Refetch the playlist and try again."
)
_ENTRY_INVALID_PREFIX = "The entry operation is no longer valid"

# ---------------------------------------------------------------------------
# Tool 1: manage_playlist  (create | update | delete)
# ---------------------------------------------------------------------------

_PLAYLIST_OPERATIONS = ("create", "update", "delete")

MANAGE_PLAYLIST_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": list(_PLAYLIST_OPERATIONS),
            "description": (
                "Which playlist operation to perform. 'create' makes a new empty "
                "playlist (needs name; optional description). 'update' renames or "
                "redescribes an existing playlist WITHOUT touching its tracks "
                "(needs playlist_id and at least one of name/description). "
                "'delete' permanently removes a playlist (destructive; needs "
                "playlist_id)."
            ),
        },
        "playlist_id": {
            "type": "string",
            "description": (
                "UUID of the canonical playlist. Required for update and delete."
            ),
        },
        "name": {
            "type": "string",
            "description": (
                "Playlist name. Required for create; optional new name for update."
            ),
        },
        "description": {
            "type": "string",
            "description": (
                "Playlist description. Optional for create; optional new "
                "description for update."
            ),
        },
    },
    "required": ["operation"],
    "additionalProperties": False,
}


def _propose_create(tool_input: Mapping[str, JsonValue], ctx: ToolContext) -> JsonValue:
    name = require_str(tool_input, "name")
    description = opt_str(tool_input, "description")
    changes = [f"A new empty playlist named {name!r} is created"]
    if description is not None:
        changes.append(f"Description set to {description!r}")
    details: JsonDict = {
        "operation": "create",
        "name": name,
        "description": description,
        "changes": changes,
    }
    return propose_action(
        ctx, "manage_playlist", tool_input, f"Create playlist {name!r}", details
    )


def _propose_update(tool_input: Mapping[str, JsonValue], ctx: ToolContext) -> JsonValue:
    playlist_id = require_uuid(tool_input, "playlist_id")
    name = opt_str(tool_input, "name")
    description = opt_str(tool_input, "description")
    if name is None and description is None:
        raise ToolExecutionError(
            "update requires at least one of 'name' or 'description' to change."
        )
    changes: list[str] = []
    if name is not None:
        changes.append(f"Playlist {playlist_id} is renamed to {name!r}")
    if description is not None:
        changes.append(f"Description set to {description!r}")
    changes.append("Tracks are left unchanged")
    details: JsonDict = {
        "operation": "update",
        "playlist_id": str(playlist_id),
        "name": name,
        "description": description,
        "changes": changes,
    }
    return propose_action(
        ctx,
        "manage_playlist",
        tool_input,
        f"Update playlist {playlist_id}",
        details,
    )


def _propose_delete(tool_input: Mapping[str, JsonValue], ctx: ToolContext) -> JsonValue:
    playlist_id = require_uuid(tool_input, "playlist_id")
    details: JsonDict = {
        "operation": "delete",
        "playlist_id": str(playlist_id),
        "changes": [
            f"Playlist {playlist_id} is permanently deleted",
            "Its tracks are preserved (they may be used in other playlists)",
        ],
        "severity": "destructive",
        "warning": "permanently deletes the playlist",
    }
    return propose_action(
        ctx,
        "manage_playlist",
        tool_input,
        f"Delete playlist {playlist_id}",
        details,
    )


async def handle_manage_playlist(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    """Propose one playlist operation — nothing mutates until the user confirms.

    Coerces the per-operation fields up front so an unparseable value can never
    sit in a pending action, then stores a confirmation card. Missing/invalid
    fields raise ``ToolExecutionError`` naming what is required so the model
    self-corrects in the same turn.
    """
    operation = require_choice(tool_input, "operation", _PLAYLIST_OPERATIONS)
    if operation == "create":
        return _propose_create(tool_input, ctx)
    if operation == "update":
        return _propose_update(tool_input, ctx)
    return _propose_delete(tool_input, ctx)


async def _exec_create(d: JsonDict, user_id: str) -> JsonValue:
    from src.domain.entities.track import TrackList
    from src.infrastructure.connectors._shared.metric_registry import (
        MetricConfigProviderImpl,
    )

    description = d["description"]
    command = CreateCanonicalPlaylistCommand(
        user_id=user_id,
        name=str(d["name"]),
        tracklist=TrackList(),
        description=str(description) if description is not None else None,
    )
    use_case = CreateCanonicalPlaylistUseCase(metric_config=MetricConfigProviderImpl())
    result = await commit(
        lambda uow: use_case.execute(command, uow),
        user_id,
        not_found=_PLAYLIST_NOT_FOUND,
        invalid_prefix=_PLAYLIST_INVALID_PREFIX,
    )
    return {
        "status": "confirmed",
        "operation": "create",
        "playlist": project_playlist(result.playlist),
    }


async def _exec_update(d: JsonDict, user_id: str) -> JsonValue:
    from src.domain.entities.track import TrackList
    from src.infrastructure.connectors._shared.metric_registry import (
        MetricConfigProviderImpl,
    )

    name = d["name"]
    description = d["description"]
    # Empty tracklist + name/description → the use case takes its metadata-only
    # path (update_canonical_playlist.py:190-198), preserving existing entries.
    command = UpdateCanonicalPlaylistCommand(
        user_id=user_id,
        playlist_id=str(d["playlist_id"]),
        new_tracklist=TrackList(),
        playlist_name=str(name) if name is not None else None,
        playlist_description=str(description) if description is not None else None,
    )
    use_case = UpdateCanonicalPlaylistUseCase(metric_config=MetricConfigProviderImpl())
    result = await commit(
        lambda uow: use_case.execute(command, uow),
        user_id,
        not_found=_PLAYLIST_NOT_FOUND,
        invalid_prefix=_PLAYLIST_INVALID_PREFIX,
    )
    return {
        "status": "confirmed",
        "operation": "update",
        "playlist": project_playlist(result.playlist),
    }


async def _exec_delete(d: JsonDict, user_id: str) -> JsonValue:
    # The chat two-phase confirm IS the gate, so force_delete bypasses the
    # external-connection warning the use case would otherwise raise.
    command = DeleteCanonicalPlaylistCommand(
        user_id=user_id,
        playlist_id=str(d["playlist_id"]),
        force_delete=True,
    )
    result = await commit(
        lambda uow: DeleteCanonicalPlaylistUseCase().execute(command, uow),
        user_id,
        not_found=_PLAYLIST_NOT_FOUND,
        invalid_prefix=_PLAYLIST_INVALID_PREFIX,
    )
    return {
        "status": "confirmed",
        "operation": "delete",
        "deleted_playlist_id": str(result.deleted_playlist_id),
        "deleted_playlist_name": result.deleted_playlist_name,
        "tracks_count": result.tracks_count,
    }


async def exec_manage_playlist(action: PendingAction, user_id: str) -> JsonValue:
    """Commit the proposed playlist operation through its use case.

    Re-validates at commit time: a playlist that changed or vanished between
    propose and confirm surfaces as an actionable ``ToolExecutionError``
    (``NotFoundError`` → gone; ``ValueError`` → a use-case guard) rather than a
    raw failure.
    """
    d = action.details
    operation = str(d["operation"])
    if operation == "create":
        return await _exec_create(d, user_id)
    if operation == "update":
        return await _exec_update(d, user_id)
    return await _exec_delete(d, user_id)


# ---------------------------------------------------------------------------
# Tool 2: manage_playlist_entries  (add | remove | reorder | repair)
# ---------------------------------------------------------------------------

_ENTRY_OPERATIONS = ("add", "remove", "reorder", "repair")

MANAGE_PLAYLIST_ENTRIES_INPUT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": list(_ENTRY_OPERATIONS),
            "description": (
                "Which entry operation to perform. 'add' appends canonical tracks "
                "(needs playlist_id and track_ids; optional 0-based position). "
                "'remove' drops entries by their ENTRY ids (needs playlist_id and "
                "entry_ids). 'reorder' sets the whole order (needs playlist_id and "
                "entry_ids as the complete ordered list). 'repair' re-resolves "
                "unresolved entries against known track mappings (needs "
                "playlist_id)."
            ),
        },
        "playlist_id": {
            "type": "string",
            "description": "UUID of the canonical playlist. Required for every operation.",
        },
        "track_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "UUIDs of canonical TRACKS to append (duplicates allowed). "
                "Required for add."
            ),
        },
        "entry_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "UUIDs of playlist ENTRIES (from query_playlists detail entries — "
                "NOT track ids). For remove: the entries to drop. For reorder: the "
                "playlist's complete entry-id list in the desired order."
            ),
        },
        "position": {
            "type": "integer",
            "description": (
                "add only: 0-based index to insert at. Omit to append to the end."
            ),
        },
    },
    "required": ["operation"],
    "additionalProperties": False,
}


def _propose_add(tool_input: Mapping[str, JsonValue], ctx: ToolContext) -> JsonValue:
    playlist_id = require_uuid(tool_input, "playlist_id")
    track_ids = require_uuid_list(tool_input, "track_ids")
    position: int | None = None
    if tool_input.get("position") is not None:
        position = opt_int(
            tool_input, "position", default=0, minimum=0, maximum=1_000_000
        )
    where = "the end" if position is None else f"position {position}"
    details: JsonDict = {
        "operation": "add",
        "playlist_id": str(playlist_id),
        "track_ids": [str(t) for t in track_ids],
        "position": position,
        "changes": [
            f"{len(track_ids)} track(s) are added to playlist {playlist_id} at {where}"
        ],
    }
    return propose_action(
        ctx,
        "manage_playlist_entries",
        tool_input,
        f"Add {len(track_ids)} track(s) to playlist {playlist_id}",
        details,
    )


def _propose_remove(tool_input: Mapping[str, JsonValue], ctx: ToolContext) -> JsonValue:
    playlist_id = require_uuid(tool_input, "playlist_id")
    entry_ids = require_uuid_list(tool_input, "entry_ids")
    details: JsonDict = {
        "operation": "remove",
        "playlist_id": str(playlist_id),
        "entry_ids": [str(e) for e in entry_ids],
        "changes": [
            f"{len(entry_ids)} entry(ies) are removed from playlist {playlist_id}"
        ],
    }
    return propose_action(
        ctx,
        "manage_playlist_entries",
        tool_input,
        f"Remove {len(entry_ids)} entry(ies) from playlist {playlist_id}",
        details,
    )


def _propose_reorder(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    playlist_id = require_uuid(tool_input, "playlist_id")
    entry_ids = require_uuid_list(tool_input, "entry_ids")
    details: JsonDict = {
        "operation": "reorder",
        "playlist_id": str(playlist_id),
        "entry_ids": [str(e) for e in entry_ids],
        "changes": [
            f"Playlist {playlist_id} entries are reordered "
            f"({len(entry_ids)} entries, full list)"
        ],
    }
    return propose_action(
        ctx,
        "manage_playlist_entries",
        tool_input,
        f"Reorder playlist {playlist_id}",
        details,
    )


def _propose_repair(tool_input: Mapping[str, JsonValue], ctx: ToolContext) -> JsonValue:
    playlist_id = require_uuid(tool_input, "playlist_id")
    details: JsonDict = {
        "operation": "repair",
        "playlist_id": str(playlist_id),
        "changes": [
            f"Unresolved entries of playlist {playlist_id} are re-resolved "
            "against known track mappings"
        ],
    }
    return propose_action(
        ctx,
        "manage_playlist_entries",
        tool_input,
        f"Repair unresolved entries of playlist {playlist_id}",
        details,
    )


async def handle_manage_playlist_entries(
    tool_input: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    """Propose one entry operation — nothing mutates until the user confirms.

    Coerces the per-operation fields up front so an unparseable id can never sit
    in a pending action, then stores a confirmation card. Missing/invalid fields
    raise ``ToolExecutionError`` naming what is required so the model
    self-corrects in the same turn.
    """
    operation = require_choice(tool_input, "operation", _ENTRY_OPERATIONS)
    if operation == "add":
        return _propose_add(tool_input, ctx)
    if operation == "remove":
        return _propose_remove(tool_input, ctx)
    if operation == "reorder":
        return _propose_reorder(tool_input, ctx)
    return _propose_repair(tool_input, ctx)


async def _exec_add(d: JsonDict, user_id: str) -> JsonValue:
    raw_position = d.get("position")
    command = AddPlaylistTracksCommand(
        user_id=user_id,
        playlist_id=UUID(str(d["playlist_id"])),
        track_ids=[UUID(str(t)) for t in _as_list(d["track_ids"])],
        position=int(raw_position) if isinstance(raw_position, int) else None,
    )
    result = await commit(
        lambda uow: AddPlaylistTracksUseCase().execute(command, uow),
        user_id,
        not_found=_ENTRY_NOT_FOUND,
        invalid_prefix=_ENTRY_INVALID_PREFIX,
    )
    return {
        "status": "confirmed",
        "operation": "add",
        "added": result.added,
        "playlist": project_playlist(result.playlist),
    }


async def _exec_remove(d: JsonDict, user_id: str) -> JsonValue:
    command = RemovePlaylistEntriesCommand(
        user_id=user_id,
        playlist_id=UUID(str(d["playlist_id"])),
        entry_ids=[UUID(str(e)) for e in _as_list(d["entry_ids"])],
    )
    result = await commit(
        lambda uow: RemovePlaylistEntriesUseCase().execute(command, uow),
        user_id,
        not_found=_ENTRY_NOT_FOUND,
        invalid_prefix=_ENTRY_INVALID_PREFIX,
    )
    return {
        "status": "confirmed",
        "operation": "remove",
        "removed": result.removed,
        "playlist": project_playlist(result.playlist),
    }


async def _exec_reorder(d: JsonDict, user_id: str) -> JsonValue:
    command = ReorderPlaylistEntriesCommand(
        user_id=user_id,
        playlist_id=UUID(str(d["playlist_id"])),
        entry_ids=[UUID(str(e)) for e in _as_list(d["entry_ids"])],
    )
    result = await commit(
        lambda uow: ReorderPlaylistEntriesUseCase().execute(command, uow),
        user_id,
        not_found=_ENTRY_NOT_FOUND,
        invalid_prefix=_ENTRY_INVALID_PREFIX,
    )
    return {
        "status": "confirmed",
        "operation": "reorder",
        "playlist": project_playlist(result.playlist),
    }


async def _exec_repair(d: JsonDict, user_id: str) -> JsonValue:
    command = RepairUnresolvedEntriesCommand(
        user_id=user_id,
        playlist_id=UUID(str(d["playlist_id"])),
    )
    result = await commit(
        lambda uow: RepairUnresolvedEntriesUseCase().execute(command, uow),
        user_id,
        not_found=_ENTRY_NOT_FOUND,
        invalid_prefix=_ENTRY_INVALID_PREFIX,
    )
    return {
        "status": "confirmed",
        "operation": "repair",
        "repaired": result.repaired,
        "still_unresolved": result.still_unresolved,
    }


def _as_list(value: JsonValue) -> list[JsonValue]:
    """Narrow a details value back to the id list a propose step stored."""
    if not isinstance(value, list):
        raise ToolExecutionError("Pending action is missing its id list")
    return value


async def exec_manage_playlist_entries(
    action: PendingAction, user_id: str
) -> JsonValue:
    """Commit the proposed entry operation through its use case.

    Re-validates at commit time: a playlist/entry/track that changed or vanished
    between propose and confirm surfaces as an actionable ``ToolExecutionError``
    (``NotFoundError`` → gone/stale; ``ValueError`` → a use-case guard) rather
    than a raw failure.
    """
    d = action.details
    operation = str(d["operation"])
    if operation == "add":
        return await _exec_add(d, user_id)
    if operation == "remove":
        return await _exec_remove(d, user_id)
    if operation == "reorder":
        return await _exec_reorder(d, user_id)
    return await _exec_repair(d, user_id)


SPECS: list[dict[str, object]] = [
    {
        "name": "manage_playlist",
        "description": (
            "Call this to propose creating, renaming, or deleting a canonical playlist. Pick "
            "an `operation`: 'create' makes a new empty playlist (needs name; "
            "optional description); 'update' renames or redescribes an existing "
            "playlist WITHOUT touching its tracks (needs playlist_id and at least "
            "one of name/description); 'delete' permanently removes a playlist "
            "(destructive; needs playlist_id). It only proposes a confirmation "
            "card — nothing changes until the user confirms."
        ),
        "input_schema": MANAGE_PLAYLIST_INPUT_SCHEMA,
        "dispatch": handle_manage_playlist,
        "use_cases": (
            "CreateCanonicalPlaylistUseCase",
            "UpdateCanonicalPlaylistUseCase",
            "DeleteCanonicalPlaylistUseCase",
        ),
        "kind": "write",
        "executor": exec_manage_playlist,
    },
    {
        "name": "manage_playlist_entries",
        "description": (
            "Call this to propose changing a canonical playlist's entries. Pick an "
            "`operation`: 'add' appends canonical tracks (needs playlist_id and "
            "track_ids; optional 0-based position); 'remove' drops entries by "
            "their ENTRY ids (needs playlist_id and entry_ids); 'reorder' sets "
            "the whole order (needs playlist_id and entry_ids as the complete "
            "ordered list); 'repair' re-resolves unresolved entries against known "
            "track mappings (needs playlist_id). Entry ids come from "
            "query_playlists detail entries and are NOT track ids. It only "
            "proposes a confirmation card — nothing changes until the user confirms."
        ),
        "input_schema": MANAGE_PLAYLIST_ENTRIES_INPUT_SCHEMA,
        "dispatch": handle_manage_playlist_entries,
        "use_cases": (
            "AddPlaylistTracksUseCase",
            "RemovePlaylistEntriesUseCase",
            "ReorderPlaylistEntriesUseCase",
            "RepairUnresolvedEntriesUseCase",
        ),
        "kind": "write",
        "executor": exec_manage_playlist_entries,
    },
]
