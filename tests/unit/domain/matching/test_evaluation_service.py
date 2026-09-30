"""Tests for MatchEvaluationService — three-zone classification.

Validates auto-accept, review, and auto-reject business rules,
single match evaluation, and batch evaluation of raw provider matches.
"""

import pytest

from src.config import create_matching_config
from src.domain.matching.config import MatchingConfig
from src.domain.matching.evaluation_service import MatchEvaluationService
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
    """Accept at or above 85, review from 50 up to 85, reject below 50."""

    @pytest.mark.parametrize(
        ("confidence", "accept", "review"),
        [
            (100, True, False),
            (85, True, False),  # the accept bound belongs to accept only
            (84, False, True),
            (67, False, True),
            (50, False, True),  # the review bound is inclusive
            (49, False, False),
            (0, False, False),
        ],
    )
    def test_each_score_falls_in_exactly_one_zone(
        self, confidence: int, accept: bool, review: bool
    ):
        service = MatchEvaluationService(
            config=MatchingConfig(
                identical_similarity_score=1.0,
                variation_similarity_score=0.6,
                auto_accept_threshold=85,
                review_threshold=50,
                high_similarity_threshold=0.9,
                phonetic_similarity_score=0.85,
            )
        )

        assert service.should_accept_match(confidence, "isrc") is accept
        assert service.should_review_match(confidence, "isrc") is review


class TestEvaluateSingleMatch:
    """Test single-match evaluation with confidence scoring and track updates."""

    def setup_method(self) -> None:
        self.service = MatchEvaluationService(config=config)

    def test_successful_isrc_match_returns_high_confidence(self):
        """High-quality ISRC match should succeed with high confidence."""
        track = make_track(
            1, title="Paranoid Android", artist="Radiohead", duration_ms=240_000
        )
        raw_match = _make_raw_match(
            connector_id="spotify:abc",
            match_method="isrc",
            title="Paranoid Android",
            artist="Radiohead",
            duration_ms=240_000,
        )

        result = self.service.evaluate_single_match(track, raw_match, "spotify")

        # ISRC exact plus exact title, artist and duration saturate the sigmoid.
        assert result.confidence == 100
        assert result.success is True
        assert result.review_required is False
        assert result.connector_id == "spotify:abc"
        assert result.match_method == "isrc"

    def test_successful_match_updates_track_with_connector_id(self):
        """Accepted match should produce an updated track with the connector mapping."""
        track = make_track(1, duration_ms=240_000)
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

    def test_isrc_match_without_duration_cannot_auto_accept(self):
        """ISRC-grade evidence with no duration comparison routes to review.

        The duration-based suspect check (assess_isrc_match_reliability) is
        structurally unreachable without a duration on both sides — the match
        may score at 100, but a human confirms it instead of auto-accepting.
        """
        track = make_track(1, title="Gold Rush", artist="Neon Priest")  # no duration
        raw_match = _make_raw_match(
            match_method="isrc",
            title="Gold Rush",
            artist="Neon Priest",
            duration_ms=None,
        )

        result = self.service.evaluate_single_match(track, raw_match, "musicbrainz")

        assert result.success is False
        assert result.review_required is True
        assert result.evidence is not None
        assert result.evidence.duration_missing is True
        # The track was NOT updated with the connector id (not accepted).
        assert "musicbrainz" not in result.track.connector_track_identifiers

    def test_isrc_match_with_duration_but_missing_titles_auto_accepts(self):
        """Missing title/artist alone doesn't gate — the suspect check ran.

        With durations on both sides the ISRC reliability check is reachable,
        so neutral-missing text metadata may still auto-accept.
        """
        track = make_track(
            1, title="Gold Rush", artist="Neon Priest", duration_ms=200_000
        )
        raw_match = _make_raw_match(
            match_method="isrc", title="", artist="", duration_ms=200_000
        )

        result = self.service.evaluate_single_match(track, raw_match, "musicbrainz")

        assert result.success is True
        assert result.review_required is False
        assert result.evidence is not None
        assert result.evidence.duration_missing is False
        assert result.evidence.isrc_suspect is False

    def test_artist_title_match_without_duration_is_not_gated(self):
        """The no-duration gate applies only to ISRC-grade methods."""
        track = make_track(1, title="Gold Rush", artist="Neon Priest")
        raw_match = _make_raw_match(
            match_method="artist_title",
            title="Gold Rush",
            artist="Neon Priest",
            duration_ms=None,
        )

        result = self.service.evaluate_single_match(track, raw_match, "spotify")

        assert result.success is True
        assert result.review_required is False


class TestEvaluateRawMatches:
    """Test batch evaluation with three-zone classification."""

    def setup_method(self) -> None:
        self.service = MatchEvaluationService(config=config)

    def test_each_track_lands_in_exactly_one_outcome(self):
        """Accepted, review, rejected and no-match are all returned.

        Rejections and no-matches feed the negative cache, so they are part
        of the result, not only the accepted matches.
        """
        tracks = [
            make_track(1, title="Good Match", artist="Artist", duration_ms=240_000),
            # Mid-batch, so an unmatched track cannot end the evaluation.
            make_track(4, title="Unmatched", artist="Artist", duration_ms=240_000),
            make_track(2, title="No Duration", artist="Artist"),
            make_track(3, title="Bad Match", artist="Artist", duration_ms=240_000),
        ]
        raw_matches = {
            1: _make_raw_match(
                connector_id="sp:1", title="Good Match", artist="Artist"
            ),
            # ISRC-grade with no duration on either side: a human confirms it.
            2: _make_raw_match(
                connector_id="sp:2",
                title="No Duration",
                artist="Artist",
                duration_ms=None,
            ),
            3: _make_raw_match(
                connector_id="sp:3",
                match_method="artist_title",
                title="Totally Different",
                artist="Wrong Artist",
                duration_ms=999_999,
            ),
        }

        result = self.service.evaluate_raw_matches(tracks, raw_matches, "spotify")

        assert list(result.accepted) == [1]
        assert result.accepted[1].connector_id == "sp:1"
        assert result.accepted[1].track.connector_track_identifiers == {
            "spotify": "sp:1"
        }
        assert list(result.review_candidates) == [2]
        assert result.review_candidates[2].connector_id == "sp:2"
        assert [r.connector_id for r in result.rejected] == ["sp:3"]
        assert result.no_match_track_ids == [4]
