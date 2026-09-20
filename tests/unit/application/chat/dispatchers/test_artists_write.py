"""Unit tests for the ``favorite_artist`` write dispatcher (propose + commit).

Same shape as the other ``*_write`` tests: the propose half only stores a
pending action, and the commit half runs the use case behind a monkeypatched
``execute_use_case``.
"""

from uuid import UUID, uuid4

import pytest

from src.application.chat.dispatchers import _common, artists_write
from src.application.chat.pending_actions import PendingAction
from src.application.chat.protocols import ToolContext
from src.application.use_cases.favorite_artist import FavoriteArtistResult
from src.domain.exceptions import NotFoundError, ToolExecutionError
from tests.fixtures import InMemoryPendingActionStore

_CTX = ToolContext(user_id="default")


@pytest.fixture
def fresh_store(monkeypatch: pytest.MonkeyPatch) -> InMemoryPendingActionStore:
    store = InMemoryPendingActionStore()
    monkeypatch.setattr(_common, "pending_action_store", store)
    return store


def _fake_runner(result: object):
    async def _run(factory: object, user_id: str | None = None) -> object:
        return result

    return _run


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
    async def test_commits_through_use_case(
        self, fresh_store: InMemoryPendingActionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        artist_id = uuid4()
        monkeypatch.setattr(
            _common,
            "execute_use_case",
            _fake_runner(
                FavoriteArtistResult(
                    artist_id=artist_id, is_favorited=True, changed=True
                )
            ),
        )
        action = await _pending(
            fresh_store, {"artist_id": str(artist_id), "is_favorited": True}
        )

        result = await artists_write.exec_favorite_artist(action, "default")

        assert isinstance(result, dict)
        assert result["status"] == "confirmed"
        assert result["operation"] == "favorite_artist"
        assert result["changed"] is True

    async def test_repeat_reports_unchanged(
        self, fresh_store: InMemoryPendingActionStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        artist_id = uuid4()
        monkeypatch.setattr(
            _common,
            "execute_use_case",
            _fake_runner(
                FavoriteArtistResult(
                    artist_id=artist_id, is_favorited=True, changed=False
                )
            ),
        )
        action = await _pending(
            fresh_store, {"artist_id": str(artist_id), "is_favorited": True}
        )

        result = await artists_write.exec_favorite_artist(action, "default")

        assert result["changed"] is False

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
