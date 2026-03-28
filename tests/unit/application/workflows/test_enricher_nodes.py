"""Tests for enricher workflow nodes.

Validates enrich_spotify_liked_status: in-memory metadata update,
DB persistence via save_track_likes_batch, and edge cases (no Spotify IDs,
empty tracklist, all liked, API failure).
"""

from unittest.mock import AsyncMock, MagicMock

from src.application.workflows.enricher_nodes import enrich_spotify_liked_status
from src.domain.entities.track import TrackList
from tests.fixtures import make_track


def _make_context(
    tracks: list,
    connector: object,
    *,
    execute_service_side_effect=None,
) -> dict:
    """Build a minimal workflow context dict for enricher node tests."""
    wf_ctx = AsyncMock()
    wf_ctx.connectors = MagicMock()
    wf_ctx.connectors.list_connectors.return_value = ["spotify"]
    wf_ctx.connectors.get_connector.return_value = connector
    if execute_service_side_effect:
        wf_ctx.execute_service.side_effect = execute_service_side_effect
    else:
        wf_ctx.execute_service.return_value = None

    return {
        "tracklist": TrackList(tracks=tracks),
        "workflow_context": wf_ctx,
    }


def _make_mock_connector(saved_status: dict[str, bool]) -> AsyncMock:
    """Build a mock SpotifyConnector with check_library_contains pre-wired."""
    connector = AsyncMock()
    connector.check_library_contains = AsyncMock(return_value=saved_status)
    return connector


class TestEnrichSpotifyLikedStatusHappyPath:
    """Core enrichment flow: API check → metadata update → DB persist."""

    async def test_sets_is_liked_metadata_on_tracks(self):
        """Tracks get connector_metadata["spotify"]["is_liked"] set from API response."""
        tracks = [
            make_track(
                id=1, title="Liked Song", connector_track_identifiers={"spotify": "aaa"}
            ),
            make_track(
                id=2, title="Not Liked", connector_track_identifiers={"spotify": "bbb"}
            ),
        ]
        saved = {"spotify:track:aaa": True, "spotify:track:bbb": False}
        connector = _make_mock_connector(saved)
        context = _make_context(tracks, connector)

        result = await enrich_spotify_liked_status(context, {})

        tl = result["tracklist"]
        assert tl.tracks[0].is_liked_on("spotify") is True
        assert tl.tracks[1].is_liked_on("spotify") is False

    async def test_persists_liked_status_to_database(self):
        """execute_service is called with a function that writes to like_repo."""
        t1 = make_track(connector_track_identifiers={"spotify": "aaa"})
        t2 = make_track(connector_track_identifiers={"spotify": "bbb"})
        tracks = [t1, t2]
        saved = {"spotify:track:aaa": True, "spotify:track:bbb": False}
        connector = _make_mock_connector(saved)

        captured_fn = None

        async def capture_service(fn):
            nonlocal captured_fn
            captured_fn = fn

        context = _make_context(
            tracks, connector, execute_service_side_effect=capture_service
        )
        await enrich_spotify_liked_status(context, {})

        # Verify execute_service was called
        assert captured_fn is not None

        # Execute the captured function with a mock UoW
        mock_uow = AsyncMock()
        mock_like_repo = AsyncMock()
        # get_like_repository is a sync method returning a repo
        mock_uow.get_like_repository = MagicMock(return_value=mock_like_repo)
        await captured_fn(mock_uow)

        # Verify save_track_likes_batch called with correct tuples
        mock_like_repo.save_track_likes_batch.assert_awaited_once()
        likes_arg = mock_like_repo.save_track_likes_batch.call_args[0][0]
        assert len(likes_arg) == 2

        # Check (track_id, service, is_liked, ...) structure
        ids_and_status = {(t[0], t[2]) for t in likes_arg}
        assert (t1.id, True) in ids_and_status
        assert (t2.id, False) in ids_and_status

    async def test_preserves_tracklist_metadata(self):
        """Original tracklist metadata is preserved in the output."""
        tracks = [
            make_track(connector_track_identifiers={"spotify": "aaa"}),
        ]
        connector = _make_mock_connector({"spotify:track:aaa": True})
        context = _make_context(tracks, connector)
        context["tracklist"] = TrackList(
            tracks=tracks, metadata={"source": "test", "track_sources": {}}
        )

        result = await enrich_spotify_liked_status(context, {})

        assert result["tracklist"].metadata["source"] == "test"

    async def test_calls_connector_with_correct_uris(self):
        """check_library_contains receives properly formatted Spotify URIs."""
        tracks = [
            make_track(connector_track_identifiers={"spotify": "abc123"}),
            make_track(connector_track_identifiers={"spotify": "def456"}),
        ]
        connector = _make_mock_connector({
            "spotify:track:abc123": False,
            "spotify:track:def456": False,
        })
        context = _make_context(tracks, connector)

        await enrich_spotify_liked_status(context, {})

        connector.check_library_contains.assert_awaited_once()
        uris = connector.check_library_contains.call_args[0][0]
        assert set(uris) == {"spotify:track:abc123", "spotify:track:def456"}


class TestEnrichSpotifyLikedStatusEdgeCases:
    """Edge cases: empty playlists, missing IDs, tracks without DB IDs."""

    async def test_no_spotify_ids_skips_api_call(self):
        """Tracks without Spotify identifiers skip the API call entirely."""
        tracks = [
            make_track(connector_track_identifiers={}),
            make_track(connector_track_identifiers={"lastfm": "xyz"}),
        ]
        connector = _make_mock_connector({})
        context = _make_context(tracks, connector)

        result = await enrich_spotify_liked_status(context, {})

        connector.check_library_contains.assert_not_awaited()
        assert result["tracklist"].tracks == tracks

    async def test_empty_tracklist_returns_empty(self):
        """Empty tracklist passes through without API calls."""
        connector = _make_mock_connector({})
        context = _make_context([], connector)

        result = await enrich_spotify_liked_status(context, {})

        connector.check_library_contains.assert_not_awaited()
        assert len(result["tracklist"].tracks) == 0

    async def test_all_tracks_persisted_with_uuid_ids(self):
        """All tracks have UUIDs, so all are persisted to the database."""
        t1 = make_track(connector_track_identifiers={"spotify": "aaa"})
        t2 = make_track(connector_track_identifiers={"spotify": "bbb"})
        tracks = [t1, t2]
        saved = {"spotify:track:aaa": True, "spotify:track:bbb": False}
        connector = _make_mock_connector(saved)

        captured_fn = None

        async def capture_service(fn):
            nonlocal captured_fn
            captured_fn = fn

        context = _make_context(
            tracks, connector, execute_service_side_effect=capture_service
        )
        result = await enrich_spotify_liked_status(context, {})

        # In-memory metadata is updated for both
        assert result["tracklist"].tracks[0].is_liked_on("spotify") is True
        assert result["tracklist"].tracks[1].is_liked_on("spotify") is False

        # Both tracks should be in the persisted batch
        assert captured_fn is not None
        mock_uow = AsyncMock()
        mock_like_repo = AsyncMock()
        mock_uow.get_like_repository = MagicMock(return_value=mock_like_repo)
        await captured_fn(mock_uow)

        likes_arg = mock_like_repo.save_track_likes_batch.call_args[0][0]
        assert len(likes_arg) == 2

    async def test_mixed_tracks_some_with_spotify_ids(self):
        """Only tracks with Spotify IDs are checked; others pass through unchanged."""
        tracks = [
            make_track(connector_track_identifiers={"spotify": "aaa"}),
            make_track(connector_track_identifiers={}),  # no Spotify ID
            make_track(connector_track_identifiers={"spotify": "ccc"}),
        ]
        saved = {"spotify:track:aaa": True, "spotify:track:ccc": False}
        connector = _make_mock_connector(saved)
        context = _make_context(tracks, connector)

        result = await enrich_spotify_liked_status(context, {})

        tl = result["tracklist"]
        assert tl.tracks[0].is_liked_on("spotify") is True
        # Track without Spotify ID should be unchanged
        assert tl.tracks[1].is_liked_on("spotify") is False  # default
        assert tl.tracks[2].is_liked_on("spotify") is False
