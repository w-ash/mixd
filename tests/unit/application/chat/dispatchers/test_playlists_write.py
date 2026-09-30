"""Unit tests for the ``manage_playlist`` / ``manage_playlist_entries`` writes.

Both tools follow the two-phase pattern: ``handle_*`` proposes (stores a pending
action, mutates nothing) and ``exec_*`` commits through the matching use case.
The pending-action store is swapped for a fresh instance per test so proposals
don't leak, and ``execute_use_case`` is monkeypatched on the module under test so
the commit path never touches a database.

The load-bearing case is ``update``: the chat rename must NOT touch tracks. Its
executor passes an EMPTY ``TrackList`` plus name/description, driving the use
case's metadata-only path (``update_canonical_playlist.py:190-198``). The
commit tests invoke the real factory and capture the Command, so the rename
test proves the tracklist is empty and only name/description are set, and the
entry tests prove ids, order, and position reach the use case.
"""

from uuid import UUID, uuid4

import pytest

from src.application.chat.dispatchers import _common, playlists_write
from src.application.chat.pending_actions import PendingAction
from src.application.chat.protocols import ToolContext
from src.application.use_cases.add_playlist_tracks import (
    AddPlaylistTracksCommand,
    AddPlaylistTracksResult,
    AddPlaylistTracksUseCase,
)
from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistCommand,
    CreateCanonicalPlaylistResult,
    CreateCanonicalPlaylistUseCase,
)
from src.application.use_cases.delete_canonical_playlist import (
    DeleteCanonicalPlaylistCommand,
    DeleteCanonicalPlaylistResult,
    DeleteCanonicalPlaylistUseCase,
)
from src.application.use_cases.remove_playlist_entries import (
    RemovePlaylistEntriesCommand,
    RemovePlaylistEntriesResult,
    RemovePlaylistEntriesUseCase,
)
from src.application.use_cases.reorder_playlist_entries import (
    ReorderPlaylistEntriesCommand,
    ReorderPlaylistEntriesResult,
    ReorderPlaylistEntriesUseCase,
)
from src.application.use_cases.repair_unresolved_entries import (
    RepairUnresolvedEntriesResult,
)
from src.application.use_cases.update_canonical_playlist import (
    UpdateCanonicalPlaylistCommand,
    UpdateCanonicalPlaylistResult,
    UpdateCanonicalPlaylistUseCase,
)
from src.domain.entities.shared import JsonDict
from src.domain.exceptions import NotFoundError, ToolExecutionError
from tests.fixtures import InMemoryPendingActionStore, make_playlist

_CTX = ToolContext(user_id="default")


@pytest.fixture
def fresh_store(monkeypatch: pytest.MonkeyPatch) -> InMemoryPendingActionStore:
    store = InMemoryPendingActionStore()
    monkeypatch.setattr(_common, "pending_action_store", store)
    return store


def _runner_returning(result: object):
    """A fake ``execute_use_case`` that ignores the factory and returns ``result``."""

    async def _run(factory, user_id: str | None = None):  # matches runner signature
        return result

    return _run


def _runner_invoking():
    """A fake ``execute_use_case`` that actually invokes the factory (with a stub uow).

    Used by the rename test so the real Command construction + use-case wiring
    runs; the use case's ``execute`` is patched separately to capture the Command.
    """

    async def _run(factory, user_id: str | None = None):
        return await factory(object())

    return _run


def _capture(
    monkeypatch: pytest.MonkeyPatch, use_case: type, result: object
) -> dict[str, object]:
    """Run the dispatcher's real factory into ``use_case``; record its Command.

    Also records the ``user_id`` the runner received, so a commit that drops the
    caller's tenant fails.
    """
    seen: dict[str, object] = {}

    async def _execute(self: object, command: object, uow: object) -> object:
        seen["command"] = command
        return result

    async def _run(factory, user_id: str | None = None):  # runner signature
        seen["user_id"] = user_id
        return await factory(object())

    monkeypatch.setattr(use_case, "execute", _execute)
    monkeypatch.setattr(_common, "execute_use_case", _run)
    return seen


def _raising_runner(exc: Exception):
    async def _run(factory, user_id: str | None = None):
        raise exc

    return _run


async def _action(operation: str, details: JsonDict, tool_name: str) -> PendingAction:
    return await InMemoryPendingActionStore().create(
        user_id="default",
        tool_name=tool_name,
        tool_input={},
        description=operation,
        details=details,
    )


# ---------------------------------------------------------------------------
# manage_playlist
# ---------------------------------------------------------------------------


class TestManagePlaylistPropose:
    async def test_create_proposes_pending_confirmation(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        result = await playlists_write.handle_manage_playlist(
            {"operation": "create", "name": "Focus", "description": "deep work"}, _CTX
        )

        assert result["status"] == "pending_confirmation"
        details = result["details"]
        assert details["operation"] == "create"
        assert details["name"] == "Focus"
        assert details["description"] == "deep work"
        assert details["changes"]
        # No destructive metadata on a non-destructive op.
        assert "severity" not in details

        action = await fresh_store.claim(UUID(result["action_id"]), "default")
        assert action.tool_name == "manage_playlist"

    async def test_create_without_name_is_actionable(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="name"):
            await playlists_write.handle_manage_playlist({"operation": "create"}, _CTX)

    async def test_update_requires_name_or_description(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="at least one"):
            await playlists_write.handle_manage_playlist(
                {"operation": "update", "playlist_id": str(uuid4())}, _CTX
            )

    async def test_update_proposes_metadata_change(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        pid = str(uuid4())
        result = await playlists_write.handle_manage_playlist(
            {"operation": "update", "playlist_id": pid, "name": "Renamed"}, _CTX
        )

        details = result["details"]
        assert details["operation"] == "update"
        assert details["playlist_id"] == pid
        assert details["name"] == "Renamed"
        assert details["description"] is None
        assert any("unchanged" in c for c in details["changes"])

    async def test_update_bad_playlist_uuid_rejected_before_pending(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        # Coercion happens up front: a non-UUID id raises rather than sitting in
        # a pending action the confirmed executor would choke on.
        with pytest.raises(ToolExecutionError, match="playlist_id"):
            await playlists_write.handle_manage_playlist(
                {"operation": "update", "playlist_id": "not-a-uuid", "name": "X"},
                _CTX,
            )
        assert not fresh_store._actions

    async def test_delete_bad_playlist_uuid_rejected_before_pending(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="playlist_id"):
            await playlists_write.handle_manage_playlist(
                {"operation": "delete", "playlist_id": "not-a-uuid"}, _CTX
            )
        assert not fresh_store._actions

    async def test_delete_proposes_destructive_confirmation(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        pid = str(uuid4())
        result = await playlists_write.handle_manage_playlist(
            {"operation": "delete", "playlist_id": pid}, _CTX
        )

        details = result["details"]
        assert details["operation"] == "delete"
        assert details["severity"] == "destructive"
        assert "permanently deletes" in details["warning"]


class TestExecManagePlaylist:
    async def test_create_commits_through_use_case(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pid = uuid4()
        seen = _capture(
            monkeypatch,
            CreateCanonicalPlaylistUseCase,
            CreateCanonicalPlaylistResult(playlist=make_playlist(id=pid, name="Focus")),
        )
        action = await _action(
            "create",
            {"operation": "create", "name": "Focus", "description": "deep work"},
            "manage_playlist",
        )

        result = await playlists_write.exec_manage_playlist(action, "user-7")

        command = seen["command"]
        assert isinstance(command, CreateCanonicalPlaylistCommand)
        assert command.name == "Focus"
        assert command.description == "deep work"
        # A chat-created playlist starts empty; tracks arrive via the entries tool.
        assert command.tracklist.tracks == []
        assert command.user_id == "user-7"
        assert seen["user_id"] == "user-7"
        assert result["status"] == "confirmed"
        assert result["operation"] == "create"
        assert result["playlist"]["playlist_id"] == str(pid)

    async def test_update_rename_preserves_tracks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The rename executor must drive the metadata-only path: an empty
        tracklist plus name/description, so existing entries are untouched."""
        pid = uuid4()
        captured: dict[str, UpdateCanonicalPlaylistCommand] = {}

        async def _capture(
            self: UpdateCanonicalPlaylistUseCase,
            command: UpdateCanonicalPlaylistCommand,
            uow: object,
        ) -> UpdateCanonicalPlaylistResult:
            captured["command"] = command
            return UpdateCanonicalPlaylistResult(
                playlist=make_playlist(id=pid, name="Renamed")
            )

        monkeypatch.setattr(UpdateCanonicalPlaylistUseCase, "execute", _capture)
        monkeypatch.setattr(_common, "execute_use_case", _runner_invoking())
        action = await _action(
            "update",
            {
                "operation": "update",
                "playlist_id": str(pid),
                "name": "Renamed",
                "description": None,
            },
            "manage_playlist",
        )

        result = await playlists_write.exec_manage_playlist(action, "default")

        command = captured["command"]
        # Proof it is a metadata-only update: empty tracklist, name set, no
        # description change, correct target — tracks are never touched.
        assert command.new_tracklist.tracks == []
        assert command.playlist_name == "Renamed"
        assert command.playlist_description is None
        assert command.playlist_id == str(pid)
        assert result["status"] == "confirmed"
        assert result["operation"] == "update"

    async def test_delete_commits_with_force_and_echoes_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _capture(
            monkeypatch,
            DeleteCanonicalPlaylistUseCase,
            DeleteCanonicalPlaylistResult(
                deleted_playlist_id=UUID(int=7),
                deleted_playlist_name="Old",
                tracks_count=3,
            ),
        )
        action = await _action(
            "delete",
            {"operation": "delete", "playlist_id": str(UUID(int=7))},
            "manage_playlist",
        )

        result = await playlists_write.exec_manage_playlist(action, "user-7")

        command = seen["command"]
        assert isinstance(command, DeleteCanonicalPlaylistCommand)
        assert command.playlist_id == str(UUID(int=7))
        # The chat confirmation IS the gate, so the external-connection warning
        # the use case would otherwise raise is bypassed.
        assert command.force_delete is True
        assert command.user_id == "user-7"
        assert result["status"] == "confirmed"
        assert result["deleted_playlist_id"] == str(UUID(int=7))
        assert result["deleted_playlist_name"] == "Old"
        assert result["tracks_count"] == 3

    async def test_delete_of_gone_playlist_is_actionable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            _common,
            "execute_use_case",
            _raising_runner(NotFoundError("gone")),
        )
        action = await _action(
            "delete",
            {"operation": "delete", "playlist_id": str(uuid4())},
            "manage_playlist",
        )

        with pytest.raises(ToolExecutionError, match="no longer exists"):
            await playlists_write.exec_manage_playlist(action, "default")


# ---------------------------------------------------------------------------
# manage_playlist_entries
# ---------------------------------------------------------------------------


class TestManagePlaylistEntriesPropose:
    async def test_add_proposes_pending_confirmation(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        pid, tid = uuid4(), uuid4()
        result = await playlists_write.handle_manage_playlist_entries(
            {
                "operation": "add",
                "playlist_id": str(pid),
                "track_ids": [str(tid)],
                "position": 2,
            },
            _CTX,
        )

        details = result["details"]
        assert details["operation"] == "add"
        assert details["playlist_id"] == str(pid)
        assert details["track_ids"] == [str(tid)]
        assert details["position"] == 2

        action = await fresh_store.claim(UUID(result["action_id"]), "default")
        assert action.tool_name == "manage_playlist_entries"

    async def test_add_defaults_position_to_none(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        result = await playlists_write.handle_manage_playlist_entries(
            {
                "operation": "add",
                "playlist_id": str(uuid4()),
                "track_ids": [str(uuid4())],
            },
            _CTX,
        )
        assert result["details"]["position"] is None

    async def test_add_without_track_ids_is_actionable(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="track_ids"):
            await playlists_write.handle_manage_playlist_entries(
                {"operation": "add", "playlist_id": str(uuid4()), "track_ids": []},
                _CTX,
            )

    async def test_remove_proposes_pending_confirmation(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        pid, eid = uuid4(), uuid4()
        result = await playlists_write.handle_manage_playlist_entries(
            {"operation": "remove", "playlist_id": str(pid), "entry_ids": [str(eid)]},
            _CTX,
        )
        assert result["details"]["entry_ids"] == [str(eid)]

    async def test_reorder_proposes_full_list(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        pid, e1, e2 = uuid4(), uuid4(), uuid4()
        result = await playlists_write.handle_manage_playlist_entries(
            {
                "operation": "reorder",
                "playlist_id": str(pid),
                "entry_ids": [str(e1), str(e2)],
            },
            _CTX,
        )
        assert result["details"]["entry_ids"] == [str(e1), str(e2)]

    async def test_repair_needs_only_playlist_id(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        pid = uuid4()
        result = await playlists_write.handle_manage_playlist_entries(
            {"operation": "repair", "playlist_id": str(pid)}, _CTX
        )
        assert result["details"]["operation"] == "repair"
        assert result["details"]["playlist_id"] == str(pid)

    async def test_bad_playlist_uuid_is_actionable(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="playlist_id"):
            await playlists_write.handle_manage_playlist_entries(
                {"operation": "repair", "playlist_id": "not-a-uuid"}, _CTX
            )


class TestExecManagePlaylistEntries:
    async def test_add_commits_tracks_at_position(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pid, t1, t2 = uuid4(), uuid4(), uuid4()
        seen = _capture(
            monkeypatch,
            AddPlaylistTracksUseCase,
            AddPlaylistTracksResult(playlist=make_playlist(id=pid), added=2),
        )
        action = await _action(
            "add",
            {
                "operation": "add",
                "playlist_id": str(pid),
                "track_ids": [str(t1), str(t2)],
                "position": 2,
            },
            "manage_playlist_entries",
        )

        result = await playlists_write.exec_manage_playlist_entries(action, "user-7")

        command = seen["command"]
        assert isinstance(command, AddPlaylistTracksCommand)
        assert command.playlist_id == pid
        assert command.track_ids == [t1, t2]
        assert command.position == 2
        assert command.user_id == "user-7"
        assert seen["user_id"] == "user-7"
        assert result["status"] == "confirmed"
        assert result["added"] == 2

    async def test_remove_commits_the_proposed_entries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pid, eid = uuid4(), uuid4()
        seen = _capture(
            monkeypatch,
            RemovePlaylistEntriesUseCase,
            RemovePlaylistEntriesResult(playlist=make_playlist(id=pid), removed=1),
        )
        action = await _action(
            "remove",
            {
                "operation": "remove",
                "playlist_id": str(pid),
                "entry_ids": [str(eid)],
            },
            "manage_playlist_entries",
        )

        result = await playlists_write.exec_manage_playlist_entries(action, "user-7")

        command = seen["command"]
        assert isinstance(command, RemovePlaylistEntriesCommand)
        assert command.playlist_id == pid
        assert command.entry_ids == [eid]
        assert command.user_id == "user-7"
        assert result["status"] == "confirmed"
        assert result["removed"] == 1

    async def test_reorder_commits_the_full_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Fixed ids, proposed out of sorted order, so a sort or reverse is caught.
        pid = uuid4()
        e1, e2, e3 = UUID(int=1), UUID(int=2), UUID(int=3)
        seen = _capture(
            monkeypatch,
            ReorderPlaylistEntriesUseCase,
            ReorderPlaylistEntriesResult(playlist=make_playlist(id=pid)),
        )
        action = await _action(
            "reorder",
            {
                "operation": "reorder",
                "playlist_id": str(pid),
                "entry_ids": [str(e3), str(e1), str(e2)],
            },
            "manage_playlist_entries",
        )

        result = await playlists_write.exec_manage_playlist_entries(action, "user-7")

        command = seen["command"]
        assert isinstance(command, ReorderPlaylistEntriesCommand)
        assert command.playlist_id == pid
        assert command.entry_ids == [e3, e1, e2]
        assert command.user_id == "user-7"
        assert result["status"] == "confirmed"
        assert result["operation"] == "reorder"

    async def test_repair_commits_through_use_case(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            _common,
            "execute_use_case",
            _runner_returning(
                RepairUnresolvedEntriesResult(repaired=3, still_unresolved=1)
            ),
        )
        action = await _action(
            "repair",
            {"operation": "repair", "playlist_id": str(uuid4())},
            "manage_playlist_entries",
        )

        result = await playlists_write.exec_manage_playlist_entries(action, "default")

        assert result["status"] == "confirmed"
        assert result["repaired"] == 3
        assert result["still_unresolved"] == 1

    async def test_stale_entry_at_confirm_is_actionable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            _common,
            "execute_use_case",
            _raising_runner(NotFoundError("stale entry")),
        )
        action = await _action(
            "remove",
            {
                "operation": "remove",
                "playlist_id": str(uuid4()),
                "entry_ids": [str(uuid4())],
            },
            "manage_playlist_entries",
        )

        with pytest.raises(ToolExecutionError, match="no longer exists"):
            await playlists_write.exec_manage_playlist_entries(action, "default")

    async def test_malformed_entry_id_list_is_actionable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A details payload whose id list did not survive as a list must fail
        # with a corrective error, never reach the use case.
        monkeypatch.setattr(
            _common, "execute_use_case", _raising_runner(AssertionError("not reached"))
        )
        action = await _action(
            "remove",
            {
                "operation": "remove",
                "playlist_id": str(uuid4()),
                "entry_ids": "not-a-list",
            },
            "manage_playlist_entries",
        )

        with pytest.raises(ToolExecutionError, match="non-empty list of UUID strings"):
            await playlists_write.exec_manage_playlist_entries(action, "default")

    async def test_malformed_playlist_id_is_actionable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            _common, "execute_use_case", _raising_runner(AssertionError("not reached"))
        )
        action = await _action(
            "repair",
            {"operation": "repair", "playlist_id": "not-a-uuid"},
            "manage_playlist_entries",
        )

        with pytest.raises(ToolExecutionError, match="must be a UUID string"):
            await playlists_write.exec_manage_playlist_entries(action, "default")
