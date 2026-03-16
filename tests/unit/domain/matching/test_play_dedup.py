"""Tests for cross-source play history deduplication.

Verifies the pure domain logic that identifies duplicate listening events
across music services (Spotify + Last.fm) by normalizing timestamps
and matching within a tolerance window.
"""

from datetime import UTC, datetime, timedelta

from src.domain.entities import TrackPlay
from src.domain.matching.play_dedup import (
    _normalize_to_start_time,
    compute_dedup_time_range,
    deduplicate_cross_source_plays,
)


def _make_play(
    track_id: int = 1,
    service: str = "spotify",
    played_at: datetime | None = None,
    ms_played: int | None = 240000,
    context: dict | None = None,
    id: int | None = None,
    source_services: list[str] | None = None,
) -> TrackPlay:
    """Factory for test TrackPlay instances."""
    if played_at is None:
        played_at = datetime(2024, 10, 1, 21, 0, 0, tzinfo=UTC)
    return TrackPlay(
        track_id=track_id,
        service=service,
        played_at=played_at,
        ms_played=ms_played,
        context=context,
        id=id,
        source_services=source_services,
    )


class TestNormalizeToStartTime:
    """Test timestamp normalization for cross-service comparison."""

    def test_spotify_end_time_normalized_to_start(self):
        """Spotify plays store end time — should subtract ms_played."""
        play = _make_play(
            service="spotify",
            played_at=datetime(2024, 10, 1, 21, 4, 0, tzinfo=UTC),
            ms_played=240000,  # 4 minutes
        )
        start_epoch = _normalize_to_start_time(play)
        expected = datetime(2024, 10, 1, 21, 0, 0, tzinfo=UTC).timestamp()
        assert abs(start_epoch - expected) < 0.01

    def test_lastfm_already_start_time(self):
        """Last.fm plays store start time — should not be adjusted."""
        played_at = datetime(2024, 10, 1, 21, 0, 0, tzinfo=UTC)
        play = _make_play(service="lastfm", played_at=played_at, ms_played=None)
        start_epoch = _normalize_to_start_time(play)
        assert abs(start_epoch - played_at.timestamp()) < 0.01

    def test_spotify_without_ms_played_not_adjusted(self):
        """Spotify plays without ms_played fall through without adjustment."""
        played_at = datetime(2024, 10, 1, 21, 4, 0, tzinfo=UTC)
        play = _make_play(service="spotify", played_at=played_at, ms_played=None)
        start_epoch = _normalize_to_start_time(play)
        assert abs(start_epoch - played_at.timestamp()) < 0.01


class TestDeduplicateCrossSourcePlays:
    """Test the core deduplication algorithm."""

    def test_matching_spotify_and_lastfm_same_event(self):
        """Spotify end-time + Last.fm start-time for same play → match."""
        # Spotify: ended at 21:04:00 after playing 4 min → started at 21:00:00
        spotify_play = _make_play(
            service="spotify",
            played_at=datetime(2024, 10, 1, 21, 4, 0, tzinfo=UTC),
            ms_played=240000,
            context={"platform": "osx", "shuffle": False},
        )
        # Last.fm: started at 21:00:05 (5 seconds clock skew)
        lastfm_existing = _make_play(
            service="lastfm",
            played_at=datetime(2024, 10, 1, 21, 0, 5, tzinfo=UTC),
            ms_played=None,
            context={"mbid": "abc-123", "loved": True},
            id=42,
        )

        result = deduplicate_cross_source_plays(
            new_plays=[spotify_play], existing_plays=[lastfm_existing]
        )

        assert result.stats["cross_source_matches"] == 1
        assert len(result.suppressed_plays) == 1
        # Spotify has higher priority — it should win
        assert len(result.plays_to_insert) == 1
        inserted = result.plays_to_insert[0]
        assert inserted.service == "spotify"
        assert inserted.source_services is not None
        assert "spotify" in inserted.source_services
        assert "lastfm" in inserted.source_services

    def test_same_service_plays_not_matched(self):
        """Two Spotify plays should never be cross-source matched."""
        play1 = _make_play(
            service="spotify",
            played_at=datetime(2024, 10, 1, 21, 4, 0, tzinfo=UTC),
            ms_played=240000,
        )
        existing = _make_play(
            service="spotify",
            played_at=datetime(2024, 10, 1, 21, 4, 5, tzinfo=UTC),
            ms_played=240000,
            id=10,
        )

        result = deduplicate_cross_source_plays(
            new_plays=[play1], existing_plays=[existing]
        )

        assert result.stats.get("cross_source_matches", 0) == 0
        assert len(result.plays_to_insert) == 1
        assert len(result.suppressed_plays) == 0

    def test_time_outside_tolerance_no_match(self):
        """Plays from different services but too far apart → no match."""
        # Spotify started at 21:00:00, Last.fm started at 21:01:00 (60s apart)
        spotify_play = _make_play(
            service="spotify",
            played_at=datetime(2024, 10, 1, 21, 4, 0, tzinfo=UTC),
            ms_played=240000,
        )
        lastfm_existing = _make_play(
            service="lastfm",
            played_at=datetime(2024, 10, 1, 21, 1, 0, tzinfo=UTC),
            ms_played=None,
            id=42,
        )

        result = deduplicate_cross_source_plays(
            new_plays=[spotify_play], existing_plays=[lastfm_existing]
        )

        # 60 seconds apart > 30 second tolerance → no match
        assert result.stats.get("cross_source_matches", 0) == 0
        assert len(result.plays_to_insert) == 1

    def test_existing_wins_when_higher_priority(self):
        """When existing Spotify play matches new Last.fm → existing wins."""
        spotify_existing = _make_play(
            service="spotify",
            played_at=datetime(2024, 10, 1, 21, 4, 0, tzinfo=UTC),
            ms_played=240000,
            context={"platform": "osx"},
            id=42,
        )
        # Last.fm started at same time as Spotify start
        lastfm_new = _make_play(
            service="lastfm",
            played_at=datetime(2024, 10, 1, 21, 0, 0, tzinfo=UTC),
            ms_played=None,
            context={"mbid": "abc-123"},
        )

        result = deduplicate_cross_source_plays(
            new_plays=[lastfm_new], existing_plays=[spotify_existing]
        )

        assert result.stats["cross_source_matches"] == 1
        assert result.stats["existing_wins"] == 1
        # Last.fm play suppressed, existing Spotify updated
        assert len(result.suppressed_plays) == 1
        assert len(result.plays_to_update) == 1
        play_id, update_fields = result.plays_to_update[0]
        assert play_id == 42
        assert "spotify" in update_fields["source_services"]
        assert "lastfm" in update_fields["source_services"]

    def test_lastfm_context_merged_into_winner(self):
        """Loser's context should be merged under a namespaced key."""
        spotify_play = _make_play(
            service="spotify",
            played_at=datetime(2024, 10, 1, 21, 4, 0, tzinfo=UTC),
            ms_played=240000,
            context={"platform": "osx"},
        )
        lastfm_existing = _make_play(
            service="lastfm",
            played_at=datetime(2024, 10, 1, 21, 0, 0, tzinfo=UTC),
            ms_played=None,
            context={"mbid": "abc-123", "loved": True},
            id=42,
        )

        result = deduplicate_cross_source_plays(
            new_plays=[spotify_play], existing_plays=[lastfm_existing]
        )

        inserted = result.plays_to_insert[0]
        assert inserted.context is not None
        assert "platform" in inserted.context
        assert "merged_from_lastfm" in inserted.context
        assert inserted.context["merged_from_lastfm"]["mbid"] == "abc-123"

    def test_fallback_tolerance_when_ms_played_missing(self):
        """When Spotify ms_played is None, use wider fallback tolerance."""
        # Without ms_played, Spotify can't normalize — uses raw played_at
        spotify_play = _make_play(
            service="spotify",
            played_at=datetime(2024, 10, 1, 21, 4, 0, tzinfo=UTC),
            ms_played=None,  # missing!
        )
        # Last.fm 100 seconds before Spotify's raw timestamp
        # Within fallback 180s but outside normal 30s
        lastfm_existing = _make_play(
            service="lastfm",
            played_at=datetime(2024, 10, 1, 21, 2, 20, tzinfo=UTC),
            ms_played=None,
            id=42,
        )

        result = deduplicate_cross_source_plays(
            new_plays=[spotify_play], existing_plays=[lastfm_existing]
        )

        # 100 seconds < 180 second fallback → should match
        assert result.stats["cross_source_matches"] == 1

    def test_multiple_tracks_independent_dedup(self):
        """Plays for different tracks should be deduped independently."""
        spotify_track1 = _make_play(
            track_id=1,
            service="spotify",
            played_at=datetime(2024, 10, 1, 21, 4, 0, tzinfo=UTC),
            ms_played=240000,
        )
        spotify_track2 = _make_play(
            track_id=2,
            service="spotify",
            played_at=datetime(2024, 10, 1, 21, 4, 0, tzinfo=UTC),
            ms_played=240000,
        )
        lastfm_track1 = _make_play(
            track_id=1,
            service="lastfm",
            played_at=datetime(2024, 10, 1, 21, 0, 0, tzinfo=UTC),
            ms_played=None,
            id=100,
        )
        # No existing Last.fm for track 2

        result = deduplicate_cross_source_plays(
            new_plays=[spotify_track1, spotify_track2],
            existing_plays=[lastfm_track1],
        )

        assert result.stats["cross_source_matches"] == 1
        assert len(result.plays_to_insert) == 2  # enriched track1 + fresh track2

    def test_no_new_plays_returns_empty(self):
        """Empty new_plays should return empty result."""
        result = deduplicate_cross_source_plays(new_plays=[], existing_plays=[])
        assert result.plays_to_insert == []
        assert result.plays_to_update == []
        assert result.suppressed_plays == []

    def test_no_existing_plays_all_inserted(self):
        """When no existing plays, all new plays should be inserted as-is."""
        plays = [
            _make_play(service="spotify", ms_played=240000),
            _make_play(service="lastfm", ms_played=None),
        ]
        result = deduplicate_cross_source_plays(
            new_plays=plays, existing_plays=[]
        )
        assert len(result.plays_to_insert) == 2
        assert len(result.suppressed_plays) == 0

    def test_null_track_id_passes_through(self):
        """Plays with no track_id should pass through to insert."""
        play = _make_play(track_id=None, service="spotify")  # type: ignore[arg-type]
        result = deduplicate_cross_source_plays(
            new_plays=[play], existing_plays=[]
        )
        assert len(result.plays_to_insert) == 1
        assert result.stats.get("no_track_id", 0) == 1


class TestComputeDedupTimeRange:
    """Test time range computation for existing play lookup."""

    def test_computes_range_with_tolerance(self):
        """Range should expand by fallback tolerance to catch edge matches."""
        plays = [
            _make_play(played_at=datetime(2024, 10, 1, 21, 0, 0, tzinfo=UTC)),
            _make_play(played_at=datetime(2024, 10, 1, 22, 0, 0, tzinfo=UTC)),
        ]
        result = compute_dedup_time_range(plays)
        assert result is not None
        start, end = result

        # 180 seconds (fallback tolerance) before earliest
        expected_start = (
            datetime(2024, 10, 1, 21, 0, 0, tzinfo=UTC) - timedelta(seconds=180)
        ).timestamp()
        expected_end = (
            datetime(2024, 10, 1, 22, 0, 0, tzinfo=UTC) + timedelta(seconds=180)
        ).timestamp()

        assert abs(start - expected_start) < 1.0
        assert abs(end - expected_end) < 1.0

    def test_empty_plays_returns_none(self):
        """Empty play list should return None."""
        assert compute_dedup_time_range([]) is None
