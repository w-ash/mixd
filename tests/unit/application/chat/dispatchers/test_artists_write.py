"""Unit tests for the ``favorite_artist`` write dispatcher (propose + commit).

Same shape as the other ``*_write`` tests: the propose half only stores a
pending action, and the commit half runs the real factory with the use case's
``execute`` patched, so the test sees the Command the dispatcher built.
"""

from uuid import UUID, uuid4

import pytest

from src.application.chat.dispatchers import _common, artists_write
from src.application.chat.pending_actions import PendingAction
from src.application.chat.protocols import ToolContext
from src.application.use_cases.favorite_artist import (
    FavoriteArtistCommand,
    FavoriteArtistResult,
    FavoriteArtistUseCase,
)
from src.domain.exceptions import NotFoundError, ToolExecutionError
from tests.fixtures import InMemoryPendingActionStore

_CTX = ToolContext(user_id="default")


@pytest.fixture
def fresh_store(monkeypatch: pytest.MonkeyPatch) -> InMemoryPendingActionStore:
    store = InMemoryPendingActionStore()
    monkeypatch.setattr(_common, "pending_action_store", store)
    return store


def _capture(monkeypatch: pytest.MonkeyPatch, result: object) -> dict[str, object]:
    """Run the dispatcher's real factory; record the Command and runner user_id."""
    seen: dict[str, object] = {}

    async def _execute(self: object, command: object, uow: object) -> object:
        seen["command"] = command
        return result

    async def _run(factory, user_id: str | None = None):  # runner signature
        seen["user_id"] = user_id
        return await factory(object())

    monkeypatch.setattr(FavoriteArtistUseCase, "execute", _execute)
    monkeypatch.setattr(_common, "execute_use_case", _run)
    return seen


def _failing_runner(error: Exception):
    async def _run(factory: object, user_id: str | None = None) -> object:
        raise error

    return _run


async def _pending(
    store: InMemoryPendingActionStore, details: dict[str, object]
) -> PendingAction:
    return await store.create(
        user_id="default",
        tool_name="favorite_artist",
        tool_input={},
        description="do it",
        details=details,
    )


class TestFavoriteArtistPropose:
    async def test_proposes_pending_confirmation(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        artist_id = uuid4()

        result = await artists_write.handle_favorite_artist(
            {"artist_id": str(artist_id)}, _CTX
        )

        assert isinstance(result, dict)
        assert result["status"] == "pending_confirmation"
        assert result["details"]["is_favorited"] is True
        action = await fresh_store.claim(UUID(result["action_id"]), "default")
        assert action.details["artist_id"] == str(artist_id)

    async def test_unfavorite_is_carried_on_details(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        result = await artists_write.handle_favorite_artist(
            {"artist_id": str(uuid4()), "is_favorited": False}, _CTX
        )

        assert result["details"]["is_favorited"] is False
        assert "Unfavorite" in result["description"]

    async def test_missing_artist_id_rejected(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="artist_id"):
            await artists_write.handle_favorite_artist({}, _CTX)


class TestExecFavoriteArtist:
    @pytest.mark.parametrize(
        ("is_favorited", "changed"),
        [(True, True), (False, False)],
        ids=["favorite", "repeat-unfavorite-is-noop"],
    )
    async def test_commits_the_proposed_state_through_use_case(
        self,
        fresh_store: InMemoryPendingActionStore,
        monkeypatch: pytest.MonkeyPatch,
        is_favorited: bool,
        changed: bool,
    ) -> None:
        artist_id = uuid4()
        seen = _capture(
            monkeypatch,
            FavoriteArtistResult(
                artist_id=artist_id, is_favorited=is_favorited, changed=changed
            ),
        )
        action = await _pending(
            fresh_store, {"artist_id": str(artist_id), "is_favorited": is_favorited}
        )

        result = await artists_write.exec_favorite_artist(action, "user-7")

        command = seen["command"]
        assert isinstance(command, FavoriteArtistCommand)
        assert command.artist_id == artist_id
        assert command.is_favorited is is_favorited
        assert command.user_id == "user-7"
        assert seen["user_id"] == "user-7"
        assert result == {
            "status": "confirmed",
            "operation": "favorite_artist",
            "description": "do it",
            "artist_id": str(artist_id),
            "is_favorited": is_favorited,
            "changed": changed,
        }

    async def test_deleted_artist_surfaces_as_tool_error(
        self, fresh_store: InMemoryPendingActionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            _common,
            "execute_use_case",
            _failing_runner(NotFoundError("gone")),
        )
        action = await _pending(
            fresh_store, {"artist_id": str(uuid4()), "is_favorited": True}
        )

        with pytest.raises(ToolExecutionError, match="no longer exists"):
            await artists_write.exec_favorite_artist(action, "default")
