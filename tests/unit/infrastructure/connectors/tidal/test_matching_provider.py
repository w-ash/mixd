"""Tests for TidalMatchingProvider — conservative ISRC-only matching.

Validates the one-code-per-request loop (the client's ``filter[isrc]``
contract), the ``IsrcOnly`` strategy contract (no artist/title funnel,
ever), the 1:N duration-cross-check pick, and the per-code failure
isolation (a failed lookup fails only its own tracks, as API_ERROR, never
NO_RESULTS).
"""

from unittest.mock import AsyncMock

from src.domain.entities import Artist, Track
from src.domain.matching.types import MatchFailureReason
from src.infrastructure.connectors._shared.matching_provider import IsrcOnly
from src.infrastructure.connectors.tidal.client import TIDAL_COUNTRY_CODE
from src.infrastructure.connectors.tidal.matching_provider import (
    TidalMatchingProvider,
)
from tests.fixtures import make_tidal_track_resource


def _make_provider(resources_by_isrc=None, failing_isrcs=()):
    """Provider over a mocked client answering per-code lookups."""
    client = AsyncMock()
    resources_by_isrc = resources_by_isrc or {}

    async def _lookup(isrc: str, country_code: str):
        assert country_code == TIDAL_COUNTRY_CODE
        if isrc in failing_isrcs:
            raise RuntimeError("transport failure")
        return resources_by_isrc.get(isrc, [])

    client.get_tracks_by_isrc.side_effect = _lookup
    return TidalMatchingProvider(client), client


def _isrc_track(
    isrc: str = "USUM72309818",
    title: str = "Test Song",
    duration_ms: int | None = 200_000,
) -> Track:
    return Track(
        title=title,
        isrc=isrc,
        duration_ms=duration_ms,
        artists=[Artist(name="Test Artist")],
    )


class TestServiceContract:
    def test_service_name_is_tidal(self):
        provider, _ = _make_provider()
        assert provider.service_name == "tidal"

    def test_strategy_is_isrc_only(self):
        """No artist/title phase exists — the strategy has no such hook."""
        provider, _ = _make_provider()
        assert isinstance(provider._match_strategy(), IsrcOnly)


class TestIsrcMatching:
    async def test_isrc_hit_returns_raw_match(self):
        resource = make_tidal_track_resource(track_id="12345", isrc="USUM72309818")
        provider, client = _make_provider({"USUM72309818": [resource]})
        track = _isrc_track("USUM72309818")

        result = await provider.fetch_raw_matches_for_tracks([track])

        assert track.id in result.matches
        match = result.matches[track.id]
        assert match["connector_id"] == "12345"
        # "isrc" (not "isrc_match"): ISRC_GRADE_METHODS keys confidence
        # scoring on the raw-match method string Spotify also emits.
        assert match["match_method"] == "isrc"
        assert match["service_data"]["title"] == "Test Song"
        assert match["service_data"]["duration_ms"] == 200_000
        assert match["service_data"]["isrc"] == "USUM72309818"
        client.get_tracks_by_isrc.assert_awaited_once_with(
            "USUM72309818", TIDAL_COUNTRY_CODE
        )

    async def test_one_code_per_request_loop(self):
        """Each unique ISRC gets its own client call — never batched codes."""
        codes = [f"USUM723098{i:02d}" for i in range(3)]
        provider, client = _make_provider({
            code: [make_tidal_track_resource(track_id=str(i), isrc=code)]
            for i, code in enumerate(codes)
        })
        tracks = [_isrc_track(code, f"Song {code}") for code in codes]

        result = await provider.fetch_raw_matches_for_tracks(tracks)

        assert len(result.matches) == 3
        assert client.get_tracks_by_isrc.await_count == 3
        called_codes = [c.args[0] for c in client.get_tracks_by_isrc.await_args_list]
        assert sorted(called_codes) == sorted(codes)

    async def test_duplicate_isrcs_looked_up_once(self):
        resource = make_tidal_track_resource(track_id="12345", isrc="USUM72309818")
        provider, client = _make_provider({"USUM72309818": [resource]})
        tracks = [
            _isrc_track("USUM72309818", "First"),
            _isrc_track("USUM72309818", "Second"),
        ]

        result = await provider.fetch_raw_matches_for_tracks(tracks)

        assert len(result.matches) == 2
        client.get_tracks_by_isrc.assert_awaited_once()

    async def test_one_to_many_pick_honors_duration_cross_check(self):
        """First result failing the cross-check is skipped for one passing."""
        outlier = make_tidal_track_resource(
            track_id="radio-edit", isrc="USUM72309818", duration="PT1M30S"
        )
        aligned = make_tidal_track_resource(
            track_id="album-cut", isrc="USUM72309818", duration="PT3M20S"
        )
        provider, _ = _make_provider({"USUM72309818": [outlier, aligned]})
        track = _isrc_track("USUM72309818", duration_ms=200_000)

        result = await provider.fetch_raw_matches_for_tracks([track])

        assert result.matches[track.id]["connector_id"] == "album-cut"

    async def test_no_passing_candidate_falls_back_to_first(self):
        """Every candidate suspect → first hit stands (same recording identity)."""
        first = make_tidal_track_resource(
            track_id="first", isrc="USUM72309818", duration="PT1M30S"
        )
        second = make_tidal_track_resource(
            track_id="second", isrc="USUM72309818", duration="PT1M00S"
        )
        provider, _ = _make_provider({"USUM72309818": [first, second]})
        track = _isrc_track("USUM72309818", duration_ms=200_000)

        result = await provider.fetch_raw_matches_for_tracks([track])

        assert result.matches[track.id]["connector_id"] == "first"

    async def test_isrc_miss_fails_without_artist_title_fallback(self):
        """ISRC miss → NO_RESULTS failure; no second-chance search runs."""
        provider, client = _make_provider({})
        track = _isrc_track("USUM72309818")

        result = await provider.fetch_raw_matches_for_tracks([track])

        assert not result.matches
        assert len(result.failures) == 1
        failure = result.failures[0]
        assert failure.track_id == track.id
        assert failure.reason == MatchFailureReason.NO_RESULTS
        assert failure.method == "isrc"
        assert failure.service == "tidal"
        client.get_tracks_by_isrc.assert_awaited_once()

    async def test_track_without_isrc_fails_without_artist_title_call(self):
        """No ISRC → NO_ISRC failure from the IsrcOnly strategy, no lookup."""
        provider, client = _make_provider()
        track = Track(title="No Code", artists=[Artist(name="Someone")])

        result = await provider.fetch_raw_matches_for_tracks([track])

        assert not result.matches
        assert len(result.failures) == 1
        assert result.failures[0].reason == MatchFailureReason.NO_ISRC
        client.get_tracks_by_isrc.assert_not_awaited()

    async def test_failed_lookup_fails_only_its_own_tracks_as_api_error(self):
        """A failed per-code lookup leaves that code UNANSWERED (API_ERROR,
        never NO_RESULTS) and does not poison the other codes' matches."""
        good = make_tidal_track_resource(track_id="ok-1", isrc="USUM72309918")
        provider, _ = _make_provider(
            {"USUM72309918": [good]}, failing_isrcs=("USUM72309818",)
        )
        failed_track = _isrc_track("USUM72309818", "Failed")
        matched_track = _isrc_track("USUM72309918", "Matched")

        result = await provider.fetch_raw_matches_for_tracks([
            failed_track,
            matched_track,
        ])

        assert set(result.matches) == {matched_track.id}
        assert len(result.failures) == 1
        failure = result.failures[0]
        assert failure.track_id == failed_track.id
        assert failure.reason == MatchFailureReason.API_ERROR

    async def test_hyphenated_isrc_normalized_for_lookup(self):
        """The client receives the normalized code, not the raw spelling."""
        resource = make_tidal_track_resource(track_id="42", isrc="USUM72309818")
        provider, client = _make_provider({"USUM72309818": [resource]})
        track = _isrc_track("US-UM7-23-09818")

        result = await provider.fetch_raw_matches_for_tracks([track])

        assert track.id in result.matches
        client.get_tracks_by_isrc.assert_awaited_once_with(
            "USUM72309818", TIDAL_COUNTRY_CODE
        )
