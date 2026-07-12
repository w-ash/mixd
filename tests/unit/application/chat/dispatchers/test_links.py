"""Unit tests for the ``query_playlist_links`` chat dispatcher.

Each test monkeypatches ``execute_use_case`` on the module under test with a
fake async runner returning the domain Result. Coverage: the list-mode
projection, the preview-sync projection including verbatim ``confirm_token``
pass-through, user-data wrapping of connector/playlist names in ``<user_data>``
tags, and the missing-discriminator-field edges.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.application.chat.dispatchers import links
from src.application.chat.protocols import ToolContext
from src.application.chat.user_data import wrap
from src.application.use_cases.list_playlist_links import ListPlaylistLinksResult
from src.application.use_cases.preview_playlist_sync import (
    PreviewPlaylistSyncCommand,
    PreviewPlaylistSyncResult,
    PreviewPlaylistSyncUseCase,
)
from src.domain.entities.playlist_link import (
    PlaylistLink,
    SyncDirection,
    SyncStatus,
)
from src.domain.exceptions import ToolExecutionError

_CTX = ToolContext(user_id="default")


def _fake_runner(result: object):
    async def _run(factory: object, user_id: str | None = None):  # runner signature
        return result

    return _run


def _make_link() -> PlaylistLink:
    return PlaylistLink(
        playlist_id=uuid4(),
        connector_name="spotify",
        connector_playlist_identifier="spotify:pl:1",
        connector_playlist_name="My Mix",
        sync_direction=SyncDirection.PULL,
        sync_status=SyncStatus.SYNCED,
        last_synced=datetime(2024, 1, 1, tzinfo=UTC),
        last_sync_tracks_added=3,
        last_sync_tracks_removed=1,
    )


class TestListMode:
    async def test_lists_links_for_playlist(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        link = _make_link()
        playlist_id = uuid4()
        monkeypatch.setattr(
            links,
            "execute_use_case",
            _fake_runner(ListPlaylistLinksResult(links=[link])),
        )

        result = await links.handle_query_playlist_links(
            {"mode": "list", "playlist_id": str(playlist_id)}, _CTX
        )

        assert result["mode"] == "list"
        assert result["playlist_id"] == str(playlist_id)
        projected = result["links"][0]
        assert projected["link_id"] == str(link.id)
        assert projected["sync_direction"] == "pull"
        assert projected["sync_status"] == "synced"
        assert projected["connector_playlist_name"] == wrap("My Mix")

    async def test_list_requires_playlist_id(self) -> None:
        with pytest.raises(ToolExecutionError, match="playlist_id"):
            await links.handle_query_playlist_links({"mode": "list"}, _CTX)


class TestPreviewSyncMode:
    async def test_preview_passes_confirm_token_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        preview = PreviewPlaylistSyncResult(
            tracks_to_add=5,
            tracks_to_remove=2,
            tracks_unchanged=10,
            direction=SyncDirection.PUSH,
            connector_name="spotify",
            playlist_name="Weekend Vibes",
            confirm_token="tok-abc-123",
        )
        monkeypatch.setattr(links, "execute_use_case", _fake_runner(preview))

        result = await links.handle_query_playlist_links(
            {"mode": "preview_sync", "link_id": str(uuid4())}, _CTX
        )

        assert result["mode"] == "preview_sync"
        assert result["tracks_to_add"] == 5
        assert result["direction"] == "push"
        # The staleness token must survive verbatim — the sync write tool needs it.
        assert result["confirm_token"] == "tok-abc-123"
        assert result["playlist_name"] == wrap("Weekend Vibes")

    async def test_direction_override_is_forwarded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        async def _fake_execute(
            self: object, command: PreviewPlaylistSyncCommand, uow: object
        ) -> PreviewPlaylistSyncResult:
            captured["direction"] = command.direction_override
            return PreviewPlaylistSyncResult(confirm_token="t")

        # Patch the use case's execute (slots class → patch on the class), and
        # let the fake runner drive the dispatcher's real factory into it.
        monkeypatch.setattr(PreviewPlaylistSyncUseCase, "execute", _fake_execute)

        async def _run(factory: object, user_id: str | None = None):
            return await factory(object())  # runs the lambda the dispatcher built

        monkeypatch.setattr(links, "execute_use_case", _run)

        await links.handle_query_playlist_links(
            {
                "mode": "preview_sync",
                "link_id": str(uuid4()),
                "direction_override": "pull",
            },
            _CTX,
        )

        assert captured["direction"] == SyncDirection.PULL

    async def test_preview_requires_link_id(self) -> None:
        with pytest.raises(ToolExecutionError, match="link_id"):
            await links.handle_query_playlist_links({"mode": "preview_sync"}, _CTX)
