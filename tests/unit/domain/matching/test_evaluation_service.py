"""Tests for TrackMatchEvaluationService — three-zone classification.

Validates auto-accept, review, and auto-reject business rules,
single match evaluation, and batch evaluation of raw provider matches.
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


class TestThreeZoneClassification:
    """Test three-zone match classification: accept, review, reject."""

    def setup_method(self) -> None:
        self.service = TrackMatchEvaluationService(config=config)

    def test_above_auto_accept_is_accepted(self):
        assert self.service.should_accept_match(config.auto_accept_threshold, "isrc")
        assert self.service.should_accept_match(100, "artist_title")

    def test_below_auto_accept_is_not_accepted(self):
        assert not self.service.should_accept_match(
            config.auto_accept_threshold - 1, "isrc"
        )

    def test_in_review_zone_is_review(self):
        """Confidence between review_threshold and auto_accept_threshold = review."""
        mid = (config.review_threshold + config.auto_accept_threshold) // 2
        assert self.service.should_review_match(mid, "isrc")

    def test_at_review_threshold_is_review(self):
        assert self.service.should_review_match(config.review_threshold, "isrc")

    def test_below_review_threshold_is_rejected(self):
        assert not self.service.should_review_match(
            config.review_threshold - 1, "isrc"
        )
        assert not self.service.should_accept_match(
            config.review_threshold - 1, "isrc"
        )

    def test_at_auto_accept_is_accepted_not_review(self):
        """Exactly at auto_accept_threshold should be accepted, not review."""
        assert self.service.should_accept_match(
            config.auto_accept_threshold, "isrc"
        )
        assert not self.service.should_review_match(
            config.auto_accept_threshold, "isrc"
        )

    def test_zones_are_exhaustive(self):
        """Every score should fall into exactly one zone."""
        for score in range(101):
            accepted = self.service.should_accept_match(score, "isrc")
            review = self.service.should_review_match(score, "isrc")
            rejected = not accepted and not review
            # Exactly one should be True
            assert sum([accepted, review, rejected]) == 1


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
        assert result.review_required is False
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
        assert result.evidence.match_weight != 0.0


class TestEvaluateRawMatches:
    """Test batch evaluation with three-zone classification."""

    def setup_method(self) -> None:
        self.service = TrackMatchEvaluationService(config=config)

    def test_accepted_and_rejected_are_separated(self):
        """Batch evaluation separates accepted from rejected."""
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

        result = self.service.evaluate_raw_matches(tracks, raw_matches, "spotify")

        assert 1 in result.accepted
        assert result.accepted[1].success is True
        # Track 2 should be rejected (not in accepted or review)
        assert 2 not in result.accepted

    def test_tracks_without_raw_matches_skipped(self):
        """Tracks with no corresponding raw match should be silently skipped."""
        tracks = [
            make_track(1, title="Song", artist="Artist"),
            make_track(2, title="Unmatched", artist="Artist"),
        ]
        raw_matches = {
            1: _make_raw_match(match_method="isrc", title="Song", artist="Artist"),
        }

        result = self.service.evaluate_raw_matches(tracks, raw_matches, "spotify")

        assert 1 in result.accepted
        assert 2 not in result.accepted
        assert 2 not in result.review_candidates

    def test_tracks_with_none_ids_skipped(self):
        """Tracks without database IDs should be skipped."""
        track_no_id = Track(title="No ID", artists=[Artist(name="Artist")])
        tracks = [track_no_id]
        raw_matches: dict[int, RawProviderMatch] = {}

        result = self.service.evaluate_raw_matches(tracks, raw_matches, "spotify")

        assert len(result.accepted) == 0
        assert len(result.review_candidates) == 0

    def test_empty_inputs_return_empty_result(self):
        """Empty tracks and matches should return empty EvaluationResult."""
        result = self.service.evaluate_raw_matches([], {}, "spotify")

        assert result.accepted == {}
        assert result.review_candidates == {}

    def test_all_tracks_matched_returns_all(self):
        """When all tracks match well, all should appear in accepted."""
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

        result = self.service.evaluate_raw_matches(tracks, raw_matches, "spotify")

        assert len(result.accepted) == 2
        assert result.accepted[1].connector_id == "sp:1"
        assert result.accepted[2].connector_id == "sp:2"
