"""Unit tests for the ``merge_tracks`` two-phase write dispatcher.

``handle_merge_tracks`` proposes (stores a destructive pending action) and
``exec_merge_tracks`` commits through the merge-and-fetch-details use case. The
pending-action store is swapped for a fresh instance per test so proposals don't
leak, and ``execute_use_case`` is monkeypatched on the module under test so the
commit path never touches a database.
"""

from uuid import UUID, uuid4

import pytest

from src.application.chat.dispatchers import _common, tracks_write
from src.application.chat.pending_actions import PendingAction, PendingActionStore
from src.application.chat.protocols import ToolContext
from src.application.use_cases.get_track_details import PlaySummary, TrackDetailsResult
from src.domain.exceptions import NotFoundError, ToolExecutionError
from tests.fixtures import make_track

_CTX = ToolContext(user_id="default")


@pytest.fixture
def fresh_store(monkeypatch: pytest.MonkeyPatch) -> PendingActionStore:
    store = PendingActionStore()
    monkeypatch.setattr(_common, "pending_action_store", store)
    return store


def _fake_use_case_runner(result: object):
    async def _run(factory, user_id: str | None = None):  # matches runner signature
        return result

    return _run


def _details_result(track_id: UUID) -> TrackDetailsResult:
    return TrackDetailsResult(
        track=make_track(id=track_id, title="Winner"),
        connector_mappings=[],
        like_status={},
        play_summary=PlaySummary(total_plays=0, first_played=None, last_played=None),
        playlists=[],
    )


class TestMergeTracksPropose:
    async def test_proposes_destructive_pending_confirmation(
        self, fresh_store: PendingActionStore
    ) -> None:
        winner_id, loser_id = uuid4(), uuid4()

        result = await tracks_write.handle_merge_tracks(
            {"winner_id": str(winner_id), "loser_id": str(loser_id)}, _CTX
        )

        assert result["status"] == "pending_confirmation"
        details = result["details"]
        assert details["operation"] == "merge"
        assert details["winner_id"] == str(winner_id)
        assert details["loser_id"] == str(loser_id)
        # Destructive metadata the confirmation card renders.
        assert details["severity"] == "destructive"
        assert "cease to exist" in details["warning"]
        assert details["changes"]  # non-empty human-readable before/after lines
        assert str(loser_id) in result["description"]

        # The proposal is claimable by its owner — i.e. actually stored.
        action = fresh_store.claim(UUID(result["action_id"]), "default")
        assert action.tool_name == "merge_tracks"

    async def test_bad_uuid_stores_nothing(
        self, fresh_store: PendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="winner_id"):
            await tracks_write.handle_merge_tracks(
                {"winner_id": "not-a-uuid", "loser_id": str(uuid4())}, _CTX
            )

    async def test_self_merge_rejected(self, fresh_store: PendingActionStore) -> None:
        same = str(uuid4())
        with pytest.raises(ToolExecutionError, match="different tracks"):
            await tracks_write.handle_merge_tracks(
                {"winner_id": same, "loser_id": same}, _CTX
            )


class TestExecMergeTracks:
    def _action(self, winner_id: UUID, loser_id: UUID) -> PendingAction:
        store = PendingActionStore()
        return store.create(
            user_id="default",
            tool_name="merge_tracks",
            tool_input={},
            description="Merge",
            details={
                "operation": "merge",
                "winner_id": str(winner_id),
                "loser_id": str(loser_id),
            },
        )

    async def test_commits_through_use_case(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        winner_id, loser_id = uuid4(), uuid4()
        monkeypatch.setattr(
            _common,
            "execute_use_case",
            _fake_use_case_runner(_details_result(winner_id)),
        )
        action = self._action(winner_id, loser_id)

        result = await tracks_write.exec_merge_tracks(action, "default")

        assert result["status"] == "confirmed"
        assert result["merged_track"]["track_id"] == str(winner_id)

    async def test_track_gone_at_confirm_is_actionable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _raise(factory, user_id: str | None = None):
            raise NotFoundError("gone")

        monkeypatch.setattr(_common, "execute_use_case", _raise)
        action = self._action(uuid4(), uuid4())

        with pytest.raises(ToolExecutionError, match="no longer exists"):
            await tracks_write.exec_merge_tracks(action, "default")
