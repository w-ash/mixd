"""Unit tests for the ``query_playlists`` chat dispatcher.

Each test monkeypatches ``execute_use_case`` on the module under test with a
fake async runner that returns queued domain Results in call order (the
canonical-detail scope runs two use cases per call). Assertions cover the
projected shape, the user-data wrapping of user-originated names/titles in
``<user_data>`` tags, and the not-found / missing-field edges.
"""

from uuid import uuid4

import pytest

from src.application.chat.dispatchers import playlists
from src.application.chat.protocols import ToolContext
from src.application.chat.user_data import wrap
from src.application.use_cases.list_connector_playlists import (
    ConnectorPlaylistView,
    ListConnectorPlaylistsResult,
)
from src.application.use_cases.list_playlists import ListPlaylistsResult
from src.application.use_cases.read_canonical_playlist import (
    ReadCanonicalPlaylistResult,
    ReadPlaylistTracksPageResult,
)
from src.domain.entities.playlist import PlaylistEntry
from src.domain.exceptions import ToolExecutionError
from tests.fixtures import make_playlist, make_track

_CTX = ToolContext(user_id="default")


def _fake_runner(*results: object):
    """Async runner returning the queued Results in call order."""
    queue = list(results)

    async def _run(factory: object, user_id: str | None = None):  # runner signature
        return queue.pop(0)

    return _run


class TestCanonicalListing:
    async def test_lists_playlists_compactly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pl = make_playlist(name="Road Trip")
        monkeypatch.setattr(
            playlists,
            "execute_use_case",
            _fake_runner(ListPlaylistsResult(playlists=[pl], total_count=1)),
        )

        result = await playlists.handle_query_playlists({}, _CTX)

        assert result["source"] == "canonical"
        assert result["total_count"] == 1
        assert result["playlists"][0]["playlist_id"] == str(pl.id)

    async def test_playlist_name_is_marked_user_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pl = make_playlist(name="Road Trip")
        monkeypatch.setattr(
            playlists,
            "execute_use_case",
            _fake_runner(ListPlaylistsResult(playlists=[pl], total_count=1)),
        )

        result = await playlists.handle_query_playlists({"source": "canonical"}, _CTX)

        name = result["playlists"][0]["name"]
        assert name == wrap("Road Trip")


class TestCanonicalDetail:
    async def test_detail_returns_playlist_and_tracks_page(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        track = make_track(title="Song One")
        pl = make_playlist(name="Detail", tracks=[track])
        entry = PlaylistEntry(track=track)
        monkeypatch.setattr(
            playlists,
            "execute_use_case",
            _fake_runner(
                ReadCanonicalPlaylistResult(playlist=pl),
                ReadPlaylistTracksPageResult(
                    entries=[entry], total=1, limit=50, offset=0
                ),
            ),
        )

        result = await playlists.handle_query_playlists(
            {"playlist_id": str(pl.id)}, _CTX
        )

        assert result["playlist"]["playlist_id"] == str(pl.id)
        assert result["total"] == 1
        assert result["tracks"][0]["position"] == 0
        assert result["tracks"][0]["track"]["title"] == wrap("Song One")

    async def test_offset_positions_entries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pl = make_playlist()
        entry = PlaylistEntry(track=make_track())
        monkeypatch.setattr(
            playlists,
            "execute_use_case",
            _fake_runner(
                ReadCanonicalPlaylistResult(playlist=pl),
                ReadPlaylistTracksPageResult(
                    entries=[entry], total=11, limit=5, offset=10
                ),
            ),
        )

        result = await playlists.handle_query_playlists(
            {"playlist_id": str(pl.id), "limit": 5, "offset": 10}, _CTX
        )

        assert result["tracks"][0]["position"] == 10

    async def test_missing_playlist_is_actionable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # ReadCanonicalPlaylist returns None (does not raise) on a miss.
        monkeypatch.setattr(
            playlists,
            "execute_use_case",
            _fake_runner(ReadCanonicalPlaylistResult(playlist=None)),
        )

        result = await playlists.handle_query_playlists(
            {"playlist_id": str(uuid4())}, _CTX
        )

        assert result["playlist"] is None
        assert "query_playlists" in result["message"]


class TestConnectorSource:
    def _view(self) -> ConnectorPlaylistView:
        return ConnectorPlaylistView(
            connector_playlist_identifier="spotify:pl:1",
            connector_playlist_db_id=uuid4(),
            name="Discover Weekly",
            description="Fresh picks",
            owner="spotify",
            image_url=None,
            track_count=30,
            snapshot_id="snap",
            collaborative=False,
            is_public=True,
            import_status="not_imported",
        )

    async def test_lists_connector_playlists_with_import_status(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            playlists,
            "execute_use_case",
            _fake_runner(
                ListConnectorPlaylistsResult(playlists=[self._view()], from_cache=True)
            ),
        )

        result = await playlists.handle_query_playlists(
            {"source": "connector", "connector": "spotify"}, _CTX
        )

        assert result["source"] == "connector"
        assert result["connector"] == "spotify"
        assert result["from_cache"] is True
        entry = result["playlists"][0]
        assert entry["import_status"] == "not_imported"
        assert entry["name"] == wrap("Discover Weekly")

    async def test_connector_source_requires_connector_field(self) -> None:
        with pytest.raises(ToolExecutionError, match="connector"):
            await playlists.handle_query_playlists({"source": "connector"}, _CTX)
