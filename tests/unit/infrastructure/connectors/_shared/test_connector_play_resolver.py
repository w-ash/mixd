"""Unit tests for the shared connector play outcome builder.

Covers the ``extra_exclusions`` and ``build_context`` seams the Spotify
resolver routes through, and the defaults Apple/Last.fm rely on.
"""

from datetime import UTC, datetime

from src.domain.entities import ConnectorTrackPlay
from src.domain.matching.play_projection import build_play_context
from src.infrastructure.connectors._shared.connector_play_resolver import (
    build_play_outcome,
)
from src.infrastructure.connectors._shared.inward_track_resolver import (
    TrackResolutionMetrics,
)
from tests.fixtures.factories import make_track


def _play(track_name: str = "Song", artist_name: str = "Artist") -> ConnectorTrackPlay:
    return ConnectorTrackPlay(
        service="lastfm",
        track_name=track_name,
        artist_name=artist_name,
        played_at=datetime(2024, 6, 15, 14, 30, tzinfo=UTC),
        ms_played=240000,
    )


def _build(resolved, **kwargs):
    return build_play_outcome(
        resolved,
        service="lastfm",
        user_id="test-user",
        default_import_source="lastfm_api",
        resolution_metrics=TrackResolutionMetrics(),
        **kwargs,
    )


class TestExtraExclusions:
    """Pre-decided exclusions merge into the outcome ahead of the loop's own."""

    def test_extra_exclusions_lead_the_outcomes_exclusions(self):
        accepted_play, unresolved_play, incognito_play = (
            _play("A"),
            _play("B"),
            _play("C"),
        )

        outcome = _build(
            [(accepted_play, make_track()), (unresolved_play, None)],
            extra_exclusions=[(incognito_play, "incognito")],
        )

        assert outcome.exclusions == (
            (incognito_play, "incognito"),
            (unresolved_play, "unresolved"),
        )

    def test_extra_exclusions_count_into_raw_plays_only(self):
        outcome = _build(
            [(_play("A"), make_track()), (_play("B"), None)],
            extra_exclusions=[(_play("C"), "incognito"), (_play("D"), "too_short")],
        )

        assert outcome.metrics["raw_plays"] == 4
        assert outcome.metrics["accepted_plays"] == 1
        assert outcome.metrics["error_count"] == 1
        assert len(outcome.track_plays) == 1

    def test_the_default_is_no_extra_exclusions(self):
        unresolved_play = _play("B")

        outcome = _build([(_play("A"), make_track()), (unresolved_play, None)])

        assert outcome.metrics["raw_plays"] == 2
        assert outcome.exclusions == ((unresolved_play, "unresolved"),)


class TestBuildContextHook:
    """A connector can replace the domain context builder per play."""

    def test_custom_builder_shapes_the_persisted_context(self):
        outcome = _build(
            [(_play(), make_track())],
            build_context=lambda play: {"resolution_method": "custom"},
        )

        assert outcome.track_plays[0].context == {"resolution_method": "custom"}

    def test_default_context_comes_from_the_domain_builder(self):
        play = _play()

        outcome = _build([(play, make_track())])

        assert outcome.track_plays[0].context == build_play_context(play)
