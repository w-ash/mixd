"""Characterization tests for source nodes.

Locks down current behavior of playlist_source, source_liked_tracks,
and source_played_tracks before workflow cleanup.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain.entities.track import Artist, Track, TrackList


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
        from src.application.workflows.source_nodes import _build_source_tracklist

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
        from src.application.workflows.source_nodes import _build_source_tracklist

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
        from src.application.workflows.source_nodes import playlist_source

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
        from src.application.workflows.source_nodes import playlist_source

        with pytest.raises(ValueError, match="playlist_id"):
            await playlist_source(mock_workflow_context, {})

    async def test_empty_playlist_id_raises(self, mock_workflow_context):
        """Empty string playlist_id raises ValueError."""
        from src.application.workflows.source_nodes import playlist_source

        with pytest.raises(ValueError, match="playlist_id"):
            await playlist_source(mock_workflow_context, {"playlist_id": ""})


class TestSourceLikedTracks:
    """Tests for source_liked_tracks node."""

    async def test_delegates_to_use_case(self, sample_tracks):
        """source_liked_tracks creates command and delegates to GetLikedTracksUseCase."""
        from src.application.workflows.source_nodes import source_liked_tracks

        mock_result = MagicMock()
        mock_result.tracklist = TrackList(tracks=sample_tracks)
        mock_result.execution_time_ms = 42

        wf_ctx = AsyncMock()
        wf_ctx.execute_use_case = AsyncMock(return_value=mock_result)

        context = {"workflow_context": wf_ctx}
        config = {"limit": 50, "sort_by": "liked_at_desc"}

        with patch("src.application.workflows.source_nodes.GetLikedTracksUseCase"):
            result = await source_liked_tracks(context, config)

        assert "tracklist" in result
        assert len(result["tracklist"].tracks) == 2

    async def test_enforces_limit_cap(self, sample_tracks):
        """Limit is capped at 10000."""
        from src.application.workflows.source_nodes import source_liked_tracks

        mock_result = MagicMock()
        mock_result.tracklist = TrackList(tracks=sample_tracks)
        mock_result.execution_time_ms = 10

        wf_ctx = AsyncMock()
        wf_ctx.execute_use_case = AsyncMock(return_value=mock_result)

        context = {"workflow_context": wf_ctx}
        config = {"limit": 99999}

        with (
            patch("src.application.workflows.source_nodes.GetLikedTracksUseCase"),
            patch(
                "src.application.workflows.source_nodes.GetLikedTracksCommand"
            ) as mock_cmd,
        ):
            await source_liked_tracks(context, config)
            # The command should have been created with capped limit
            mock_cmd.assert_called_once()
            assert mock_cmd.call_args.kwargs["limit"] == 10000


class TestSourcePlayedTracks:
    """Tests for source_played_tracks node."""

    async def test_delegates_to_use_case(self, sample_tracks):
        """source_played_tracks creates command and delegates to GetPlayedTracksUseCase."""
        from src.application.workflows.source_nodes import source_played_tracks

        mock_result = MagicMock()
        mock_result.tracklist = TrackList(tracks=sample_tracks)
        mock_result.execution_time_ms = 55

        wf_ctx = AsyncMock()
        wf_ctx.execute_use_case = AsyncMock(return_value=mock_result)

        context = {"workflow_context": wf_ctx}
        config = {"limit": 100, "days_back": 30, "sort_by": "played_at_desc"}

        with patch("src.application.workflows.source_nodes.GetPlayedTracksUseCase"):
            result = await source_played_tracks(context, config)

        assert "tracklist" in result
        assert len(result["tracklist"].tracks) == 2
