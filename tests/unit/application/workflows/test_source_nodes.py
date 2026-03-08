"""Characterization tests for source nodes.

Locks down current behavior of playlist_source, source_liked_tracks,
and source_played_tracks before workflow cleanup.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistResult,
)
from src.application.use_cases.get_liked_tracks import GetLikedTracksCommand
from src.application.use_cases.update_canonical_playlist import (
    UpdateCanonicalPlaylistResult,
)
from src.application.workflows.source_nodes import (
    _build_source_tracklist,
    playlist_source,
    source_liked_tracks,
    source_played_tracks,
)
from src.domain.entities.track import Artist, Track, TrackList
from tests.fixtures import make_connector_playlist, make_connector_playlist_item


@pytest.fixture
def sample_tracks():
    """Tracks with IDs for source mapping."""
    return [
        Track(id=1, title="Song A", artists=[Artist(name="Artist 1")]),
        Track(id=2, title="Song B", artists=[Artist(name="Artist 2")]),
    ]


@pytest.fixture
def mock_workflow_context(sample_tracks):
    """Build a mock workflow context dict that playlist_source expects."""
    playlist = MagicMock()
    playlist.id = "pl-1"
    playlist.name = "Test Playlist"
    playlist.tracks = sample_tracks

    read_result = MagicMock()
    read_result.playlist = playlist

    wf_ctx = AsyncMock()
    wf_ctx.execute_use_case = AsyncMock(return_value=read_result)
    wf_ctx.use_cases = MagicMock()

    return {
        "workflow_context": wf_ctx,
    }


class TestBuildSourceTracklist:
    """Tests for _build_source_tracklist helper."""

    def test_builds_tracklist_with_source_metadata(self, sample_tracks):
        """Helper builds TrackList with correct track_sources metadata."""
        result = _build_source_tracklist(
            sample_tracks, "My Playlist", "spotify", "sp-123"
        )

        assert isinstance(result, TrackList)
        assert len(result.tracks) == 2
        assert result.metadata["operation"] == "playlist_source"
        assert 1 in result.metadata["track_sources"]
        assert result.metadata["track_sources"][1]["source"] == "spotify"
        assert result.metadata["track_sources"][1]["source_id"] == "sp-123"
        assert result.metadata["track_sources"][1]["playlist_name"] == "My Playlist"

    def test_skips_tracks_without_id(self):
        """Tracks with id=None are excluded from source map."""
        tracks = [
            Track(id=None, title="No ID", artists=[Artist(name="A1")]),
            Track(id=5, title="Has ID", artists=[Artist(name="A2")]),
        ]
        result = _build_source_tracklist(tracks, "PL", "canonical", "id-1")

        assert len(result.metadata["track_sources"]) == 1
        assert 5 in result.metadata["track_sources"]


class TestPlaylistSource:
    """Tests for playlist_source node."""

    async def test_canonical_read_returns_tracklist_with_sources(
        self, mock_workflow_context, sample_tracks
    ):
        """Canonical playlist read returns TrackList with track_sources metadata."""
        config = {"playlist_id": "pl-1"}
        result = await playlist_source(mock_workflow_context, config)

        assert "tracklist" in result
        tl = result["tracklist"]
        assert isinstance(tl, TrackList)
        assert len(tl.tracks) == 2
        assert "track_sources" in tl.metadata
        assert tl.metadata["operation"] == "playlist_source"

        # Verify track source entries
        for track in sample_tracks:
            assert track.id in tl.metadata["track_sources"]
            src = tl.metadata["track_sources"][track.id]
            assert src["source"] == "canonical"

    async def test_missing_playlist_id_raises(self, mock_workflow_context):
        """Missing playlist_id raises ValueError."""
        with pytest.raises(ValueError, match="playlist_id"):
            await playlist_source(mock_workflow_context, {})

    async def test_empty_playlist_id_raises(self, mock_workflow_context):
        """Empty string playlist_id raises ValueError."""
        with pytest.raises(ValueError, match="playlist_id"):
            await playlist_source(mock_workflow_context, {"playlist_id": ""})


class TestSourceLikedTracks:
    """Tests for source_liked_tracks node."""

    async def test_delegates_to_use_case(self, sample_tracks):
        """source_liked_tracks creates command and delegates via execute_use_case."""
        mock_result = MagicMock()
        mock_result.tracklist = TrackList(tracks=sample_tracks)
        mock_result.execution_time_ms = 42

        wf_ctx = AsyncMock()
        wf_ctx.execute_use_case = AsyncMock(return_value=mock_result)

        context = {"workflow_context": wf_ctx}
        config = {"limit": 50, "sort_by": "liked_at_desc"}

        result = await source_liked_tracks(context, config)

        assert "tracklist" in result
        assert len(result["tracklist"].tracks) == 2
        wf_ctx.execute_use_case.assert_awaited_once()

    async def test_enforces_limit_cap(self, sample_tracks):
        """Limit is capped at MAX_USER_LIMIT."""
        mock_result = MagicMock()
        mock_result.tracklist = TrackList(tracks=sample_tracks)
        mock_result.execution_time_ms = 10

        wf_ctx = AsyncMock()
        wf_ctx.execute_use_case = AsyncMock(return_value=mock_result)

        context = {"workflow_context": wf_ctx}
        config = {"limit": 99999}

        await source_liked_tracks(context, config)
        # The command passed to execute_use_case should have capped limit
        _, call_kwargs = wf_ctx.execute_use_case.call_args
        command = call_kwargs.get("command") or wf_ctx.execute_use_case.call_args[0][1]
        assert isinstance(command, GetLikedTracksCommand)
        assert command.limit == 10000


class TestSourcePlayedTracks:
    """Tests for source_played_tracks node."""

    async def test_delegates_to_use_case(self, sample_tracks):
        """source_played_tracks creates command and delegates via execute_use_case."""
        mock_result = MagicMock()
        mock_result.tracklist = TrackList(tracks=sample_tracks)
        mock_result.execution_time_ms = 55

        wf_ctx = AsyncMock()
        wf_ctx.execute_use_case = AsyncMock(return_value=mock_result)

        context = {"workflow_context": wf_ctx}
        config = {"limit": 100, "days_back": 30, "sort_by": "played_at_desc"}

        result = await source_played_tracks(context, config)

        assert "tracklist" in result
        assert len(result["tracklist"].tracks) == 2
        wf_ctx.execute_use_case.assert_awaited_once()


class TestPlaylistSourceConnector:
    """Tests for playlist_source connector branch (sync + upsert in one UoW)."""

    @pytest.fixture
    def connector_tracks(self):
        """Tracks returned by the canonical playlist after upsert."""
        return [
            Track(id=10, title="Connector Song A", artists=[Artist(name="Art 1")]),
            Track(id=11, title="Connector Song B", artists=[Artist(name="Art 2")]),
        ]

    @pytest.fixture
    def connector_context(self):
        """Build mock context where execute_service calls the closure with a mock UoW."""
        mockuow = MagicMock()
        wf_ctx = AsyncMock()
        wf_ctx.use_cases = MagicMock()

        async def _call_service(fn):
            return await fn(mockuow)

        wf_ctx.execute_service = AsyncMock(side_effect=_call_service)
        return {"workflow_context": wf_ctx}, mockuow

    def _make_upsert_result(self, tracks, *, is_create: bool):
        """Build a mock result matching Create or Update result type."""
        playlist = MagicMock()
        playlist.id = "canonical-1"
        playlist.name = "Spotify Favorites"
        playlist.tracks = tracks

        result = MagicMock(
            spec=CreateCanonicalPlaylistResult
            if is_create
            else UpdateCanonicalPlaylistResult,
        )
        result.playlist = playlist
        return result

    def _make_connector_playlist(self, *, empty: bool = False):
        """Build a ConnectorPlaylist with or without items."""
        if empty:
            return make_connector_playlist(
                items=[], connector_name="spotify", name="Empty PL"
            )
        items = [
            make_connector_playlist_item("track-a", position=0),
            make_connector_playlist_item("track-b", position=1),
        ]
        return make_connector_playlist(
            items=items, connector_name="spotify", name="Spotify Favorites"
        )

    @pytest.mark.parametrize(
        ("is_create", "expected_action"),
        [(True, "created"), (False, "updated")],
        ids=["first_run_creates", "subsequent_run_updates"],
    )
    async def test_connector_upserts_canonical_playlist(
        self, connector_context, connector_tracks, is_create, expected_action
    ):
        """Connector branch syncs + upserts in a single execute_service call."""
        context, uow = connector_context
        cp = self._make_connector_playlist()
        upsert_result = self._make_upsert_result(connector_tracks, is_create=is_create)

        with (
            patch(
                "src.application.workflows.source_nodes.sync_connector_playlist",
                new_callable=AsyncMock,
                return_value=cp,
            ) as mock_sync,
            patch(
                "src.application.workflows.source_nodes.upsert_canonical_playlist",
                new_callable=AsyncMock,
                return_value=upsert_result,
            ) as mock_upsert,
        ):
            result = await playlist_source(
                context, {"playlist_id": "sp-abc", "connector": "spotify"}
            )

        mock_sync.assert_awaited_once_with("spotify", "sp-abc", uow)
        mock_upsert.assert_awaited_once()
        call_args = mock_upsert.call_args
        assert call_args.args[:4] == (cp, "spotify", "sp-abc", uow)

        tl = result["tracklist"]
        assert isinstance(tl, TrackList)
        assert len(tl.tracks) == 2

    async def test_connector_empty_playlist_returns_empty_tracklist(
        self, connector_context
    ):
        """Empty connector playlist returns empty TrackList without calling upsert."""
        context, uow = connector_context
        empty_cp = self._make_connector_playlist(empty=True)

        with (
            patch(
                "src.application.workflows.source_nodes.sync_connector_playlist",
                new_callable=AsyncMock,
                return_value=empty_cp,
            ),
            patch(
                "src.application.workflows.source_nodes.upsert_canonical_playlist",
                new_callable=AsyncMock,
            ) as mock_upsert,
        ):
            result = await playlist_source(
                context, {"playlist_id": "sp-abc", "connector": "spotify"}
            )

        mock_upsert.assert_not_awaited()
        tl = result["tracklist"]
        assert isinstance(tl, TrackList)
        assert len(tl.tracks) == 0

    async def test_connector_fetch_error_propagates(self, connector_context):
        """External service failure propagates as-is (not swallowed)."""
        context, uow = connector_context

        with patch(
            "src.application.workflows.source_nodes.sync_connector_playlist",
            new_callable=AsyncMock,
            side_effect=ConnectionError("Spotify API down"),
        ):
            with pytest.raises(ConnectionError, match="Spotify API down"):
                await playlist_source(
                    context, {"playlist_id": "sp-abc", "connector": "spotify"}
                )

    async def test_connector_tracklist_has_connector_source_metadata(
        self, connector_context, connector_tracks
    ):
        """Source metadata uses connector name, not 'canonical'."""
        context, uow = connector_context
        cp = self._make_connector_playlist()
        upsert_result = self._make_upsert_result(connector_tracks, is_create=True)

        with (
            patch(
                "src.application.workflows.source_nodes.sync_connector_playlist",
                new_callable=AsyncMock,
                return_value=cp,
            ),
            patch(
                "src.application.workflows.source_nodes.upsert_canonical_playlist",
                new_callable=AsyncMock,
                return_value=upsert_result,
            ),
        ):
            result = await playlist_source(
                context, {"playlist_id": "sp-abc", "connector": "spotify"}
            )

        tl = result["tracklist"]
        for track in connector_tracks:
            src = tl.metadata["track_sources"][track.id]
            assert src["source"] == "spotify"
            assert src["source_id"] == "sp-abc"
            assert src["playlist_name"] == "Spotify Favorites"
