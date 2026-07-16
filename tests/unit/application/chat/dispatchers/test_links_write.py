"""Unit tests for the ``manage_playlist_link`` two-phase write dispatcher.

``handle_manage_playlist_link`` proposes and ``exec_manage_playlist_link``
commits through the create/update/delete link use cases. The pending-action
store is swapped for a fresh instance per test so proposals don't leak, and
``execute_use_case`` is monkeypatched on the module under test so the commit
path never touches a database.
"""

from uuid import UUID, uuid4

import pytest

from src.application.chat.dispatchers import _common, links_write
from src.application.chat.pending_actions import PendingAction
from src.application.chat.protocols import ToolContext
from src.application.use_cases.create_playlist_link import CreatePlaylistLinkResult
from src.application.use_cases.delete_playlist_link import DeletePlaylistLinkResult
from src.application.use_cases.update_playlist_link import UpdatePlaylistLinkResult
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection
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


def _make_link(link_id: UUID, direction: SyncDirection) -> PlaylistLink:
    return PlaylistLink(
        playlist_id=uuid4(),
        connector_name="spotify",
        connector_playlist_identifier="ext123",
        sync_direction=direction,
        id=link_id,
    )


class TestManagePlaylistLinkPropose:
    async def test_create_proposes_pending_confirmation(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        playlist_id = uuid4()
        result = await links_write.handle_manage_playlist_link(
            {
                "operation": "create",
                "playlist_id": str(playlist_id),
                "connector": "spotify",
                "identifier": "ext123",
            },
            _CTX,
        )

        assert result["status"] == "pending_confirmation"
        details = result["details"]
        assert details["operation"] == "create"
        assert details["playlist_id"] == str(playlist_id)
        assert details["direction"] == "pull"  # default
        assert details["changes"]

        action = await fresh_store.claim(UUID(result["action_id"]), "default")
        assert action.tool_name == "manage_playlist_link"

    async def test_delete_carries_moderate_warning_not_destructive(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        link_id = uuid4()
        result = await links_write.handle_manage_playlist_link(
            {"operation": "delete", "link_id": str(link_id)}, _CTX
        )

        details = result["details"]
        assert details["operation"] == "delete"
        assert "stay" in details["warning"]
        # Moderate, not destructive — no severity marker.
        assert "severity" not in details

    async def test_update_bad_direction_rejected(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="direction"):
            await links_write.handle_manage_playlist_link(
                {
                    "operation": "update",
                    "link_id": str(uuid4()),
                    "direction": "sideways",
                },
                _CTX,
            )

    async def test_create_missing_playlist_id_rejected(
        self, fresh_store: InMemoryPendingActionStore
    ) -> None:
        with pytest.raises(ToolExecutionError, match="playlist_id"):
            await links_write.handle_manage_playlist_link(
                {"operation": "create", "connector": "spotify", "identifier": "x"},
                _CTX,
            )


class TestExecManagePlaylistLink:
    async def _action(self, details: dict[str, object]) -> PendingAction:
        store = InMemoryPendingActionStore()
        return await store.create(
            user_id="default",
            tool_name="manage_playlist_link",
            tool_input={},
            description="Link op",
            details=details,
        )

    async def test_create_commits_and_projects_link(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        link_id = uuid4()
        monkeypatch.setattr(
            _common,
            "execute_use_case",
            _fake_runner(
                CreatePlaylistLinkResult(link=_make_link(link_id, SyncDirection.PUSH))
            ),
        )
        action = await self._action({
            "operation": "create",
            "playlist_id": str(uuid4()),
            "connector": "spotify",
            "identifier": "ext123",
            "direction": "push",
        })

        out = await links_write.exec_manage_playlist_link(action, "default")

        assert out["status"] == "confirmed"
        assert out["link"]["link_id"] == str(link_id)
        assert out["link"]["sync_direction"] == "push"
        assert out["link"]["connector_name"] == "spotify"

    async def test_update_commits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        link_id = uuid4()
        monkeypatch.setattr(
            _common,
            "execute_use_case",
            _fake_runner(
                UpdatePlaylistLinkResult(link=_make_link(link_id, SyncDirection.PULL))
            ),
        )
        action = await self._action({
            "operation": "update",
            "link_id": str(link_id),
            "direction": "pull",
        })

        out = await links_write.exec_manage_playlist_link(action, "default")

        assert out["status"] == "confirmed"
        assert out["link"]["sync_direction"] == "pull"

    async def test_delete_commits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        link_id = uuid4()
        monkeypatch.setattr(
            _common,
            "execute_use_case",
            _fake_runner(DeletePlaylistLinkResult(deleted=True)),
        )
        action = await self._action({"operation": "delete", "link_id": str(link_id)})

        out = await links_write.exec_manage_playlist_link(action, "default")

        assert out["status"] == "confirmed"
        assert out["deleted"] is True
        assert out["link_id"] == str(link_id)

    async def test_update_link_gone_is_actionable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _raise(factory: object, user_id: str | None = None) -> object:
            raise NotFoundError("gone")

        monkeypatch.setattr(_common, "execute_use_case", _raise)
        action = await self._action({
            "operation": "update",
            "link_id": str(uuid4()),
            "direction": "pull",
        })

        with pytest.raises(ToolExecutionError, match="no longer exists"):
            await links_write.exec_manage_playlist_link(action, "default")
