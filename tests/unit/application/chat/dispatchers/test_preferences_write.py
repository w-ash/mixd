"""Unit tests for the ``set_preferences`` write dispatcher (propose + commit).

The propose half stores a pending action (never mutates); the commit half runs
the use case behind a monkeypatched ``execute_use_case``. The pending-action
store is swapped for a fresh instance (patched on ``_common``, where
``propose_action`` reads it) so proposals never leak across tests.
"""

from uuid import UUID, uuid4

import pytest

from src.application.chat.dispatchers import _common, preferences_write
from src.application.chat.pending_actions import PendingAction, PendingActionStore
from src.application.chat.protocols import ToolContext
from src.application.use_cases.set_track_preference import SetTrackPreferenceResult
from src.application.use_cases.sync_preferences_from_likes import (
    SyncPreferencesFromLikesResult,
)
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
        tool_name="set_preferences",
        tool_input={},
        description="do it",
        details=details,
    )


class TestSetPreferencesPropose:
    async def test_set_proposes_pending_confirmation(
        self, fresh_store: PendingActionStore
    ) -> None:
        track_id = uuid4()
        result = await preferences_write.handle_set_preferences(
            {"operation": "set", "track_id": str(track_id), "state": "star"}, _CTX
        )

        assert isinstance(result, dict)
        assert result["status"] == "pending_confirmation"
        assert result["details"]["operation"] == "set"
        assert result["details"]["state"] == "star"
        assert "star" in result["description"]
        action = fresh_store.claim(UUID(result["action_id"]), "default")
        assert action.details["track_id"] == str(track_id)

    async def test_set_without_state_clears(
        self, fresh_store: PendingActionStore
    ) -> None:
        result = await preferences_write.handle_set_preferences(
            {"operation": "set", "track_id": str(uuid4())}, _CTX
        )

        assert result["details"]["state"] is None
        assert "Clear" in result["description"]

    async def test_sync_from_likes_needs_nothing(
        self, fresh_store: PendingActionStore
    ) -> None:
        result = await preferences_write.handle_set_preferences(
            {"operation": "sync_from_likes"}, _CTX
        )

        assert result["status"] == "pending_confirmation"
        assert result["details"]["operation"] == "sync_from_likes"
        assert result["details"]["changes"]

    async def test_invalid_state_rejected(
        self, fresh_store: PendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="hmm, nah, yah, star"):
            await preferences_write.handle_set_preferences(
                {"operation": "set", "track_id": str(uuid4()), "state": "love"}, _CTX
            )

    async def test_missing_track_id_rejected(
        self, fresh_store: PendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="track_id"):
            await preferences_write.handle_set_preferences(
                {"operation": "set", "state": "yah"}, _CTX
            )


class TestExecSetPreferences:
    async def test_set_commits_through_use_case(
        self, fresh_store: PendingActionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        track_id = uuid4()
        monkeypatch.setattr(
            preferences_write,
            "execute_use_case",
            _fake_runner(
                SetTrackPreferenceResult(track_id=track_id, state="star", changed=True)
            ),
        )
        action = _pending(
            fresh_store,
            {"operation": "set", "track_id": str(track_id), "state": "star"},
        )

        result = await preferences_write.exec_set_preferences(action, "default")

        assert isinstance(result, dict)
        assert result["status"] == "confirmed"
        assert result["state"] == "star"
        assert result["changed"] is True

    async def test_clear_commits_with_none_state(
        self, fresh_store: PendingActionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        track_id = uuid4()
        monkeypatch.setattr(
            preferences_write,
            "execute_use_case",
            _fake_runner(
                SetTrackPreferenceResult(track_id=track_id, state=None, changed=True)
            ),
        )
        action = _pending(
            fresh_store,
            {"operation": "set", "track_id": str(track_id), "state": None},
        )

        result = await preferences_write.exec_set_preferences(action, "default")

        assert result["state"] is None

    async def test_sync_commits_through_use_case(
        self, fresh_store: PendingActionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            preferences_write,
            "execute_use_case",
            _fake_runner(
                SyncPreferencesFromLikesResult(created=5, upgraded=2, skipped=1)
            ),
        )
        action = _pending(fresh_store, {"operation": "sync_from_likes"})

        result = await preferences_write.exec_set_preferences(action, "default")

        assert result["created"] == 5
        assert result["upgraded"] == 2
        assert result["skipped"] == 1

    async def test_missing_track_at_commit_is_actionable(
        self, fresh_store: PendingActionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _raise(factory: object, user_id: str | None = None) -> object:
            raise NotFoundError("gone")

        monkeypatch.setattr(preferences_write, "execute_use_case", _raise)
        action = _pending(
            fresh_store,
            {"operation": "set", "track_id": str(uuid4()), "state": "yah"},
        )

        with pytest.raises(ToolExecutionError, match="no longer exists"):
            await preferences_write.exec_set_preferences(action, "default")
