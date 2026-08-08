"""Unit tests for SpotifyConnectorPlayResolver.

Tests the resolver's core business logic: duration filtering, incognito filtering,
track resolution, relinking, metadata preservation, and error handling.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from src.config.constants import MatchMethod, SpotifyConstants
from src.domain.entities import ConnectorTrackPlay, TrackPlay
from src.infrastructure.connectors.spotify.client import (
    field_filtered_search_query,
    free_text_search_query,
)
from src.infrastructure.connectors.spotify.play_resolver import (
    SpotifyConnectorPlayResolver,
    should_include_spotify_play,
)
from tests.fixtures import attach_resolution_recorder
from tests.fixtures.factories import make_spotify_track, make_track


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
        attach_resolution_recorder(uow)

        outcome = await resolver.resolve_connector_plays([], uow, user_id="test-user")
        plays, metrics = outcome.track_plays, outcome.metrics

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

        attach_resolution_recorder(uow)
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

        outcome = await resolver.resolve_connector_plays(
            [play], uow, user_id="test-user"
        )
        plays, metrics = outcome.track_plays, outcome.metrics

        assert len(plays) == 0
        assert metrics["incognito_excluded"] == 1

    async def test_duration_filtered_plays_excluded(
        self, resolver_with_existing_tracks
    ):
        """Play < 4 minutes on a long track should be excluded."""
        resolver, uow = resolver_with_existing_tracks
        # 5-minute canonical track, only 30 seconds played
        play = _make_connector_play(ms_played=30000)

        outcome = await resolver.resolve_connector_plays(
            [play], uow, user_id="test-user"
        )
        plays, metrics = outcome.track_plays, outcome.metrics

        assert len(plays) == 0
        assert metrics["duration_excluded"] == 1

    async def test_accepted_play_produces_track_play(
        self, resolver_with_existing_tracks
    ):
        resolver, uow = resolver_with_existing_tracks
        play = _make_connector_play(ms_played=300000)  # 5 min, well above threshold

        outcome = await resolver.resolve_connector_plays(
            [play], uow, user_id="test-user"
        )
        plays, metrics = outcome.track_plays, outcome.metrics

        assert len(plays) == 1
        assert isinstance(plays[0], TrackPlay)
        assert metrics["accepted_plays"] == 1

    async def test_accepted_play_preserves_rich_metadata(
        self, resolver_with_existing_tracks
    ):
        resolver, uow = resolver_with_existing_tracks
        play = _make_connector_play(ms_played=300000)

        outcome = await resolver.resolve_connector_plays(
            [play], uow, user_id="test-user"
        )
        plays, _ = outcome.track_plays, outcome.metrics

        context = plays[0].context
        assert context["platform"] == "Linux"
        assert context["country"] == "US"
        assert context["reason_start"] == "trackdone"
        assert context["reason_end"] == "trackdone"
        assert context["shuffle"] is False
        assert context["track_name"] == "Test Song"
        assert context["artist_name"] == "Test Artist"
        assert context["resolution_method"] == MatchMethod.PLAY_RESOLVER


class TestResolverContextKeys:
    """The persisted context key set is pinned (track_plays.context JSON contract)."""

    async def test_resolved_play_context_keys_are_byte_identical(self):
        connector = MagicMock()
        resolver = SpotifyConnectorPlayResolver(spotify_connector=connector)

        uow = MagicMock()

        attach_resolution_recorder(uow)
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {
            ("spotify", "4iV5W9uYEdYUVa79Axb7Rh"): make_track(duration_ms=300000),
        }
        uow.get_connector_repository.return_value = connector_repo

        play = _make_connector_play(ms_played=300000)
        outcome = await resolver.resolve_connector_plays(
            [play], uow, user_id="test-user"
        )
        plays, _ = outcome.track_plays, outcome.metrics

        context = plays[0].context
        assert set(context.keys()) == {
            "track_name",
            "artist_name",
            "album_name",
            "platform",
            "country",
            "reason_start",
            "reason_end",
            "shuffle",
            "skipped",
            "offline",
            "incognito_mode",
            "spotify_track_uri",
            "spotify_track_id",
            "resolution_method",
            "architecture_version",
        }
        assert context["architecture_version"] == "connector_plays_deferred_resolution"
        assert context["spotify_track_id"] == "4iV5W9uYEdYUVa79Axb7Rh"

    async def test_unrecognized_metadata_passed_through(self):
        """Extra service_metadata keys survive into context verbatim."""
        connector = MagicMock()
        resolver = SpotifyConnectorPlayResolver(spotify_connector=connector)

        uow = MagicMock()

        attach_resolution_recorder(uow)
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {
            ("spotify", "4iV5W9uYEdYUVa79Axb7Rh"): make_track(duration_ms=300000),
        }
        uow.get_connector_repository.return_value = connector_repo

        play = _make_connector_play(ms_played=300000, episode_show="A Podcast")
        outcome = await resolver.resolve_connector_plays(
            [play], uow, user_id="test-user"
        )
        plays, _ = outcome.track_plays, outcome.metrics

        assert plays[0].context["episode_show"] == "A Podcast"


class TestResolverTrackResolution:
    """Test canonical track creation and lookup."""

    async def test_existing_mapping_reuses_canonical_track(self):
        """Tracks with existing connector mappings should not call Spotify API."""
        connector = MagicMock()
        resolver = SpotifyConnectorPlayResolver(spotify_connector=connector)

        canonical = make_track(id=42)
        uow = MagicMock()
        attach_resolution_recorder(uow)
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {
            ("spotify", "4iV5W9uYEdYUVa79Axb7Rh"): canonical,
        }
        uow.get_connector_repository.return_value = connector_repo

        play = _make_connector_play(ms_played=300000)
        outcome = await resolver.resolve_connector_plays(
            [play], uow, user_id="test-user"
        )
        plays, metrics = outcome.track_plays, outcome.metrics

        assert len(plays) == 1
        assert plays[0].track_id == 42
        # Spotify API should NOT have been called since mapping existed
        connector.get_tracks_by_ids.assert_not_called()

    async def test_missing_mapping_creates_new_track_via_api(self):
        """Missing mappings should trigger Spotify API lookup + track creation."""
        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {
            "4iV5W9uYEdYUVa79Axb7Rh": make_spotify_track("4iV5W9uYEdYUVa79Axb7Rh"),
        }
        resolver = SpotifyConnectorPlayResolver(spotify_connector=connector)

        uow = MagicMock()

        attach_resolution_recorder(uow)
        # No existing mappings
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        uow.get_connector_repository.return_value = connector_repo
        # save_track returns a track with ID
        track_repo = AsyncMock()
        saved_track = make_track(id=99)
        track_repo.save_track.return_value = saved_track
        track_repo.find_tracks_by_title_artist.return_value = {}
        uow.get_track_repository.return_value = track_repo

        play = _make_connector_play(ms_played=300000)
        outcome = await resolver.resolve_connector_plays(
            [play], uow, user_id="test-user"
        )
        plays, metrics = outcome.track_plays, outcome.metrics

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

        attach_resolution_recorder(uow)
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        uow.get_connector_repository.return_value = connector_repo
        track_repo = AsyncMock()
        track_repo.find_tracks_by_title_artist.return_value = {}
        uow.get_track_repository.return_value = track_repo

        play = _make_connector_play(ms_played=300000)
        outcome = await resolver.resolve_connector_plays(
            [play], uow, user_id="test-user"
        )
        plays, metrics = outcome.track_plays, outcome.metrics

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
        attach_resolution_recorder(uow)

        outcome = await resolver.resolve_connector_plays(
            [play], uow, user_id="test-user"
        )
        plays, metrics = outcome.track_plays, outcome.metrics

        assert plays == []


class TestFallbackHintDurationEstimate:
    """The export has no track length; completed plays are the stand-in.

    ``ms_played`` is time in the player, not distance through the track, so a
    listener who seeks backwards inflates one play and one who seeks forwards
    deflates it — both while still ending ``trackdone``. The median absorbs
    either; a max would enshrine the inflated one.
    """

    URI = "spotify:track:4iV5W9uYEdYUVa79Axb7Rh"

    def _hint(self, plays: list[ConnectorTrackPlay]):
        resolver = SpotifyConnectorPlayResolver(spotify_connector=AsyncMock())
        _ids, hints = resolver._extract_ids_and_hints(plays)
        return hints["4iV5W9uYEdYUVa79Axb7Rh"]

    def test_a_single_completed_play_is_its_own_median(self):
        hint = self._hint([_make_connector_play(ms_played=216_000)])

        assert hint.completed_play_ms_estimate == 216_000

    def test_the_median_ignores_a_seek_inflated_outlier(self):
        """One replayed-through play must not move the estimate."""
        hint = self._hint([
            _make_connector_play(ms_played=214_000),
            _make_connector_play(ms_played=216_000),
            _make_connector_play(ms_played=21_600_000),
        ])

        assert hint.completed_play_ms_estimate == 216_000

    def test_an_even_count_averages_the_two_middle_plays(self):
        hint = self._hint([
            _make_connector_play(ms_played=210_000),
            _make_connector_play(ms_played=214_000),
            _make_connector_play(ms_played=218_000),
            _make_connector_play(ms_played=600_000),
        ])

        assert hint.completed_play_ms_estimate == 216_000

    def test_skipped_plays_yield_no_estimate_at_all(self):
        """A play that was cut short says nothing about how long the track is."""
        hint = self._hint([
            _make_connector_play(ms_played=30_000, reason_end="fwdbtn"),
            _make_connector_play(ms_played=12_000, reason_end="endplay"),
        ])

        assert hint.completed_play_ms_estimate is None

    def test_only_completed_plays_contribute(self):
        hint = self._hint([
            _make_connector_play(ms_played=30_000, reason_end="fwdbtn"),
            _make_connector_play(ms_played=216_000),
        ])

        assert hint.completed_play_ms_estimate == 216_000

    def test_names_still_come_from_the_first_play_carrying_the_id(self):
        hint = self._hint([
            _make_connector_play(
                artist_name="Johnny Cash", track_name="Hurt", ms_played=216_000
            ),
            _make_connector_play(
                artist_name="Johnny Cash (Remastered)",
                track_name="Hurt - 2002",
                ms_played=218_000,
            ),
        ])

        assert (hint.artist_name, hint.track_name) == ("Johnny Cash", "Hurt")

    def test_estimates_are_kept_per_track_id(self):
        other_uri = "spotify:track:5rHtvcQXTiZbjPGYAOMQMP"
        resolver = SpotifyConnectorPlayResolver(spotify_connector=AsyncMock())

        _ids, hints = resolver._extract_ids_and_hints([
            _make_connector_play(ms_played=216_000),
            _make_connector_play(ms_played=400_000, track_uri=other_uri),
        ])

        assert hints["4iV5W9uYEdYUVa79Axb7Rh"].completed_play_ms_estimate == 216_000
        assert hints["5rHtvcQXTiZbjPGYAOMQMP"].completed_play_ms_estimate == 400_000


class TestDurationEstimateAccumulatesAcrossChunks:
    """The estimate is run-scoped, because the plays that prove it are.

    The orchestrator hands the resolver 50 plays at a time, so a track played
    to completion a handful of times across a decade of history has its plays
    scattered over many chunks. Derived from one chunk, the estimate was
    routinely absent for exactly the ids that needed it — and absent means the
    wrong-version veto asserts nothing at all.
    """

    def test_the_median_spans_every_chunk_seen_so_far(self):
        resolver = SpotifyConnectorPlayResolver(spotify_connector=AsyncMock())

        _ids, first = resolver._extract_ids_and_hints([
            _make_connector_play(ms_played=210_000),
        ])
        _ids, second = resolver._extract_ids_and_hints([
            _make_connector_play(ms_played=214_000),
            _make_connector_play(ms_played=218_000),
        ])

        assert first["4iV5W9uYEdYUVa79Axb7Rh"].completed_play_ms_estimate == 210_000
        # 210k/214k/218k — the earlier chunk's play is still evidence.
        assert second["4iV5W9uYEdYUVa79Axb7Rh"].completed_play_ms_estimate == 214_000

    def test_a_chunk_with_no_completed_play_inherits_the_earlier_estimate(self):
        """The chunk holding a dead id often holds none of its finished plays."""
        resolver = SpotifyConnectorPlayResolver(spotify_connector=AsyncMock())

        _ids, _first = resolver._extract_ids_and_hints([
            _make_connector_play(ms_played=216_000),
        ])
        _ids, second = resolver._extract_ids_and_hints([
            _make_connector_play(ms_played=30_000, reason_end="fwdbtn"),
        ])

        assert second["4iV5W9uYEdYUVa79Axb7Rh"].completed_play_ms_estimate == 216_000

    def test_the_accumulator_belongs_to_one_run_only(self):
        """A second import gets a fresh resolver, and a fresh accumulator."""
        first = SpotifyConnectorPlayResolver(spotify_connector=AsyncMock())
        _ids, _hints = first._extract_ids_and_hints([
            _make_connector_play(ms_played=216_000),
        ])

        second = SpotifyConnectorPlayResolver(spotify_connector=AsyncMock())
        _ids, hints = second._extract_ids_and_hints([
            _make_connector_play(ms_played=30_000, reason_end="fwdbtn"),
        ])

        assert hints["4iV5W9uYEdYUVa79Axb7Rh"].completed_play_ms_estimate is None


class TestFallbackHintsIntegration:
    """Test that play resolver builds and passes fallback hints correctly."""

    async def test_fallback_hints_built_from_connector_plays(self):
        """resolve_connector_plays should build FallbackHints from play metadata."""
        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {}
        connector.search_track.return_value = []
        resolver = SpotifyConnectorPlayResolver(spotify_connector=connector)

        uow = MagicMock()

        attach_resolution_recorder(uow)
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        uow.get_connector_repository.return_value = connector_repo
        track_repo = AsyncMock()
        track_repo.find_tracks_by_title_artist.return_value = {}
        uow.get_track_repository.return_value = track_repo

        play = _make_connector_play(
            track_name="My Song", artist_name="My Artist", ms_played=300000
        )
        await resolver.resolve_connector_plays([play], uow, user_id="test-user")

        # The inward resolver should have received fallback hints
        # We verify indirectly by checking search was attempted for the dead ID
        # (since get_tracks_by_ids returned empty, ID is dead, hint should trigger search)
        # Two calls: the field-filtered query, then the single widening pass.
        assert connector.search_track.await_args_list == [
            call(
                field_filtered_search_query("My Artist", "My Song"),
                SpotifyConstants.SEARCH_MAX_LIMIT,
            ),
            call(
                free_text_search_query("My Artist", "My Song"),
                SpotifyConstants.SEARCH_MAX_LIMIT,
            ),
        ]

    async def test_fallback_resolved_plays_tagged_in_context(self):
        """Plays resolved via fallback should have resolution_method='search_fallback'."""
        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {}
        search_result = make_spotify_track(
            "new_id", name="Test Song", artist_name="Test Artist"
        )
        connector.search_track.return_value = [search_result]

        resolver = SpotifyConnectorPlayResolver(spotify_connector=connector)

        uow = MagicMock()

        attach_resolution_recorder(uow)
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        uow.get_connector_repository.return_value = connector_repo
        track_repo = AsyncMock()
        saved_track = make_track(id=99)
        track_repo.save_track.return_value = saved_track
        track_repo.find_tracks_by_title_artist.return_value = {}
        uow.get_track_repository.return_value = track_repo

        # ms_played matches the candidate's length: a completed play is the
        # only duration evidence a dead id has, and a candidate materially
        # shorter than it is vetoed as a different version.
        play = _make_connector_play(ms_played=240000)
        outcome = await resolver.resolve_connector_plays(
            [play], uow, user_id="test-user"
        )
        plays, metrics = outcome.track_plays, outcome.metrics

        assert len(plays) == 1
        assert plays[0].context["resolution_method"] == MatchMethod.SEARCH_FALLBACK
        assert metrics["fallback_resolved"] == 1


class TestRedirectResolvedPlays:
    """Test that redirect-resolved plays are correctly tagged and metricked."""

    async def test_redirect_resolved_plays_tagged(self):
        """Plays resolved via redirect should have resolution_method='spotify_redirect'."""
        connector = AsyncMock()
        # Return track with DIFFERENT id than requested (redirect)
        redirected_track = make_spotify_track(
            "new_canonical_id_000000", name="Test Song"
        )
        connector.get_tracks_by_ids.return_value = {
            "4iV5W9uYEdYUVa79Axb7Rh": redirected_track,
        }

        resolver = SpotifyConnectorPlayResolver(spotify_connector=connector)

        uow = MagicMock()

        attach_resolution_recorder(uow)
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        uow.get_connector_repository.return_value = connector_repo
        track_repo = AsyncMock()
        saved_track = make_track(id=99)
        track_repo.save_track.return_value = saved_track
        track_repo.find_tracks_by_title_artist.return_value = {}
        uow.get_track_repository.return_value = track_repo

        play = _make_connector_play(ms_played=300000)
        outcome = await resolver.resolve_connector_plays(
            [play], uow, user_id="test-user"
        )
        plays, metrics = outcome.track_plays, outcome.metrics

        assert len(plays) == 1
        assert plays[0].context["resolution_method"] == MatchMethod.SPOTIFY_REDIRECT
        assert metrics["redirect_resolved"] == 1

    async def test_redirect_resolved_metric(self):
        """redirect_resolved metric should count redirected IDs."""
        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {
            "4iV5W9uYEdYUVa79Axb7Rh": make_spotify_track("new_id_0000000000000000"),
        }

        resolver = SpotifyConnectorPlayResolver(spotify_connector=connector)

        uow = MagicMock()

        attach_resolution_recorder(uow)
        connector_repo = AsyncMock()
        connector_repo.find_tracks_by_connectors.return_value = {}
        uow.get_connector_repository.return_value = connector_repo
        track_repo = AsyncMock()
        track_repo.save_track.return_value = make_track(id=99)
        track_repo.find_tracks_by_title_artist.return_value = {}
        uow.get_track_repository.return_value = track_repo

        play = _make_connector_play(ms_played=300000)
        outcome = await resolver.resolve_connector_plays(
            [play], uow, user_id="test-user"
        )
        _, metrics = outcome.track_plays, outcome.metrics

        assert metrics["redirect_resolved"] == 1


class TestResolverMetrics:
    """Test metrics dictionary structure and correctness."""

    async def test_metrics_include_all_expected_keys(self):
        resolver = SpotifyConnectorPlayResolver(spotify_connector=MagicMock())
        uow = MagicMock()
        attach_resolution_recorder(uow)

        outcome = await resolver.resolve_connector_plays([], uow, user_id="test-user")
        _, metrics = outcome.track_plays, outcome.metrics

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
            "fallback_resolved",
            "redirect_resolved",
            "dead_ids_unresolved",
            "isrc_suspect_deferred",
        }
        assert expected_keys == set(metrics.keys())

    async def test_mixed_play_metrics_correct(self):
        """Multiple plays with different outcomes should produce correct aggregate metrics."""
        connector = AsyncMock()
        connector.get_tracks_by_ids.return_value = {}
        resolver = SpotifyConnectorPlayResolver(spotify_connector=connector)

        canonical = make_track(id=1, duration_ms=300000)
        uow = MagicMock()
        attach_resolution_recorder(uow)
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

        outcome = await resolver.resolve_connector_plays(
            plays, uow, user_id="test-user"
        )
        result, metrics = outcome.track_plays, outcome.metrics

        assert metrics["raw_plays"] == 3
        assert metrics["accepted_plays"] == 1
        assert metrics["duration_excluded"] == 1
        assert metrics["incognito_excluded"] == 1
