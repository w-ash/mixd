"""Unit tests for SpotifyConnectorPlayResolver.

Tests the resolver's core business logic: duration filtering, incognito filtering,
track resolution, relinking, metadata preservation, and error handling.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.entities import ConnectorTrackPlay, TrackPlay
from src.infrastructure.connectors.spotify.models import (
    SpotifyAlbum,
    SpotifyArtist,
    SpotifyLinkedFrom,
    SpotifyTrack,
)
from src.infrastructure.connectors.spotify.play_resolver import (
    SpotifyConnectorPlayResolver,
    should_include_spotify_play,
)
from tests.fixtures.factories import make_track


def _make_spotify_track(
    spotify_id: str,
    name: str = "Test Song",
    artist_name: str = "Test Artist",
    album_name: str = "Test Album",
    duration_ms: int = 300000,
    linked_from_id: str | None = None,
) -> SpotifyTrack:
    """Create a minimal SpotifyTrack Pydantic model for testing."""
    return SpotifyTrack(
        id=spotify_id,
        name=name,
        artists=[SpotifyArtist(name=artist_name)],
        album=SpotifyAlbum(name=album_name),
        duration_ms=duration_ms,
        linked_from=SpotifyLinkedFrom(id=linked_from_id) if linked_from_id else None,
    )


def _make_connector_play(
    track_name: str = "Test Song",
    artist_name: str = "Test Artist",
    ms_played: int = 240000,
    track_uri: str = "spotify:track:4iV5W9uYEdYUVa79Axb7Rh",
    incognito: bool = False,
    **extra_metadata: object,
) -> ConnectorTrackPlay:
    """Create a ConnectorTrackPlay for testing."""
    return ConnectorTrackPlay(
        service="spotify",
        track_name=track_name,
        artist_name=artist_name,
        album_name="Test Album",
        played_at=datetime(2024, 6, 15, 14, 30, tzinfo=UTC),
        ms_played=ms_played,
        service_metadata={
            "track_uri": track_uri,
            "platform": "Linux",
            "country": "US",
            "reason_start": "trackdone",
            "reason_end": "trackdone",
            "shuffle": False,
            "skipped": False,
            "offline": False,
            "incognito_mode": incognito,
            **extra_metadata,
        },
        import_timestamp=datetime(2024, 7, 1, tzinfo=UTC),
        import_source="spotify_export",
        import_batch_id="test-batch",
    )


class TestShouldIncludeSpotifyPlay:
    """Test Spotify duration filtering rules."""

    def test_play_over_4min_always_included(self):
        assert should_include_spotify_play(250000, 300000) is True

    def test_play_exactly_4min_included(self):
        assert should_include_spotify_play(240000, 300000) is True

    def test_short_play_under_50_percent_excluded(self):
        """3-minute track played for 1 minute (33%) → excluded."""
        assert should_include_spotify_play(60000, 180000) is False

    def test_short_play_over_50_percent_included(self):
        """3-minute track played for 2 minutes (67%) → included."""
        assert should_include_spotify_play(120000, 180000) is True

    def test_long_track_under_4min_play_excluded(self):
        """10-minute track played for 3 minutes → excluded (track >= 8min, threshold is 4min)."""
        assert should_include_spotify_play(180000, 600000) is False

    def test_missing_duration_with_short_play_excluded(self):
        """No track duration info + under 4 minutes = exclude."""
        assert should_include_spotify_play(120000, None) is False

    def test_missing_duration_with_long_play_included(self):
        """No track duration but >= 4 minutes = always include."""
        assert should_include_spotify_play(250000, None) is True


class TestResolverEmptyInput:
    """Test resolver behavior with no input."""

    async def test_empty_plays_returns_empty_result(self):
        resolver = SpotifyConnectorPlayResolver(spotify_connector=MagicMock())
        uow = MagicMock()

        plays, metrics = await resolver.resolve_connector_plays([], uow)

        assert plays == []
        assert metrics["raw_plays"] == 0
        assert metrics["accepted_plays"] == 0
        assert metrics["error_count"] == 0


class TestResolverFiltering:
    """Test duration and incognito filtering during resolution."""

    @pytest.fixture
    def resolver_with_existing_tracks(self):
        """Resolver + mock UoW where all tracks resolve to existing canonical tracks."""
        connector = MagicMock()
        resolver = SpotifyConnectorPlayResolver(spotify_connector=connector)

        uow = MagicMock()
        # Existing connector mappings return a canonical track
        canonical_track = make_track(duration_ms=300000)  # 5-minute track
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {
            ("spotify", "4iV5W9uYEdYUVa79Axb7Rh"): canonical_track,
        }
        uow.get_connector_repository.return_value = connector_repo

        return resolver, uow

    async def test_incognito_plays_excluded(self, resolver_with_existing_tracks):
        resolver, uow = resolver_with_existing_tracks
        play = _make_connector_play(incognito=True, ms_played=300000)

        plays, metrics = await resolver.resolve_connector_plays([play], uow)

        assert len(plays) == 0
        assert metrics["incognito_excluded"] == 1

    async def test_duration_filtered_plays_excluded(
        self, resolver_with_existing_tracks
    ):
        """Play < 4 minutes on a long track should be excluded."""
        resolver, uow = resolver_with_existing_tracks
        # 5-minute canonical track, only 30 seconds played
        play = _make_connector_play(ms_played=30000)

        plays, metrics = await resolver.resolve_connector_plays([play], uow)

        assert len(plays) == 0
        assert metrics["duration_excluded"] == 1

    async def test_accepted_play_produces_track_play(
        self, resolver_with_existing_tracks
    ):
        resolver, uow = resolver_with_existing_tracks
        play = _make_connector_play(ms_played=300000)  # 5 min, well above threshold

        plays, metrics = await resolver.resolve_connector_plays([play], uow)

        assert len(plays) == 1
        assert isinstance(plays[0], TrackPlay)
        assert metrics["accepted_plays"] == 1

    async def test_accepted_play_preserves_rich_metadata(
        self, resolver_with_existing_tracks
    ):
        resolver, uow = resolver_with_existing_tracks
        play = _make_connector_play(ms_played=300000)

        plays, _ = await resolver.resolve_connector_plays([play], uow)

        context = plays[0].context
        assert context["platform"] == "Linux"
        assert context["country"] == "US"
        assert context["reason_start"] == "trackdone"
        assert context["reason_end"] == "trackdone"
        assert context["shuffle"] is False
        assert context["track_name"] == "Test Song"
        assert context["artist_name"] == "Test Artist"
        assert context["resolution_method"] == "spotify_connector_play_resolver"


class TestResolverTrackResolution:
    """Test canonical track creation and lookup."""

    async def test_existing_mapping_reuses_canonical_track(self):
        """Tracks with existing connector mappings should not call Spotify API."""
        connector = MagicMock()
        resolver = SpotifyConnectorPlayResolver(spotify_connector=connector)

        canonical = make_track(id=42)
        uow = MagicMock()
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {
            ("spotify", "4iV5W9uYEdYUVa79Axb7Rh"): canonical,
        }
        uow.get_connector_repository.return_value = connector_repo

        play = _make_connector_play(ms_played=300000)
        plays, metrics = await resolver.resolve_connector_plays([play], uow)

        assert len(plays) == 1
        assert plays[0].track_id == 42
        # Spotify API should NOT have been called since mapping existed
        connector.get_tracks_by_ids.assert_not_called()

    async def test_missing_mapping_creates_new_track_via_api(self):
        """Missing mappings should trigger Spotify API lookup + track creation."""
        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {
            "4iV5W9uYEdYUVa79Axb7Rh": _make_spotify_track("4iV5W9uYEdYUVa79Axb7Rh"),
        }
        resolver = SpotifyConnectorPlayResolver(spotify_connector=connector)

        uow = MagicMock()
        # No existing mappings
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        uow.get_connector_repository.return_value = connector_repo
        # save_track returns a track with ID
        track_repo = AsyncMock()
        saved_track = make_track(id=99)
        track_repo.save_track.return_value = saved_track
        uow.get_track_repository.return_value = track_repo

        play = _make_connector_play(ms_played=300000)
        plays, metrics = await resolver.resolve_connector_plays([play], uow)

        assert len(plays) == 1
        assert plays[0].track_id == 99
        assert metrics["new_tracks_count"] == 1
        connector.get_tracks_by_ids.assert_called_once()

    async def test_failed_resolution_logged_and_skipped(self):
        """Failed track resolution should be logged as error, not crash."""
        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {}  # No metadata found
        resolver = SpotifyConnectorPlayResolver(spotify_connector=connector)

        uow = MagicMock()
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        uow.get_connector_repository.return_value = connector_repo

        play = _make_connector_play(ms_played=300000)
        plays, metrics = await resolver.resolve_connector_plays([play], uow)

        assert len(plays) == 0
        assert metrics["error_count"] == 1
        assert len(metrics["resolution_failures"]) == 1
        assert metrics["resolution_failures"][0]["reason"] == "track_resolution_failed"

    async def test_no_valid_spotify_ids_returns_empty(self):
        """Plays with no extractable Spotify IDs should return empty."""
        connector = MagicMock()
        resolver = SpotifyConnectorPlayResolver(spotify_connector=connector)

        # Track URI that doesn't match spotify:track: pattern
        play = _make_connector_play(track_uri="invalid:uri:format")
        uow = MagicMock()

        plays, metrics = await resolver.resolve_connector_plays([play], uow)

        assert plays == []


class TestResolverRelinking:
    """Test Spotify relinking (track ID changes across markets)."""

    async def test_relinking_creates_both_mappings(self):
        """When Spotify relinks a track, both old and new IDs should be mapped."""
        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {
            "oldTrackId123456789012": _make_spotify_track(
                "newTrackId123456789012",
                linked_from_id="oldTrackId123456789012",
            ),
        }
        resolver = SpotifyConnectorPlayResolver(spotify_connector=connector)

        uow = MagicMock()
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        uow.get_connector_repository.return_value = connector_repo
        track_repo = AsyncMock()
        saved_track = make_track(id=55)
        track_repo.save_track.return_value = saved_track
        uow.get_track_repository.return_value = track_repo

        play = _make_connector_play(
            track_uri="spotify:track:oldTrackId123456789012",
            ms_played=300000,
        )

        plays, metrics = await resolver.resolve_connector_plays([play], uow)

        # map_track_to_connector should be called twice: primary + non-primary
        map_calls = uow.get_connector_repository.return_value.map_track_to_connector
        assert map_calls.call_count == 2

        # First call: primary mapping (new ID from API response)
        first_call = map_calls.call_args_list[0]
        assert first_call.args[2] == "newTrackId123456789012"  # connector_id
        assert first_call.kwargs["auto_set_primary"] is True

        # Second call: non-primary mapping (old ID from linked_from)
        second_call = map_calls.call_args_list[1]
        assert second_call.args[2] == "oldTrackId123456789012"
        assert second_call.kwargs["auto_set_primary"] is False


class TestResolverMetrics:
    """Test metrics dictionary structure and correctness."""

    async def test_metrics_include_all_expected_keys(self):
        resolver = SpotifyConnectorPlayResolver(spotify_connector=MagicMock())
        uow = MagicMock()

        _, metrics = await resolver.resolve_connector_plays([], uow)

        expected_keys = {
            "raw_plays",
            "accepted_plays",
            "duration_excluded",
            "incognito_excluded",
            "error_count",
            "resolution_failures",
            "new_tracks_count",
            "updated_tracks_count",
            "unique_tracks_processed",
            "tracks_resolved",
        }
        assert expected_keys == set(metrics.keys())

    async def test_mixed_play_metrics_correct(self):
        """Multiple plays with different outcomes should produce correct aggregate metrics."""
        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {}
        resolver = SpotifyConnectorPlayResolver(spotify_connector=connector)

        canonical = make_track(id=1, duration_ms=300000)
        uow = MagicMock()
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {
            ("spotify", "4iV5W9uYEdYUVa79Axb7Rh"): canonical,
        }
        uow.get_connector_repository.return_value = connector_repo

        plays = [
            _make_connector_play(ms_played=300000),  # accepted
            _make_connector_play(ms_played=5000),  # duration filtered
            _make_connector_play(
                ms_played=300000, incognito=True
            ),  # incognito filtered
        ]

        result, metrics = await resolver.resolve_connector_plays(plays, uow)

        assert metrics["raw_plays"] == 3
        assert metrics["accepted_plays"] == 1
        assert metrics["duration_excluded"] == 1
        assert metrics["incognito_excluded"] == 1
