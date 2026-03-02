"""Tests for TrackMatchEvaluationService — pure domain evaluation logic.

This test suite validates the business rules for match acceptance, single match
evaluation, and batch evaluation of raw provider matches.
"""

from src.config import create_matching_config
from src.domain.entities import Artist, Track
from src.domain.matching.evaluation_service import TrackMatchEvaluationService
from src.domain.matching.types import RawProviderMatch
from tests.fixtures import make_track

config = create_matching_config()


def _make_raw_match(
    connector_id: str = "ext:123",
    match_method: str = "isrc",
    title: str = "Test Song",
    artist: str = "Test Artist",
    duration_ms: int | None = 240_000,
) -> RawProviderMatch:
    """Create a RawProviderMatch with sensible defaults for testing."""
    return RawProviderMatch(
        connector_id=connector_id,
        match_method=match_method,
        service_data={
            "title": title,
            "artist": artist,
            "duration_ms": duration_ms,
        },
    )


class TestShouldAcceptMatch:
    """Test threshold-based match acceptance business rules."""

    def setup_method(self) -> None:
        self.service = TrackMatchEvaluationService(config=config)

    def test_isrc_above_threshold_accepted(self):
        """ISRC match above threshold should be accepted."""
        assert self.service.should_accept_match(config.threshold_isrc, "isrc")

    def test_isrc_below_threshold_rejected(self):
        """ISRC match below threshold should be rejected."""
        assert not self.service.should_accept_match(config.threshold_isrc - 1, "isrc")

    def test_artist_title_above_threshold_accepted(self):
        """Artist/title match above threshold should be accepted."""
        assert self.service.should_accept_match(
            config.threshold_artist_title, "artist_title"
        )

    def test_artist_title_below_threshold_rejected(self):
        """Artist/title match below threshold should be rejected."""
        assert not self.service.should_accept_match(
            config.threshold_artist_title - 1, "artist_title"
        )

    def test_mbid_above_threshold_accepted(self):
        """MBID match above threshold should be accepted."""
        assert self.service.should_accept_match(config.threshold_mbid, "mbid")

    def test_mbid_below_threshold_rejected(self):
        """MBID match below threshold should be rejected."""
        assert not self.service.should_accept_match(config.threshold_mbid - 1, "mbid")

    def test_unknown_method_uses_default_threshold(self):
        """Unknown match method should use the default threshold."""
        assert self.service.should_accept_match(
            config.threshold_default, "unknown_method"
        )
        assert not self.service.should_accept_match(
            config.threshold_default - 1, "unknown_method"
        )


class TestEvaluateSingleMatch:
    """Test single-match evaluation with confidence scoring and track updates."""

    def setup_method(self) -> None:
        self.service = TrackMatchEvaluationService(config=config)

    def test_successful_isrc_match_returns_high_confidence(self):
        """High-quality ISRC match should succeed with high confidence."""
        track = make_track(1, title="Paranoid Android", artist="Radiohead")
        raw_match = _make_raw_match(
            connector_id="spotify:abc",
            match_method="isrc",
            title="Paranoid Android",
            artist="Radiohead",
            duration_ms=240_000,
        )

        result = self.service.evaluate_single_match(track, raw_match, "spotify")

        assert result.success is True
        assert result.confidence >= config.threshold_isrc
        assert result.connector_id == "spotify:abc"
        assert result.match_method == "isrc"

    def test_successful_match_updates_track_with_connector_id(self):
        """Accepted match should produce an updated track with the connector mapping."""
        track = make_track(1)
        raw_match = _make_raw_match(connector_id="spotify:xyz", match_method="isrc")

        result = self.service.evaluate_single_match(track, raw_match, "spotify")

        assert result.success is True
        assert result.track.connector_track_identifiers.get("spotify") == "spotify:xyz"

    def test_rejected_match_preserves_original_track(self):
        """Rejected match should return the original track unchanged."""
        track = make_track(1, title="Song A", artist="Artist A", duration_ms=240_000)
        raw_match = _make_raw_match(
            match_method="artist_title",
            title="Completely Different Song",
            artist="Completely Different Artist",
            duration_ms=500_000,
        )

        result = self.service.evaluate_single_match(track, raw_match, "spotify")

        assert result.success is False
        assert result.track is track  # Same object, not modified

    def test_evidence_is_populated(self):
        """Match result should include confidence evidence details."""
        track = make_track(1, title="Karma Police", artist="Radiohead")
        raw_match = _make_raw_match(
            match_method="artist_title",
            title="Karma Police",
            artist="Radiohead",
        )

        result = self.service.evaluate_single_match(track, raw_match, "spotify")

        assert result.evidence is not None
        assert result.evidence.base_score > 0
        assert result.evidence.final_score == result.confidence


class TestEvaluateRawMatches:
    """Test batch evaluation of raw provider matches."""

    def setup_method(self) -> None:
        self.service = TrackMatchEvaluationService(config=config)

    def test_only_accepted_matches_in_results(self):
        """Batch evaluation should only return accepted matches."""
        tracks = [
            make_track(1, title="Good Match", artist="Artist"),
            make_track(2, title="Bad Match", artist="Artist", duration_ms=240_000),
        ]
        raw_matches = {
            1: _make_raw_match(
                match_method="isrc",
                title="Good Match",
                artist="Artist",
            ),
            2: _make_raw_match(
                match_method="artist_title",
                title="Totally Different",
                artist="Wrong Artist",
                duration_ms=999_999,
            ),
        }

        results = self.service.evaluate_raw_matches(tracks, raw_matches, "spotify")

        assert 1 in results
        assert results[1].success is True
        # Track 2 should be rejected due to low confidence
        assert 2 not in results

    def test_tracks_without_raw_matches_skipped(self):
        """Tracks with no corresponding raw match should be silently skipped."""
        tracks = [
            make_track(1, title="Song", artist="Artist"),
            make_track(2, title="Unmatched", artist="Artist"),
        ]
        raw_matches = {
            1: _make_raw_match(match_method="isrc", title="Song", artist="Artist"),
        }

        results = self.service.evaluate_raw_matches(tracks, raw_matches, "spotify")

        assert 1 in results
        assert 2 not in results

    def test_tracks_with_none_ids_skipped(self):
        """Tracks without database IDs should be skipped."""
        track_no_id = Track(title="No ID", artists=[Artist(name="Artist")])
        tracks = [track_no_id]
        raw_matches: dict[int, RawProviderMatch] = {}

        results = self.service.evaluate_raw_matches(tracks, raw_matches, "spotify")

        assert len(results) == 0

    def test_empty_inputs_return_empty_dict(self):
        """Empty tracks and matches should return empty results."""
        results = self.service.evaluate_raw_matches([], {}, "spotify")

        assert results == {}

    def test_all_tracks_matched_returns_all(self):
        """When all tracks match, all should appear in results."""
        tracks = [
            make_track(1, title="Song 1", artist="Artist"),
            make_track(2, title="Song 2", artist="Artist"),
        ]
        raw_matches = {
            1: _make_raw_match(
                connector_id="sp:1",
                match_method="isrc",
                title="Song 1",
                artist="Artist",
            ),
            2: _make_raw_match(
                connector_id="sp:2",
                match_method="isrc",
                title="Song 2",
                artist="Artist",
            ),
        }

        results = self.service.evaluate_raw_matches(tracks, raw_matches, "spotify")

        assert len(results) == 2
        assert results[1].connector_id == "sp:1"
        assert results[2].connector_id == "sp:2"
