"""Tests for Spotify utility functions.

Validates create_track_from_spotify_data correctly converts SpotifyTrack Pydantic
models into domain Track entities with proper field mapping and validation.
Also tests the shared widening search ladder and the
search -> rank -> evaluate pipeline built on it.
"""

from contextlib import aclosing
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from src.config import create_matching_config
from src.config.constants import SpotifyConstants
from src.domain.entities import Artist
from src.domain.matching.evaluation_service import TrackMatchEvaluationService
from src.infrastructure.connectors.spotify.client import (
    field_filtered_search_query,
    free_text_search_query,
)
from src.infrastructure.connectors.spotify.models import (
    SpotifyAlbum,
    SpotifyArtist,
    SpotifyExternalIds,
    SpotifyTrack,
)
from src.infrastructure.connectors.spotify.utilities import (
    SpotifySearchMatch,
    create_track_from_spotify_data,
    normalized_spotify_isrc,
    search_and_evaluate_attempt,
    widening_search_passes,
)
from tests.fixtures import make_track


class TestCreateTrackFromSpotifyData:
    """Happy path: valid SpotifyTrack produces correct domain Track."""

    def test_basic_track_conversion(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Test Song",
            artists=[SpotifyArtist(name="Test Artist")],
            album=SpotifyAlbum(name="Test Album"),
            duration_ms=240000,
            external_ids=SpotifyExternalIds(isrc="USRC12345678"),
        )

        track = create_track_from_spotify_data(
            "abc123", spotify_track, user_id="tenant-a"
        )

        assert track.title == "Test Song"
        assert track.artists == [Artist(name="Test Artist")]
        assert track.album == "Test Album"
        assert track.duration_ms == 240000
        assert track.isrc == "USRC12345678"
        assert track.connector_track_identifiers.get("spotify") == "abc123"

    def test_the_created_track_carries_the_callers_tenant(self):
        """user_id is keyword-only with no default — Track's silent
        "default" fallback is exactly the mistenancy this pins against."""
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Test Song",
            artists=[SpotifyArtist(name="Test Artist")],
        )

        track = create_track_from_spotify_data(
            "abc123", spotify_track, user_id="tenant-a"
        )

        assert track.user_id == "tenant-a"

    def test_a_hyphenated_lowercase_isrc_is_normalized(self):
        """Raw provider ISRCs must land in the canonical form the user-scoped
        unique key (uq_tracks_user_isrc) dedupes against."""
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Test Song",
            artists=[SpotifyArtist(name="Test Artist")],
            external_ids=SpotifyExternalIds(isrc="us-rc1-23-45678"),
        )

        track = create_track_from_spotify_data(
            "abc123", spotify_track, user_id="tenant-a"
        )

        assert track.isrc == "USRC12345678"

    def test_multiple_artists(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Collab Song",
            artists=[
                SpotifyArtist(name="Artist A"),
                SpotifyArtist(name="Artist B"),
            ],
        )

        track = create_track_from_spotify_data(
            "abc123", spotify_track, user_id="tenant-a"
        )

        assert len(track.artists) == 2
        assert track.artists[0].name == "Artist A"
        assert track.artists[1].name == "Artist B"

    def test_no_album(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Single",
            artists=[SpotifyArtist(name="Artist")],
            album=None,
        )

        track = create_track_from_spotify_data(
            "abc123", spotify_track, user_id="tenant-a"
        )

        assert track.album is None

    def test_zero_duration_treated_as_none(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Song",
            artists=[SpotifyArtist(name="Artist")],
            duration_ms=0,
        )

        track = create_track_from_spotify_data(
            "abc123", spotify_track, user_id="tenant-a"
        )

        assert track.duration_ms is None

    def test_no_isrc(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Song",
            artists=[SpotifyArtist(name="Artist")],
        )

        track = create_track_from_spotify_data(
            "abc123", spotify_track, user_id="tenant-a"
        )

        assert track.isrc is None


class TestNormalizedSpotifyIsrc:
    """The one wrapper every Spotify caller shares for ISRC normalization."""

    def _track_with_isrc(self, isrc: str | None) -> SpotifyTrack:
        return SpotifyTrack(
            id="abc123",
            name="Song",
            artists=[SpotifyArtist(name="Artist")],
            external_ids=SpotifyExternalIds(isrc=isrc),
        )

    def test_hyphens_removed_and_uppercased(self):
        assert (
            normalized_spotify_isrc(self._track_with_isrc("us-rc1-23-45678"))
            == "USRC12345678"
        )

    def test_missing_isrc_is_none(self):
        assert normalized_spotify_isrc(self._track_with_isrc(None)) is None

    def test_defaulted_external_ids_is_none(self):
        """external_ids has a default_factory, so the guard is on the value."""
        track = SpotifyTrack(
            id="abc123", name="Song", artists=[SpotifyArtist(name="Artist")]
        )

        assert normalized_spotify_isrc(track) is None

    def test_malformed_isrc_is_none(self):
        assert normalized_spotify_isrc(self._track_with_isrc("NOT AN ISRC")) is None


class TestCreateTrackFromSpotifyDataValidation:
    """Error cases: missing or invalid data raises ValueError."""

    def test_empty_name_raises(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="",
            artists=[SpotifyArtist(name="Artist")],
        )

        with pytest.raises(ValueError, match="Missing track title"):
            create_track_from_spotify_data("abc123", spotify_track, user_id="tenant-a")

    def test_no_artists_raises(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Song",
            artists=[],
        )

        with pytest.raises(ValueError, match="Missing artists"):
            create_track_from_spotify_data("abc123", spotify_track, user_id="tenant-a")

    def test_artists_with_empty_names_raises(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Song",
            artists=[SpotifyArtist(name=""), SpotifyArtist(name="")],
        )

        with pytest.raises(ValueError, match="No valid artist names"):
            create_track_from_spotify_data("abc123", spotify_track, user_id="tenant-a")

    def test_skips_empty_artist_names(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Song",
            artists=[SpotifyArtist(name=""), SpotifyArtist(name="Valid")],
        )

        track = create_track_from_spotify_data(
            "abc123", spotify_track, user_id="tenant-a"
        )

        assert len(track.artists) == 1
        assert track.artists[0].name == "Valid"


# --- widening ladder + search_and_evaluate_attempt tests ---


def _make_candidate(
    *,
    track_id: str | None = "sp123",
    name: str = "Creep",
    artist: str = "Radiohead",
    duration_ms: int = 238000,
) -> MagicMock:
    artist_mock = MagicMock()
    artist_mock.name = artist

    candidate = MagicMock()
    candidate.id = track_id
    candidate.name = name
    candidate.artists = [artist_mock]
    candidate.duration_ms = duration_ms
    return candidate


def _make_connector(candidates: list[MagicMock] | None = None) -> AsyncMock:
    connector = AsyncMock()
    connector.search_track.return_value = candidates or []
    connector.connector_name = "spotify"
    return connector


@pytest.fixture
def evaluation_service() -> TrackMatchEvaluationService:
    return TrackMatchEvaluationService(config=create_matching_config())


class TestWideningSearchPasses:
    """The ladder both search callers share: filtered first, free text second."""

    async def test_the_first_pass_is_the_field_filtered_query(self):
        connector = _make_connector([_make_candidate()])

        passes = widening_search_passes(connector, "Radiohead", "Creep", 5)
        async with aclosing(passes):
            first = await anext(passes)

        assert first.query == field_filtered_search_query("Radiohead", "Creep")
        assert connector.search_track.await_args_list == [call(first.query, 5)]

    async def test_the_second_pass_is_the_free_text_widening(self):
        connector = _make_connector([_make_candidate()])

        passes = widening_search_passes(connector, "Radiohead", "Creep", 5)
        async with aclosing(passes):
            _ = await anext(passes)
            second = await anext(passes)

        assert second.query == free_text_search_query("Radiohead", "Creep")
        assert connector.search_track.await_args_list[1] == call(second.query, 5)

    async def test_the_second_search_waits_for_the_consumer_to_ask(self):
        """The whole cost bound: a consumer that stops pays one /search, not two."""
        connector = _make_connector([_make_candidate()])

        passes = widening_search_passes(connector, "Radiohead", "Creep", 5)
        async with aclosing(passes):
            _ = await anext(passes)
            assert connector.search_track.await_count == 1

        assert connector.search_track.await_count == 1

    async def test_the_ladder_stops_after_two_rungs(self):
        connector = _make_connector([])

        collected = [
            search_pass
            async for search_pass in widening_search_passes(
                connector, "Radiohead", "Creep", 5
            )
        ]

        assert len(collected) == 2
        assert connector.search_track.await_count == 2

    async def test_every_pass_carries_the_query_that_went_on_the_wire(self):
        """Telemetry and the request read one string, not two built in parallel."""
        connector = _make_connector([])

        collected = [
            search_pass
            async for search_pass in widening_search_passes(
                connector, 'The "Real" Band', 'Move Your Body (12" Mix)', 5
            )
        ]

        sent = [c.args[0] for c in connector.search_track.await_args_list]
        assert [p.query for p in collected] == sent


class TestSearchAndEvaluateHappyPath:
    """Successful search should return SpotifySearchMatch with correct fields."""

    async def test_returns_match_with_correct_fields(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        candidate = _make_candidate()
        connector = _make_connector([candidate])
        track = make_track(id=1, title="Creep", artist="Radiohead")

        attempt = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            track,
            "Radiohead",
            "Creep",
            widen=True,
            require_success=True,
        )

        assert isinstance(attempt.match, SpotifySearchMatch)
        assert attempt.match.candidate is candidate
        assert attempt.match.similarity > 0.0
        assert attempt.match.match_result.confidence > 0
        # An accepted first pass never widens.
        connector.search_track.assert_called_once_with(
            field_filtered_search_query("Radiohead", "Creep"),
            SpotifyConstants.SEARCH_DEFAULT_LIMIT,
        )


class TestSearchAndEvaluateNoCandidates:
    """Empty search results should produce no match."""

    async def test_returns_none_when_no_candidates(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        connector = _make_connector([])
        track = make_track(id=1)

        attempt = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            track,
            "Unknown",
            "Song",
            widen=False,
            require_success=False,
        )

        assert attempt.match is None


class TestSearchAndEvaluateBelowThreshold:
    """Candidates below min_similarity should be rejected."""

    async def test_returns_none_below_threshold(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        candidate = _make_candidate(name="Completely Different Title")
        connector = _make_connector([candidate])
        track = make_track(id=1, title="Creep", artist="Radiohead")

        attempt = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            track,
            "Radiohead",
            "Creep",
            widen=False,
            require_success=False,
            min_similarity=0.99,
        )

        assert attempt.match is None


class TestSearchAndEvaluateConnectorId:
    """A candidate without .id is usable only when the caller supplies a fallback."""

    async def test_returns_none_when_no_id(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        connector = _make_connector([_make_candidate(track_id=None)])
        track = make_track(id=1, title="Creep", artist="Radiohead")

        attempt = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            track,
            "Radiohead",
            "Creep",
            widen=False,
            require_success=False,
        )

        assert attempt.match is None

    async def test_uses_fallback_connector_id(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        candidate = _make_candidate(track_id=None)
        connector = _make_connector([candidate])
        track = make_track(id=1, title="Creep", artist="Radiohead")

        attempt = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            track,
            "Radiohead",
            "Creep",
            widen=False,
            require_success=False,
            fallback_connector_id="dead_id_123",
        )

        assert attempt.match is not None
        assert attempt.match.candidate is candidate
        assert attempt.match.match_result.connector_id == "dead_id_123"


class TestWideningIsOptIn:
    """``widen`` is required at every call site because the cost is per-caller."""

    async def test_widen_false_issues_exactly_one_search_on_a_miss(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        """The cross-discovery bound: a run of misses must not double its volume."""
        connector = _make_connector()
        connector.search_track.side_effect = [[], [_make_candidate()]]
        track = make_track(id=1, title="Creep", artist="Radiohead")

        attempt = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            track,
            "Radiohead",
            "Creep",
            widen=False,
            require_success=False,
        )

        assert attempt.match is None
        assert connector.search_track.await_count == 1
        assert attempt.queries == (field_filtered_search_query("Radiohead", "Creep"),)

    async def test_widen_true_issues_the_second_search_on_a_miss(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        connector = _make_connector()
        connector.search_track.side_effect = [[], [_make_candidate()]]
        track = make_track(id=1, title="Creep", artist="Radiohead")

        attempt = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            track,
            "Radiohead",
            "Creep",
            widen=True,
            require_success=True,
        )

        assert attempt.match is not None
        assert connector.search_track.await_args_list == [
            call(
                field_filtered_search_query("Radiohead", "Creep"),
                SpotifyConstants.SEARCH_DEFAULT_LIMIT,
            ),
            call(
                free_text_search_query("Radiohead", "Creep"),
                SpotifyConstants.SEARCH_DEFAULT_LIMIT,
            ),
        ]

    async def test_widening_is_bounded_to_exactly_one_extra_pass(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        connector = _make_connector()
        connector.search_track.side_effect = [[], []]
        track = make_track(id=1, title="Creep", artist="Radiohead")

        attempt = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            track,
            "Radiohead",
            "Creep",
            widen=True,
            require_success=True,
        )

        assert attempt.match is None
        assert connector.search_track.await_count == 2

    async def test_a_junk_first_pass_still_widens(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        """Returning junk is not the same as clearing the gate."""
        connector = _make_connector()
        connector.search_track.side_effect = [
            [_make_candidate(name="Something Else Entirely")],
            [_make_candidate()],
        ]
        track = make_track(id=1, title="Creep", artist="Radiohead")

        attempt = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            track,
            "Radiohead",
            "Creep",
            widen=True,
            require_success=True,
            min_similarity=0.7,
        )

        assert attempt.match is not None
        assert connector.search_track.await_count == 2

    async def test_the_limit_is_the_same_on_both_passes(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        connector = _make_connector()
        connector.search_track.side_effect = [[], []]
        track = make_track(id=1, title="Creep", artist="Radiohead")

        _ = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            track,
            "Radiohead",
            "Creep",
            widen=True,
            require_success=True,
            limit=SpotifyConstants.SEARCH_MAX_LIMIT,
        )

        for search_call in connector.search_track.await_args_list:
            assert search_call.args[1] == SpotifyConstants.SEARCH_MAX_LIMIT


class TestRequireSuccess:
    """Title similarity ranks candidates; only evaluation says they are the track."""

    ARTIST = "Johnny Cash"
    TITLE = "Hurt"

    def _wrong_artist_candidate(self) -> MagicMock:
        # Same title, different performer, and a length that gives the
        # evaluator something to disagree with.
        return _make_candidate(
            track_id="nin_hurt",
            name="Hurt",
            artist="Nine Inch Nails",
            duration_ms=373000,
        )

    async def test_a_failing_evaluation_is_not_a_match(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        connector = _make_connector([self._wrong_artist_candidate()])
        track = make_track(
            id=1, title=self.TITLE, artist=self.ARTIST, duration_ms=216000
        )

        attempt = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            track,
            self.ARTIST,
            self.TITLE,
            widen=False,
            require_success=True,
            min_similarity=0.7,
        )

        assert attempt.match is None

    async def test_the_same_candidate_survives_when_success_is_not_required(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        """Cross-discovery keeps the rejected match so it can log its confidence."""
        connector = _make_connector([self._wrong_artist_candidate()])
        track = make_track(
            id=1, title=self.TITLE, artist=self.ARTIST, duration_ms=216000
        )

        attempt = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            track,
            self.ARTIST,
            self.TITLE,
            widen=False,
            require_success=False,
            min_similarity=0.7,
        )

        assert attempt.match is not None
        assert attempt.match.match_result.success is False

    async def test_the_gate_applies_to_the_widened_pass_too(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        """The free-text query does not constrain the artist at all."""
        connector = _make_connector()
        connector.search_track.side_effect = [[], [self._wrong_artist_candidate()]]
        track = make_track(
            id=1, title=self.TITLE, artist=self.ARTIST, duration_ms=216000
        )

        attempt = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            track,
            self.ARTIST,
            self.TITLE,
            widen=True,
            require_success=True,
            min_similarity=0.7,
        )

        assert attempt.match is None
        assert connector.search_track.await_count == 2

    async def test_a_first_pass_failure_does_not_stop_the_widened_rescue(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        connector = _make_connector()
        connector.search_track.side_effect = [
            [self._wrong_artist_candidate()],
            [
                _make_candidate(
                    track_id="jc_hurt",
                    name="Hurt",
                    artist="Johnny Cash",
                    duration_ms=216000,
                )
            ],
        ]
        track = make_track(
            id=1, title=self.TITLE, artist=self.ARTIST, duration_ms=216000
        )

        attempt = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            track,
            self.ARTIST,
            self.TITLE,
            widen=True,
            require_success=True,
            min_similarity=0.7,
        )

        assert attempt.match is not None
        assert attempt.match.candidate.id == "jc_hurt"


class TestMinimumCandidateDuration:
    """A candidate shorter than the caller's evidence cannot be the recording."""

    async def test_a_too_short_candidate_is_rejected_despite_a_perfect_title(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        connector = _make_connector([
            _make_candidate(name="Creep", artist="Radiohead", duration_ms=218000)
        ])
        track = make_track(id=1, title="Creep", artist="Radiohead")

        attempt = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            track,
            "Radiohead",
            "Creep",
            widen=False,
            require_success=False,
            min_candidate_duration_ms=223000,
        )

        assert attempt.match is None

    async def test_a_longer_candidate_is_untouched(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        """One-directional: an over-long candidate is ordinary, not suspicious."""
        connector = _make_connector([
            _make_candidate(name="Creep", artist="Radiohead", duration_ms=400000)
        ])
        track = make_track(id=1, title="Creep", artist="Radiohead")

        attempt = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            track,
            "Radiohead",
            "Creep",
            widen=False,
            require_success=False,
            min_candidate_duration_ms=223000,
        )

        assert attempt.match is not None

    async def test_the_veto_runs_before_ranking_so_a_longer_candidate_can_win(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        """Vetoing the ranked winner afterwards would discard the real match."""
        short_exact = _make_candidate(
            track_id="edit", name="Creep", artist="Radiohead", duration_ms=180000
        )
        long_variant = _make_candidate(
            track_id="album", name="Creep", artist="Radiohead", duration_ms=238000
        )
        connector = _make_connector([short_exact, long_variant])
        track = make_track(id=1, title="Creep", artist="Radiohead")

        attempt = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            track,
            "Radiohead",
            "Creep",
            widen=False,
            require_success=False,
            min_candidate_duration_ms=223000,
        )

        assert attempt.match is not None
        assert attempt.match.candidate.id == "album"

    async def test_a_candidate_without_a_duration_is_not_vetoed(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        """Spotify reports 0 for an unknown length; absence is not evidence."""
        connector = _make_connector([
            _make_candidate(name="Creep", artist="Radiohead", duration_ms=0)
        ])
        track = make_track(id=1, title="Creep", artist="Radiohead")

        attempt = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            track,
            "Radiohead",
            "Creep",
            widen=False,
            require_success=False,
            min_candidate_duration_ms=223000,
        )

        assert attempt.match is not None

    async def test_no_minimum_leaves_every_candidate_in_play(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        connector = _make_connector([
            _make_candidate(name="Creep", artist="Radiohead", duration_ms=1000)
        ])
        track = make_track(id=1, title="Creep", artist="Radiohead")

        attempt = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            track,
            "Radiohead",
            "Creep",
            widen=False,
            require_success=False,
        )

        assert attempt.match is not None


class TestSearchAttemptQueries:
    """The attempt carries the exact query strings sent, for caller telemetry."""

    async def test_accepted_first_pass_records_only_the_filtered_query(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        connector = _make_connector([_make_candidate()])
        track = make_track(id=1, title="Creep", artist="Radiohead")

        attempt = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            track,
            "Radiohead",
            "Creep",
            widen=True,
            require_success=True,
        )

        assert attempt.queries == ('artist:"Radiohead" track:"Creep"',)

    async def test_widened_attempt_records_both_queries_in_order(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        connector = _make_connector()
        connector.search_track.side_effect = [[], []]
        track = make_track(id=1, title="Creep", artist="Radiohead")

        attempt = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            track,
            "Radiohead",
            "Creep",
            widen=True,
            require_success=True,
        )

        assert attempt.queries == (
            'artist:"Radiohead" track:"Creep"',
            "Radiohead Creep",
        )

    async def test_the_recorded_queries_are_the_ones_on_the_wire(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        """No parallel construction: the log and the request read one string."""
        connector = _make_connector()
        connector.search_track.side_effect = [[], []]
        track = make_track(id=1, title='Move Your Body (12" Mix)', artist="Eusebe")

        attempt = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            track,
            "Eusebe",
            'Move Your Body (12" Mix)',
            widen=True,
            require_success=True,
        )

        sent = [c.args[0] for c in connector.search_track.await_args_list]
        assert list(attempt.queries) == sent
        assert all('"' not in query for query in sent[1:])


class TestSearchAndEvaluateExceptionPropagation:
    """Exceptions from the connector should bubble up (not be caught)."""

    async def test_propagates_connector_exception(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        connector = _make_connector()
        connector.search_track.side_effect = RuntimeError("API down")
        track = make_track(id=1)

        with pytest.raises(RuntimeError, match="API down"):
            _ = await search_and_evaluate_attempt(
                connector,
                evaluation_service,
                track,
                "Radiohead",
                "Creep",
                widen=True,
                require_success=True,
            )


class TestArtistSimilarityFloor:
    """The floor guards the ~7% of dead ids with no duration evidence.

    With no duration on the hint, the full evaluation ACCEPTS a same-title
    wrong-artist recording (artist disagreement costs ~7 of 100 confidence
    points), so require_success alone cannot stop the substitution — only the
    artist floor can.
    """

    ARTIST = "Johnny Cash"
    TITLE = "Hurt"

    def _wrong_artist_candidate(self) -> MagicMock:
        return _make_candidate(
            track_id="nin_hurt",
            name="Hurt",
            artist="Nine Inch Nails",
            duration_ms=373000,
        )

    def _durationless_track(self):
        # No duration evidence: the evaluator has only artist+title, and the
        # measured confidence for this pair clears the auto-accept bar.
        return make_track(id=1, title=self.TITLE, artist=self.ARTIST)

    async def test_wrong_artist_rejected_by_floor_despite_success(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        connector = _make_connector([self._wrong_artist_candidate()])

        attempt = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            self._durationless_track(),
            self.ARTIST,
            self.TITLE,
            widen=False,
            require_success=True,
            min_similarity=0.7,
            min_artist_similarity=(
                SpotifyConstants.FALLBACK_ARTIST_SIMILARITY_THRESHOLD
            ),
        )

        assert attempt.match is None

    async def test_without_the_floor_the_same_candidate_is_accepted(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        """Documents the exposure the floor closes — if this starts failing,
        the matcher's artist weighting changed and the floor may be redundant."""
        connector = _make_connector([self._wrong_artist_candidate()])

        attempt = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            self._durationless_track(),
            self.ARTIST,
            self.TITLE,
            widen=False,
            require_success=True,
            min_similarity=0.7,
        )

        assert attempt.match is not None

    async def test_right_artist_clears_the_floor(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        candidate = _make_candidate(
            track_id="jc_hurt",
            name="Hurt",
            artist="Johnny Cash",
            duration_ms=216000,
        )
        connector = _make_connector([candidate])

        attempt = await search_and_evaluate_attempt(
            connector,
            evaluation_service,
            self._durationless_track(),
            self.ARTIST,
            self.TITLE,
            widen=False,
            require_success=True,
            min_similarity=0.7,
            min_artist_similarity=(
                SpotifyConstants.FALLBACK_ARTIST_SIMILARITY_THRESHOLD
            ),
        )

        assert attempt.match is not None
        assert attempt.match.candidate.id == "jc_hurt"

    async def test_missing_evidence_fails_closed_when_floor_requested(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        connector = _make_connector([self._wrong_artist_candidate()])
        service = MagicMock()
        service.config = evaluation_service.config
        service.evaluate_single_match.return_value = MagicMock(
            success=True, evidence=None
        )

        attempt = await search_and_evaluate_attempt(
            connector,
            service,
            self._durationless_track(),
            self.ARTIST,
            self.TITLE,
            widen=False,
            require_success=True,
            min_similarity=0.7,
            min_artist_similarity=(
                SpotifyConstants.FALLBACK_ARTIST_SIMILARITY_THRESHOLD
            ),
        )

        assert attempt.match is None
