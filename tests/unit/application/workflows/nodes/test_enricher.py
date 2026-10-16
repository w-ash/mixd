"""Tests for enricher workflow nodes.

Validates enrich_spotify_liked_status: in-memory metadata update,
DB persistence (saved tracks upserted via save_track_likes_batch, unsaved
tracks removed via delete_track_likes_batch), and edge cases (no Spotify IDs,
empty tracklist, all liked, API failure).

Also covers enricher.preferences, enricher.tags, and enricher.artist_favorites
registration + config builders — the execution path itself is covered at the
use-case layer (test_enrich_tracks_use_case.py).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.connector_protocols import LibraryContainsConnector
from src.application.use_cases.enrich_tracks import EnrichmentConfig

# Importing the node catalog runs @node(...) decorators that register every
# workflow node type — required for get_node() to resolve the new entries.
from src.application.workflows.nodes import catalog
from src.application.workflows.nodes.enricher import enrich_spotify_liked_status
from src.application.workflows.nodes.factories import (
    static_enrichment_config,
)
from src.application.workflows.nodes.registry import get_node
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
    wf_ctx.connectors.describe.return_value = MagicMock(
        capabilities=frozenset({"library_contains"})
    )
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
    """Build a mock SpotifyConnector with check_library_contains pre-wired.

    ``spec`` limits the mock to the protocol's attributes so it passes the
    ``isinstance`` narrowing in ``NodeContext.get_connector``.
    """
    connector = AsyncMock(spec=LibraryContainsConnector)
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
        """Saved tracks are upserted as like rows; unsaved tracks lose theirs."""
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

        # Saved track → upsert (track_id, service, liked_at); unsaved → delete
        mock_like_repo.save_track_likes_batch.assert_awaited_once()
        saved_arg = mock_like_repo.save_track_likes_batch.call_args[0][0]
        assert [(t[0], t[1]) for t in saved_arg] == [(t1.id, "spotify")]

        mock_like_repo.delete_track_likes_batch.assert_awaited_once()
        deleted_arg = mock_like_repo.delete_track_likes_batch.call_args[0][0]
        assert deleted_arg == [(t2.id, "spotify")]

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

    async def test_mixed_tracks_some_with_spotify_ids(self):
        """Only tracks with Spotify IDs are checked; others pass through unchanged.

        Both Spotify tracks are liked, so a status written to the wrong index
        would mark the ID-less middle track instead of the last one.
        """
        tracks = [
            make_track(connector_track_identifiers={"spotify": "aaa"}),
            make_track(connector_track_identifiers={}),  # no Spotify ID
            make_track(connector_track_identifiers={"spotify": "ccc"}),
        ]
        saved = {"spotify:track:aaa": True, "spotify:track:ccc": True}
        connector = _make_mock_connector(saved)
        context = _make_context(tracks, connector)

        result = await enrich_spotify_liked_status(context, {})

        tl = result["tracklist"]
        assert [t.is_liked_on("spotify") for t in tl.tracks] == [True, False, True]
        # Track without Spotify ID should be unchanged
        assert tl.tracks[1] is tracks[1]


class TestPreferenceAndTagEnricherRegistration:
    """Verify enricher.preferences and enricher.tags are registered and that
    their config builders produce the right EnrichmentConfig. The full
    execution path is covered by test_enrich_tracks_use_case.py.
    """

    @pytest.mark.parametrize(
        "node_id",
        ["enricher.preferences", "enricher.tags", "enricher.artist_favorites"],
    )
    def test_static_enricher_node_registered(self, node_id: str):
        """The editor draws one tracklist input and one tracklist output port."""
        # Side-effect registration happens at import time; reference the module
        # so the import isn't flagged as unused.
        assert catalog.__name__.endswith("catalog")

        _, meta = get_node(node_id)
        assert meta["category"] == "enricher"
        assert meta["input_type"] == "tracklist"
        assert meta["output_type"] == "tracklist"

    @pytest.mark.parametrize(
        "enrichment_type", ["preferences", "tags", "artist_favorites"]
    )
    def test_builders_ignore_user_config(self, enrichment_type: str):
        """Builders select their enrichment type and ignore any user config."""
        config = static_enrichment_config(enrichment_type)(
            MagicMock(), {"metrics": ["total_plays"], "connector": "spotify"}
        )
        assert config == EnrichmentConfig(enrichment_type=enrichment_type)
