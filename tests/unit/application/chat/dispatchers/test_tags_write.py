"""Unit tests for the ``manage_tags`` write dispatcher (propose + commit).

The propose half stores a pending action (never mutates); the commit half runs
the real factory into a patched use-case ``execute`` so the test sees the
Command the dispatcher built. The pending-action
store is swapped for a fresh instance (patched on ``_common``, where
``propose_action`` reads it) so proposals never leak across tests.
"""

from uuid import UUID, uuid4

import pytest

from src.application.chat.dispatchers import _common, tags_write
from src.application.chat.pending_actions import PendingAction
from src.application.chat.protocols import ToolContext
from src.application.use_cases.batch_tag_tracks import (
    BatchTagTracksCommand,
    BatchTagTracksResult,
    BatchTagTracksUseCase,
)
from src.application.use_cases.tag_track import (
    TagTrackCommand,
    TagTrackResult,
    TagTrackUseCase,
)
from src.application.use_cases.tag_vocabulary import (
    DeleteTagCommand,
    DeleteTagResult,
    DeleteTagUseCase,
    MergeTagsCommand,
    MergeTagsResult,
    MergeTagsUseCase,
    RenameTagCommand,
    RenameTagResult,
    RenameTagUseCase,
)
from src.domain.exceptions import NotFoundError, ToolExecutionError
from tests.fixtures import InMemoryPendingActionStore

_CTX = ToolContext(user_id="default")


@pytest.fixture
def fresh_store(monkeypatch: pytest.MonkeyPatch) -> InMemoryPendingActionStore:
    store = InMemoryPendingActionStore()
    monkeypatch.setattr(_common, "pending_action_store", store)
    return store


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


async def _pending(
    store: InMemoryPendingActionStore, details: dict[str, object]
) -> PendingAction:
    return await store.create(
        user_id="default",
        tool_name="manage_tags",
        tool_input={},
        description="do it",
        details=details,
    )


class TestManageTagsPropose:
    async def test_tag_proposes_pending_confirmation(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        track_id = uuid4()
        result = await tags_write.handle_manage_tags(
            {"operation": "tag", "track_id": str(track_id), "tag": "mood:chill"}, _CTX
        )

        assert isinstance(result, dict)
        assert result["status"] == "pending_confirmation"
        assert result["details"]["operation"] == "tag"
        assert result["details"]["track_id"] == str(track_id)
        assert result["details"]["changes"]
        # Stored and claimable by its owner — i.e. actually persisted.
        action = await fresh_store.claim(UUID(result["action_id"]), "default")
        assert action.tool_name == "manage_tags"
        assert action.details["tag"] == "mood:chill"

    async def test_batch_tag_counts_tracks(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        ids = [str(uuid4()), str(uuid4())]
        result = await tags_write.handle_manage_tags(
            {"operation": "batch_tag", "track_ids": ids, "tag": "gym"}, _CTX
        )

        assert result["details"]["track_ids"] == ids
        assert "2 tracks" in result["description"]

    async def test_rename_maps_source_and_target(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        result = await tags_write.handle_manage_tags(
            {"operation": "rename", "source_tag": "chil", "target_tag": "chill"}, _CTX
        )

        assert result["details"]["source_tag"] == "chil"
        assert result["details"]["target_tag"] == "chill"
        assert "severity" not in result["details"]  # rename is not destructive

    async def test_merge_is_destructive(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        result = await tags_write.handle_manage_tags(
            {"operation": "merge", "source_tag": "a", "target_tag": "b"}, _CTX
        )

        assert result["details"]["severity"] == "destructive"
        assert "collapses" in result["details"]["warning"]

    async def test_delete_is_destructive(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        result = await tags_write.handle_manage_tags(
            {"operation": "delete", "tag": "junk"}, _CTX
        )

        assert result["details"]["severity"] == "destructive"
        assert "removes all associations" in result["details"]["warning"]

    async def test_missing_track_id_rejected(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="track_id"):
            await tags_write.handle_manage_tags(
                {"operation": "tag", "tag": "mood:chill"}, _CTX
            )

    async def test_unknown_operation_rejected(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="operation"):
            await tags_write.handle_manage_tags({"operation": "obliterate"}, _CTX)


class TestExecManageTags:
    async def test_tag_commits_through_use_case(
        self, fresh_store: InMemoryPendingActionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        track_id = uuid4()
        seen = _capture(
            monkeypatch,
            TagTrackUseCase,
            TagTrackResult(track_id=track_id, tag="mood:chill", changed=True),
        )
        action = await _pending(
            fresh_store,
            {"operation": "tag", "track_id": str(track_id), "tag": "mood:chill"},
        )

        result = await tags_write.exec_manage_tags(action, "user-7")

        command = seen["command"]
        assert isinstance(command, TagTrackCommand)
        assert command.track_id == track_id
        assert command.raw_tag == "mood:chill"
        # An assistant-made tag is recorded as a manual (user) choice.
        assert command.source == "manual"
        assert command.user_id == "user-7"
        assert seen["user_id"] == "user-7"
        assert result == {
            "status": "confirmed",
            "operation": "tag",
            "description": "do it",
            "tag": "mood:chill",
            "changed": True,
        }

    async def test_batch_tag_commits_every_track(
        self, fresh_store: InMemoryPendingActionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        t1, t2 = uuid4(), uuid4()
        seen = _capture(
            monkeypatch,
            BatchTagTracksUseCase,
            BatchTagTracksResult(tag="gym", requested=2, tagged=2),
        )
        action = await _pending(
            fresh_store,
            {"operation": "batch_tag", "track_ids": [str(t1), str(t2)], "tag": "gym"},
        )

        result = await tags_write.exec_manage_tags(action, "user-7")

        command = seen["command"]
        assert isinstance(command, BatchTagTracksCommand)
        assert command.track_ids == [t1, t2]
        assert command.raw_tag == "gym"
        assert command.source == "manual"
        assert command.user_id == "user-7"
        assert result["requested"] == 2
        assert result["tagged"] == 2

    async def test_delete_commits_the_named_tag(
        self, fresh_store: InMemoryPendingActionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _capture(
            monkeypatch, DeleteTagUseCase, DeleteTagResult(affected_count=7)
        )
        action = await _pending(fresh_store, {"operation": "delete", "tag": "junk"})

        result = await tags_write.exec_manage_tags(action, "user-7")

        command = seen["command"]
        assert isinstance(command, DeleteTagCommand)
        assert command.tag == "junk"
        assert command.user_id == "user-7"
        assert result["tag"] == "junk"
        assert result["affected_count"] == 7

    @pytest.mark.parametrize(
        ("operation", "use_case", "command_type", "result"),
        [
            ("rename", RenameTagUseCase, RenameTagCommand, RenameTagResult(3)),
            ("merge", MergeTagsUseCase, MergeTagsCommand, MergeTagsResult(3)),
        ],
    )
    async def test_rename_and_merge_commit_source_into_target(
        self,
        fresh_store: InMemoryPendingActionStore,
        monkeypatch: pytest.MonkeyPatch,
        operation: str,
        use_case: type,
        command_type: type,
        result: object,
    ) -> None:
        # Direction is the whole contract: merge collapses source INTO target.
        seen = _capture(monkeypatch, use_case, result)
        action = await _pending(
            fresh_store,
            {"operation": operation, "source_tag": "chil", "target_tag": "chill"},
        )

        out = await tags_write.exec_manage_tags(action, "user-7")

        command = seen["command"]
        assert isinstance(command, RenameTagCommand | MergeTagsCommand)
        assert type(command) is command_type
        assert command.source == "chil"
        assert command.target == "chill"
        assert command.user_id == "user-7"
        assert out["operation"] == operation
        assert out["source_tag"] == "chil"
        assert out["target_tag"] == "chill"
        assert out["affected_count"] == 3

    async def test_missing_track_at_commit_is_actionable(
        self, fresh_store: InMemoryPendingActionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _raise(factory: object, user_id: str | None = None) -> object:
            raise NotFoundError("gone")

        monkeypatch.setattr(_common, "execute_use_case", _raise)
        action = await _pending(
            fresh_store,
            {"operation": "tag", "track_id": str(uuid4()), "tag": "mood:chill"},
        )

        with pytest.raises(ToolExecutionError, match="no longer exists"):
            await tags_write.exec_manage_tags(action, "default")

    async def test_malformed_track_id_at_commit_is_actionable(
        self, fresh_store: InMemoryPendingActionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Details the executor cannot parse raise a corrective error rather
        # than a bare ValueError, and never reach the use case.
        async def _raise(factory: object, user_id: str | None = None) -> object:
            raise AssertionError("not reached")

        monkeypatch.setattr(_common, "execute_use_case", _raise)
        action = await _pending(
            fresh_store,
            {"operation": "tag", "track_id": "not-a-uuid", "tag": "mood:chill"},
        )

        with pytest.raises(ToolExecutionError, match="must be a UUID string"):
            await tags_write.exec_manage_tags(action, "default")

    async def test_malformed_batch_track_ids_at_commit_is_actionable(
        self, fresh_store: InMemoryPendingActionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _raise(factory: object, user_id: str | None = None) -> object:
            raise AssertionError("not reached")

        monkeypatch.setattr(_common, "execute_use_case", _raise)
        action = await _pending(
            fresh_store,
            {"operation": "batch_tag", "track_ids": "not-a-list", "tag": "gym"},
        )

        with pytest.raises(ToolExecutionError, match="non-empty list of UUID strings"):
            await tags_write.exec_manage_tags(action, "default")
