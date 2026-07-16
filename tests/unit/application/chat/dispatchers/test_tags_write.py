"""Unit tests for the ``manage_tags`` write dispatcher (propose + commit).

The propose half stores a pending action (never mutates); the commit half runs
the use case behind a monkeypatched ``execute_use_case``. The pending-action
store is swapped for a fresh instance (patched on ``_common``, where
``propose_action`` reads it) so proposals never leak across tests.
"""

from uuid import UUID, uuid4

import pytest

from src.application.chat.dispatchers import _common, tags_write
from src.application.chat.pending_actions import PendingAction, PendingActionStore
from src.application.chat.protocols import ToolContext
from src.application.use_cases.batch_tag_tracks import BatchTagTracksResult
from src.application.use_cases.tag_track import TagTrackResult
from src.application.use_cases.tag_vocabulary import DeleteTagResult, RenameTagResult
from src.domain.exceptions import NotFoundError, ToolExecutionError

_CTX = ToolContext(user_id="default")


@pytest.fixture
def fresh_store(monkeypatch: pytest.MonkeyPatch) -> PendingActionStore:
    store = PendingActionStore()
    monkeypatch.setattr(_common, "pending_action_store", store)
    return store


def _fake_runner(result: object):
    async def _run(factory: object, user_id: str | None = None) -> object:
        return result

    return _run


def _pending(store: PendingActionStore, details: dict[str, object]) -> PendingAction:
    return store.create(
        user_id="default",
        tool_name="manage_tags",
        tool_input={},
        description="do it",
        details=details,
    )


class TestManageTagsPropose:
    async def test_tag_proposes_pending_confirmation(
        self, fresh_store: PendingActionStore
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
        action = fresh_store.claim(UUID(result["action_id"]), "default")
        assert action.tool_name == "manage_tags"
        assert action.details["tag"] == "mood:chill"

    async def test_batch_tag_counts_tracks(
        self, fresh_store: PendingActionStore
    ) -> None:
        ids = [str(uuid4()), str(uuid4())]
        result = await tags_write.handle_manage_tags(
            {"operation": "batch_tag", "track_ids": ids, "tag": "gym"}, _CTX
        )

        assert result["details"]["track_ids"] == ids
        assert "2 tracks" in result["description"]

    async def test_rename_maps_source_and_target(
        self, fresh_store: PendingActionStore
    ) -> None:
        result = await tags_write.handle_manage_tags(
            {"operation": "rename", "source_tag": "chil", "target_tag": "chill"}, _CTX
        )

        assert result["details"]["source_tag"] == "chil"
        assert result["details"]["target_tag"] == "chill"
        assert "severity" not in result["details"]  # rename is not destructive

    async def test_merge_is_destructive(self, fresh_store: PendingActionStore) -> None:
        result = await tags_write.handle_manage_tags(
            {"operation": "merge", "source_tag": "a", "target_tag": "b"}, _CTX
        )

        assert result["details"]["severity"] == "destructive"
        assert "collapses" in result["details"]["warning"]

    async def test_delete_is_destructive(self, fresh_store: PendingActionStore) -> None:
        result = await tags_write.handle_manage_tags(
            {"operation": "delete", "tag": "junk"}, _CTX
        )

        assert result["details"]["severity"] == "destructive"
        assert "removes all associations" in result["details"]["warning"]

    async def test_missing_track_id_rejected(
        self, fresh_store: PendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="track_id"):
            await tags_write.handle_manage_tags(
                {"operation": "tag", "tag": "mood:chill"}, _CTX
            )

    async def test_unknown_operation_rejected(
        self, fresh_store: PendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="operation"):
            await tags_write.handle_manage_tags({"operation": "obliterate"}, _CTX)


class TestExecManageTags:
    async def test_tag_commits_through_use_case(
        self, fresh_store: PendingActionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        track_id = uuid4()
        monkeypatch.setattr(
            _common,
            "execute_use_case",
            _fake_runner(
                TagTrackResult(track_id=track_id, tag="mood:chill", changed=True)
            ),
        )
        action = _pending(
            fresh_store,
            {"operation": "tag", "track_id": str(track_id), "tag": "mood:chill"},
        )

        result = await tags_write.exec_manage_tags(action, "default")

        assert isinstance(result, dict)
        assert result["status"] == "confirmed"
        assert result["operation"] == "tag"
        assert result["tag"] == "mood:chill"
        assert result["changed"] is True

    async def test_batch_tag_commits_through_use_case(
        self, fresh_store: PendingActionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            _common,
            "execute_use_case",
            _fake_runner(BatchTagTracksResult(tag="gym", requested=2, tagged=2)),
        )
        action = _pending(
            fresh_store,
            {
                "operation": "batch_tag",
                "track_ids": [str(uuid4()), str(uuid4())],
                "tag": "gym",
            },
        )

        result = await tags_write.exec_manage_tags(action, "default")

        assert result["requested"] == 2
        assert result["tagged"] == 2

    async def test_delete_commits_through_use_case(
        self, fresh_store: PendingActionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            _common,
            "execute_use_case",
            _fake_runner(DeleteTagResult(affected_count=7)),
        )
        action = _pending(fresh_store, {"operation": "delete", "tag": "junk"})

        result = await tags_write.exec_manage_tags(action, "default")

        assert result["affected_count"] == 7

    async def test_rename_commits_through_use_case(
        self, fresh_store: PendingActionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            _common,
            "execute_use_case",
            _fake_runner(RenameTagResult(affected_count=3)),
        )
        action = _pending(
            fresh_store,
            {"operation": "rename", "source_tag": "chil", "target_tag": "chill"},
        )

        result = await tags_write.exec_manage_tags(action, "default")

        assert result["source_tag"] == "chil"
        assert result["target_tag"] == "chill"
        assert result["affected_count"] == 3

    async def test_missing_track_at_commit_is_actionable(
        self, fresh_store: PendingActionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _raise(factory: object, user_id: str | None = None) -> object:
            raise NotFoundError("gone")

        monkeypatch.setattr(_common, "execute_use_case", _raise)
        action = _pending(
            fresh_store,
            {"operation": "tag", "track_id": str(uuid4()), "tag": "mood:chill"},
        )

        with pytest.raises(ToolExecutionError, match="no longer exists"):
            await tags_write.exec_manage_tags(action, "default")
