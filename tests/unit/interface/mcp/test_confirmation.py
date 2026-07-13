"""Two-phase confirmation wrapper for MCP write tools.

Drives ``handle_write_call`` through a real write dispatcher's (DB-free) propose
path and the real ``pending_action_store``, monkeypatching only the final commit
(``execute_confirmed_action`` — that's the DB-backed step, already covered by the
chat suite). Locks the plan's guarantees: preview never mutates, confirm commits
once, expired token re-previews, args drift is rejected.
"""

from uuid import UUID, uuid4

import pytest

from src.application.chat.pending_actions import PendingActionStore
from src.application.chat.protocols import ToolContext
from src.application.tools.registry import _SPECS_BY_NAME
from src.domain.entities.shared import JsonValue
from src.domain.exceptions import ToolExecutionError
from src.interface.mcp import confirmation

_CTX = ToolContext(user_id="default")
_SPEC = _SPECS_BY_NAME["manage_tags"]


def _batch_tag_args(tag: str = "jazz", n: int = 3) -> dict[str, JsonValue]:
    return {
        "operation": "batch_tag",
        "track_ids": [str(uuid4()) for _ in range(n)],
        "tag": tag,
    }


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> PendingActionStore:
    """A fresh store shared by both the propose (via _common) and claim bindings."""
    fresh = PendingActionStore()
    monkeypatch.setattr(confirmation, "pending_action_store", fresh)
    monkeypatch.setattr(
        "src.application.chat.dispatchers._common.pending_action_store", fresh
    )
    return fresh


@pytest.fixture
def committed(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Stub the DB-backed commit; record the claimed action it received."""
    seen: list[object] = []

    async def _fake_commit(action: object, user_id: str) -> JsonValue:
        seen.append(action)
        return {"status": "confirmed", "description": "done"}

    monkeypatch.setattr(confirmation, "execute_confirmed_action", _fake_commit)
    return seen


class TestPreview:
    async def test_first_call_previews_and_stores_without_committing(
        self, store: PendingActionStore, committed: list[object]
    ) -> None:
        result = await confirmation.handle_write_call(_SPEC, _batch_tag_args(), _CTX)

        assert isinstance(result, dict)
        assert result["status"] == "needs_confirmation"
        assert result["confirm_token"]
        assert "preview" in result
        # Stored for confirmation, and nothing committed.
        assert store.claim(UUID(result["confirm_token"]), "default")
        assert committed == []


class TestCommit:
    async def test_confirm_with_valid_token_commits_once(
        self, store: PendingActionStore, committed: list[object]
    ) -> None:
        args = _batch_tag_args()
        preview = await confirmation.handle_write_call(_SPEC, dict(args), _CTX)
        token = preview["confirm_token"]  # type: ignore[index]

        result = await confirmation.handle_write_call(
            _SPEC, {**args, "confirm": True, "confirm_token": token}, _CTX
        )
        assert isinstance(result, dict)
        assert result["status"] == "confirmed"
        assert len(committed) == 1

    async def test_confirm_true_without_token_errors(
        self, store: PendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="confirm_token"):
            await confirmation.handle_write_call(
                _SPEC, {**_batch_tag_args(), "confirm": True}, _CTX
            )


class TestExpiredAndDrift:
    async def test_expired_or_unknown_token_re_previews(
        self, store: PendingActionStore, committed: list[object]
    ) -> None:
        # A well-formed but unknown token → fresh preview, never a stale commit.
        result = await confirmation.handle_write_call(
            _SPEC,
            {**_batch_tag_args(), "confirm": True, "confirm_token": str(uuid4())},
            _CTX,
        )
        assert isinstance(result, dict)
        assert result["status"] == "needs_confirmation"
        assert committed == []

    async def test_malformed_token_re_previews(
        self, store: PendingActionStore, committed: list[object]
    ) -> None:
        result = await confirmation.handle_write_call(
            _SPEC,
            {**_batch_tag_args(), "confirm": True, "confirm_token": "not-a-uuid"},
            _CTX,
        )
        assert isinstance(result, dict)
        assert result["status"] == "needs_confirmation"
        assert committed == []

    async def test_args_drift_is_rejected(
        self, store: PendingActionStore, committed: list[object]
    ) -> None:
        preview = await confirmation.handle_write_call(_SPEC, _batch_tag_args(), _CTX)
        token = preview["confirm_token"]  # type: ignore[index]

        # Confirm with DIFFERENT args than were previewed.
        drifted = _batch_tag_args(tag="rock")
        with pytest.raises(ToolExecutionError, match="Arguments changed"):
            await confirmation.handle_write_call(
                _SPEC, {**drifted, "confirm": True, "confirm_token": token}, _CTX
            )
        assert committed == []
