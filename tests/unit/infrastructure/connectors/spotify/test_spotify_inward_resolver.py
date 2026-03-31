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
from src.infrastructure.connectors.spotify.models import SpotifyExternalIds
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
            ["id1", "id2"], uow, user_id="test-user"
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

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id1"], uow, user_id="test-user"
        )

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
            ["id1", "id2"], uow, user_id="test-user"
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

        result, metrics = await resolver.resolve_to_canonical_tracks(
            [old_id], uow, user_id="test-user"
        )

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

        await resolver.resolve_to_canonical_tracks([old_id], uow, user_id="test-user")

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

        await resolver.resolve_to_canonical_tracks([track_id], uow, user_id="test-user")

        # Only one mapping call (no secondary stale ID mapping)
        assert connector_repo.map_track_to_connector.call_count == 1
        assert track_id not in resolver.redirect_resolved_ids


def _make_uow_with_repos():
    """Create a UoW mock with track and connector repos wired."""
    uow = MagicMock()
    track_repo = AsyncMock()
    track_repo.save_track.return_value = make_track(1)
    track_repo.find_tracks_by_title_artist.return_value = {}
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
            [dead_id],
            uow,
            fallback_hints=hints,
            user_id="test-user",
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
            [dead_id],
            uow,
            fallback_hints=hints,
            user_id="test-user",
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
            [dead_id],
            uow,
            fallback_hints=hints,
            user_id="test-user",
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
            [dead_id],
            uow,
            fallback_hints=hints,
            user_id="test-user",
        )

        assert dead_id not in result
        assert metrics.failed == 1

    async def test_no_hints_leaves_dead_id_unresolved(self):
        """Dead IDs without hints should remain as failures (no search attempted)."""
        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {}  # All dead

        resolver = SpotifyInwardResolver(spotify_connector=connector)
        uow, _, _ = _make_uow_with_repos()

        # No fallback hints provided
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["dead_id"],
            uow,
            fallback_hints=None,
            user_id="test-user",
        )

        assert "dead_id" not in result
        assert metrics.failed == 1
        connector.search_track.assert_not_called()

    async def test_isrc_dedup_reuses_existing_track(self):
        """Search results whose ISRC already exists should upsert, not duplicate."""
        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {}
        connector.search_track.return_value = [
            make_spotify_track("new_id", "Song", "Artist", isrc="USRC12345678")
        ]

        resolver = SpotifyInwardResolver(spotify_connector=connector)
        uow, track_repo, connector_repo = _make_uow_with_repos()

        # save_track returns the EXISTING track (ISRC upsert)
        existing_track = make_track(id=42, isrc="USRC12345678")
        track_repo.save_track.return_value = existing_track
        connector_repo.map_track_to_connector.return_value = existing_track

        hints = {"dead_id": FallbackHint(artist_name="Artist", track_name="Song")}

        result, _ = await resolver.resolve_to_canonical_tracks(
            ["dead_id"],
            uow,
            fallback_hints=hints,
            user_id="test-user",
        )

        assert "dead_id" in result
        assert result["dead_id"].id == 42  # Existing track, not a new one
        track_repo.save_track.assert_called_once()  # Upsert, not create + duplicate

    async def test_fallback_resolved_ids_populated(self):
        """fallback_resolved_ids should contain only IDs resolved via search."""
        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {
            "id1": make_spotify_track("id1"),
        }
        connector.search_track.return_value = [make_spotify_track("new_id2", "Song B")]

        resolver = SpotifyInwardResolver(spotify_connector=connector)
        uow, track_repo, connector_repo = _make_uow_with_repos()

        track_repo.save_track.side_effect = [make_track(1), make_track(2)]
        connector_repo.map_track_to_connector.return_value = make_track(1)

        hints = {"id2": FallbackHint(artist_name="Artist", track_name="Song B")}

        await resolver.resolve_to_canonical_tracks(
            ["id1", "id2"],
            uow,
            fallback_hints=hints,
            user_id="test-user",
        )

        assert resolver.fallback_resolved_ids == {"id2"}


class TestCanonicalReuse:
    """Canonical Reuse: Spotify resolver reuses existing canonical tracks from fallback hints."""

    async def test_reuses_existing_canonical_via_hint(self):
        """When a canonical track exists and hint matches, creates Spotify mapping."""
        existing_track = make_track(42, title="My Song", artist="Artist")

        connector = AsyncMock()
        # Batch fetch returns nothing — all IDs are "missing"
        connector.get_tracks_by_ids.return_value = {}

        resolver = SpotifyInwardResolver(spotify_connector=connector)
        uow, track_repo, connector_repo = _make_uow_with_repos()

        # find_tracks_by_title_artist returns the existing canonical track
        track_repo.find_tracks_by_title_artist.return_value = {
            ("my song", "artist"): existing_track,
        }
        connector_repo.map_track_to_connector.return_value = existing_track

        hints = {"sp_id_1": FallbackHint(artist_name="Artist", track_name="My Song")}
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["sp_id_1"],
            uow,
            fallback_hints=hints,
            user_id="test-user",
        )

        assert "sp_id_1" in result
        assert result["sp_id_1"].id == 42
        assert metrics.reused == 1
        assert metrics.created == 0

        # Verify mapping was created with CANONICAL_REUSE method
        map_calls = connector_repo.map_track_to_connector.call_args_list
        assert len(map_calls) == 1
        assert map_calls[0].args[1] == "spotify"
        assert map_calls[0].args[2] == "sp_id_1"
        assert map_calls[0].args[3] == MatchMethod.CANONICAL_REUSE

    async def test_no_reuse_without_hints(self):
        """Without fallback hints, canonical reuse should not find any candidates."""
        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {}

        resolver = SpotifyInwardResolver(spotify_connector=connector)
        uow, track_repo, connector_repo = _make_uow_with_repos()

        # No hints provided — canonical reuse returns empty
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["sp_id_1"],
            uow,
            fallback_hints=None,
            user_id="test-user",
        )

        assert "sp_id_1" not in result
        track_repo.find_tracks_by_title_artist.assert_not_called()

    async def test_low_confidence_reuse_rejected(self):
        """Candidates with low title similarity should be rejected in canonical reuse."""
        # Track title is very different from hint
        existing_track = make_track(42, title="Completely Different", artist="Artist")

        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {}

        resolver = SpotifyInwardResolver(spotify_connector=connector)
        uow, track_repo, connector_repo = _make_uow_with_repos()

        track_repo.find_tracks_by_title_artist.return_value = {
            ("my song", "artist"): existing_track,
        }

        hints = {"sp_id_1": FallbackHint(artist_name="Artist", track_name="My Song")}
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["sp_id_1"],
            uow,
            fallback_hints=hints,
            user_id="test-user",
        )

        # Should be rejected due to low title similarity
        assert "sp_id_1" not in result
        assert metrics.reused == 0
        # No mapping should be created
        connector_repo.map_track_to_connector.assert_not_called()

    async def test_reuse_plus_direct_import_combined(self):
        """Canonical reuse and track creation work together."""
        existing_track = make_track(42, title="Reused Song", artist="Artist A")

        connector = AsyncMock()
        # id2 found by API, id1 not found (will try canonical reuse)
        connector.get_tracks_by_ids.return_value = {
            "id2": make_spotify_track("id2", "New Song", "Artist B"),
        }

        resolver = SpotifyInwardResolver(spotify_connector=connector)
        uow, track_repo, connector_repo = _make_uow_with_repos()

        track_repo.find_tracks_by_title_artist.return_value = {
            ("reused song", "artist a"): existing_track,
        }
        track_repo.save_track.return_value = make_track(99)
        connector_repo.map_track_to_connector.return_value = existing_track

        hints = {"id1": FallbackHint(artist_name="Artist A", track_name="Reused Song")}
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["id1", "id2"],
            uow,
            fallback_hints=hints,
            user_id="test-user",
        )

        assert "id1" in result
        assert "id2" in result
        assert metrics.reused == 1
        assert metrics.created == 1


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


class TestISRCDedup:
    """ISRC dedup: reuse existing canonical tracks when Spotify returns matching ISRC."""

    async def test_isrc_dedup_reuses_existing_canonical(self):
        """When Spotify returns a track with an ISRC that already exists, reuse it."""
        existing_track = make_track(42, title="Same Song", artist="Same Artist")
        spotify_id = "new_spotify_id_0000000"

        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {
            spotify_id: make_spotify_track(
                spotify_id,
                "Same Song",
                "Same Artist",
                external_ids=SpotifyExternalIds(isrc="USRC17000001"),
            ),
        }

        resolver = SpotifyInwardResolver(spotify_connector=connector)
        uow, track_repo, connector_repo = _make_uow_with_repos()

        # Existing track already has this ISRC
        track_repo.find_tracks_by_isrcs.return_value = {"USRC17000001": existing_track}

        result, metrics = await resolver.resolve_to_canonical_tracks(
            [spotify_id], uow, user_id="test-user"
        )

        assert spotify_id in result
        assert result[spotify_id].id == 42

        # Should have created a mapping with ISRC_MATCH method, not saved a new track
        map_calls = connector_repo.map_track_to_connector.call_args_list
        assert any(call.args[3] == MatchMethod.ISRC_MATCH for call in map_calls)
        # save_track should NOT have been called (reused existing)
        track_repo.save_track.assert_not_called()

    async def test_no_isrc_dedup_when_isrc_not_in_db(self):
        """When ISRC is not found in DB, normal track creation proceeds."""
        spotify_id = "new_spotify_id_0000000"

        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {
            spotify_id: make_spotify_track(
                spotify_id,
                "New Song",
                "New Artist",
                external_ids=SpotifyExternalIds(isrc="USRC17000002"),
            ),
        }

        resolver = SpotifyInwardResolver(spotify_connector=connector)
        uow, track_repo, connector_repo = _make_uow_with_repos()

        # No existing track with this ISRC
        track_repo.find_tracks_by_isrcs.return_value = {}
        track_repo.save_track.return_value = make_track(99)

        result, metrics = await resolver.resolve_to_canonical_tracks(
            [spotify_id], uow, user_id="test-user"
        )

        assert spotify_id in result
        assert metrics.created == 1
        track_repo.save_track.assert_called_once()

    async def test_no_isrc_dedup_without_external_ids(self):
        """Tracks without ISRCs skip ISRC dedup entirely."""
        spotify_id = "no_isrc_id_0000000000"

        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {
            spotify_id: make_spotify_track(spotify_id, "No ISRC Song"),
        }

        resolver = SpotifyInwardResolver(spotify_connector=connector)
        uow, track_repo, connector_repo = _make_uow_with_repos()
        track_repo.save_track.return_value = make_track(99)

        result, metrics = await resolver.resolve_to_canonical_tracks(
            [spotify_id], uow, user_id="test-user"
        )

        assert spotify_id in result
        assert metrics.created == 1
        # find_tracks_by_isrcs should not be called with empty list
        # (it's called once with empty list at most, which returns {})
        track_repo.save_track.assert_called_once()
