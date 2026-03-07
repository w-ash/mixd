"""Tests for SpotifyInwardResolver.

Validates that the Spotify-specific inward resolver correctly batches API calls,
detects redirects (where Spotify returns a different .id), creates dual mappings,
and delegates bulk lookup to the base class.
"""

from unittest.mock import AsyncMock, MagicMock

from src.config.constants import MatchMethod
from src.infrastructure.connectors.spotify.inward_resolver import (
    FallbackHint,
    SpotifyInwardResolver,
)
from tests.fixtures import make_spotify_track, make_track


class TestBatchFetch:
    """SpotifyInwardResolver should batch its API call."""

    async def test_calls_get_tracks_by_ids_once(self):
        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {
            "id1": make_spotify_track("id1", "Song A"),
            "id2": make_spotify_track("id2", "Song B"),
        }

        resolver = SpotifyInwardResolver(spotify_connector=connector)

        uow = MagicMock()
        track_repo = AsyncMock()
        track_repo.save_track.side_effect = [make_track(1), make_track(2)]
        uow.get_track_repository.return_value = track_repo

        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        connector_repo.map_track_to_connector.return_value = make_track(1)
        uow.get_connector_repository.return_value = connector_repo

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id1", "id2"], uow
        )

        connector.get_tracks_by_ids.assert_called_once()
        assert len(result) == 2
        assert metrics.created == 2

    async def test_existing_tracks_not_refetched(self):
        """Tracks with existing connector mappings should not trigger API calls."""
        connector = AsyncMock()
        existing = make_track(42)

        resolver = SpotifyInwardResolver(spotify_connector=connector)

        uow = MagicMock()
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {
            ("spotify", "id1"): existing,
        }
        uow.get_connector_repository.return_value = connector_repo

        result, metrics = await resolver.resolve_to_canonical_tracks(["id1"], uow)

        connector.get_tracks_by_ids.assert_not_called()
        assert result["id1"] == existing
        assert metrics.existing == 1
        assert metrics.created == 0


class TestMissingMetadata:
    """IDs not returned by Spotify API should be reported as failed."""

    async def test_missing_api_response_counted_as_failed(self):
        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {
            "id1": make_spotify_track("id1"),
            # id2 intentionally missing from API response
        }

        resolver = SpotifyInwardResolver(spotify_connector=connector)

        uow = MagicMock()
        track_repo = AsyncMock()
        track_repo.save_track.return_value = make_track(1)
        uow.get_track_repository.return_value = track_repo

        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        connector_repo.map_track_to_connector.return_value = make_track(1)
        uow.get_connector_repository.return_value = connector_repo

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id1", "id2"], uow
        )

        assert "id1" in result
        assert "id2" not in result
        assert metrics.created == 1
        assert metrics.failed == 1


class TestNormalization:
    """Spotify IDs are pass-through (already stable)."""

    async def test_spotify_id_passthrough(self):
        connector = AsyncMock()
        resolver = SpotifyInwardResolver(spotify_connector=connector)

        # Spotify IDs should be passed as-is
        assert (
            resolver._normalize_id("4iV5W9uYEdYUVa79Axb7Rh") == "4iV5W9uYEdYUVa79Axb7Rh"
        )


class TestRedirectDetection:
    """Spotify redirects (track.id != requested_id) should create dual mappings."""

    async def test_redirect_creates_dual_mappings(self):
        """When Spotify returns a different .id, both old and new IDs get mapped."""
        old_id = "old_stale_id_0000000000"
        new_id = "new_canonical_id_000000"

        connector = AsyncMock()
        # Spotify returns a track with new_id when we ask for old_id
        connector.get_tracks_by_ids.return_value = {
            old_id: make_spotify_track(new_id, "Redirected Song"),
        }

        resolver = SpotifyInwardResolver(spotify_connector=connector)

        uow = MagicMock()
        track_repo = AsyncMock()
        saved_track = make_track(1)
        track_repo.save_track.return_value = saved_track
        uow.get_track_repository.return_value = track_repo

        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        connector_repo.map_track_to_connector.return_value = saved_track
        uow.get_connector_repository.return_value = connector_repo

        result, metrics = await resolver.resolve_to_canonical_tracks([old_id], uow)

        assert old_id in result
        assert metrics.created == 1

        # Should have been called twice: primary (new_id) + secondary (old_id)
        map_calls = connector_repo.map_track_to_connector.call_args_list
        assert len(map_calls) == 2

        # Primary mapping: new canonical ID
        primary = map_calls[0]
        assert primary.args[2] == new_id  # connector_id
        assert primary.args[3] == MatchMethod.DIRECT_IMPORT  # match_method
        assert primary.kwargs["confidence"] == 100
        assert primary.kwargs["auto_set_primary"] is True

        # Secondary mapping: old stale ID
        secondary = map_calls[1]
        assert secondary.args[2] == old_id  # connector_id
        assert (
            secondary.args[3] == f"{MatchMethod.DIRECT_IMPORT}_stale_id"
        )  # match_method
        assert secondary.kwargs["confidence"] == 100
        assert secondary.kwargs["auto_set_primary"] is False

    async def test_redirect_resolved_ids_tracked(self):
        """Redirected IDs should appear in redirect_resolved_ids set."""
        old_id = "old_stale_id_0000000000"
        new_id = "new_canonical_id_000000"

        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {
            old_id: make_spotify_track(new_id, "Song"),
        }

        resolver = SpotifyInwardResolver(spotify_connector=connector)

        uow = MagicMock()
        track_repo = AsyncMock()
        track_repo.save_track.return_value = make_track(1)
        uow.get_track_repository.return_value = track_repo

        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        connector_repo.map_track_to_connector.return_value = make_track(1)
        uow.get_connector_repository.return_value = connector_repo

        await resolver.resolve_to_canonical_tracks([old_id], uow)

        assert old_id in resolver.redirect_resolved_ids

    async def test_no_redirect_single_mapping(self):
        """When track.id == requested_id, only one mapping should be created."""
        track_id = "same_id_returned_00000"

        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {
            track_id: make_spotify_track(track_id, "Normal Song"),
        }

        resolver = SpotifyInwardResolver(spotify_connector=connector)

        uow = MagicMock()
        track_repo = AsyncMock()
        track_repo.save_track.return_value = make_track(1)
        uow.get_track_repository.return_value = track_repo

        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        connector_repo.map_track_to_connector.return_value = make_track(1)
        uow.get_connector_repository.return_value = connector_repo

        await resolver.resolve_to_canonical_tracks([track_id], uow)

        # Only one mapping call (no secondary stale ID mapping)
        assert connector_repo.map_track_to_connector.call_count == 1
        assert track_id not in resolver.redirect_resolved_ids


def _make_uow_with_repos():
    """Create a UoW mock with track and connector repos wired."""
    uow = MagicMock()
    track_repo = AsyncMock()
    track_repo.save_track.return_value = make_track(1)
    uow.get_track_repository.return_value = track_repo

    connector_repo = AsyncMock()
    connector_repo.find_tracks_by_connectors.return_value = {}
    connector_repo.map_track_to_connector.return_value = make_track(1)
    uow.get_connector_repository.return_value = connector_repo
    return uow, track_repo, connector_repo


class TestFallbackSearch:
    """Fallback search resolves dead Spotify IDs via artist+title search."""

    async def test_dead_id_with_hint_resolved_via_search(self):
        """Dead ID with a hint searches Spotify, creates track with dual mappings."""
        dead_id = "dead_id_000000000000000"
        found_id = "found_id_00000000000000"

        connector = AsyncMock()
        # Batch fetch returns nothing for dead_id
        connector.get_tracks_by_ids.return_value = {}
        # Search returns a candidate
        connector.search_track.return_value = [
            make_spotify_track(found_id, "My Song"),
        ]

        resolver = SpotifyInwardResolver(spotify_connector=connector)
        uow, track_repo, connector_repo = _make_uow_with_repos()

        hints = {dead_id: FallbackHint(artist_name="Artist", track_name="My Song")}
        result, metrics = await resolver.resolve_to_canonical_tracks(
            [dead_id], uow, fallback_hints=hints
        )

        assert dead_id in result
        assert dead_id in resolver.fallback_resolved_ids
        connector.search_track.assert_called_once_with("Artist", "My Song")

        # Primary mapping (found_id) + secondary mapping (dead_id stale)
        map_calls = connector_repo.map_track_to_connector.call_args_list
        assert len(map_calls) == 2
        assert map_calls[0].args[2] == found_id
        assert map_calls[0].kwargs["auto_set_primary"] is True
        assert map_calls[1].args[2] == dead_id
        assert map_calls[1].kwargs["auto_set_primary"] is False

    async def test_no_search_results_returns_none(self):
        """When search returns no candidates, the dead ID remains unresolved."""
        dead_id = "dead_id_000000000000000"

        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {}
        connector.search_track.return_value = []

        resolver = SpotifyInwardResolver(spotify_connector=connector)
        uow, _, _ = _make_uow_with_repos()

        hints = {dead_id: FallbackHint(artist_name="Artist", track_name="Song")}
        result, metrics = await resolver.resolve_to_canonical_tracks(
            [dead_id], uow, fallback_hints=hints
        )

        assert dead_id not in result
        assert metrics.failed == 1

    async def test_below_similarity_threshold_rejected(self):
        """Candidates with low title similarity are rejected."""
        dead_id = "dead_id_000000000000000"

        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {}
        # Return a candidate with a very different name
        connector.search_track.return_value = [
            make_spotify_track("other_id_00000000000000", "Completely Different Title"),
        ]

        resolver = SpotifyInwardResolver(spotify_connector=connector)
        uow, _, _ = _make_uow_with_repos()

        hints = {dead_id: FallbackHint(artist_name="Artist", track_name="My Song")}
        result, metrics = await resolver.resolve_to_canonical_tracks(
            [dead_id], uow, fallback_hints=hints
        )

        assert dead_id not in result

    async def test_search_exception_does_not_crash(self):
        """API failure during search is logged and returns no result."""
        dead_id = "dead_id_000000000000000"

        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {}
        connector.search_track.side_effect = RuntimeError("API down")

        resolver = SpotifyInwardResolver(spotify_connector=connector)
        uow, _, _ = _make_uow_with_repos()

        hints = {dead_id: FallbackHint(artist_name="Artist", track_name="Song")}
        result, metrics = await resolver.resolve_to_canonical_tracks(
            [dead_id], uow, fallback_hints=hints
        )

        assert dead_id not in result
        assert metrics.failed == 1


class TestResolutionMethod:
    """get_resolution_method() returns correct tags for different resolution paths."""

    async def test_resolution_method_returns_redirect(self):
        connector = AsyncMock()
        resolver = SpotifyInwardResolver(spotify_connector=connector)
        resolver._redirect_resolved_ids = {"old_id"}

        assert resolver.get_resolution_method("old_id") == MatchMethod.SPOTIFY_REDIRECT

    async def test_resolution_method_returns_fallback(self):
        connector = AsyncMock()
        resolver = SpotifyInwardResolver(spotify_connector=connector)
        resolver._fallback_resolved_ids = {"dead_id"}

        assert resolver.get_resolution_method("dead_id") == MatchMethod.SEARCH_FALLBACK

    async def test_resolution_method_returns_default(self):
        connector = AsyncMock()
        resolver = SpotifyInwardResolver(spotify_connector=connector)

        assert resolver.get_resolution_method("normal_id") == MatchMethod.PLAY_RESOLVER
