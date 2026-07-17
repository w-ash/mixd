"""Tests for LastfmConnectorPlayResolver.

Validates the resolver that folds the former thin resolution service: it
extracts unique artist::title identifiers from connector plays, delegates
bulk resolution to a LastfmInwardResolver, maps canonical tracks back to
input order, builds TrackPlay objects with preserved Last.fm metadata, and
exposes resolution metrics plus progress callbacks.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from src.domain.entities import ConnectorTrackPlay
from src.infrastructure.connectors._shared.inward_track_resolver import (
    TrackResolutionMetrics,
)
from src.infrastructure.connectors.lastfm.identifiers import make_lastfm_identifier
from src.infrastructure.connectors.lastfm.play_resolver import (
    LastfmConnectorPlayResolver,
)
from tests.fixtures import make_track
from tests.fixtures.mocks import make_mock_uow


def _play(
    artist: str, track: str, *, minute: int = 0, **metadata: object
) -> ConnectorTrackPlay:
    """Build a minimal Last.fm ConnectorTrackPlay."""
    return ConnectorTrackPlay(
        artist_name=artist,
        track_name=track,
        played_at=datetime(2024, 3, 15, 12, minute, tzinfo=UTC),
        service="lastfm",
        service_metadata=metadata,
    )


def _make_resolver(
    *,
    canonical_tracks: dict | None = None,
    metrics: TrackResolutionMetrics | None = None,
) -> tuple[LastfmConnectorPlayResolver, AsyncMock]:
    """Build a resolver with its inward resolver mocked.

    Returns the resolver and the AsyncMock passed in as the inward resolver
    so callers can assert on delegation behavior.
    """
    inward = AsyncMock()
    inward.resolve_to_canonical_tracks.return_value = (
        canonical_tracks or {},
        metrics or TrackResolutionMetrics(),
    )
    resolver = LastfmConnectorPlayResolver(
        lastfm_client=MagicMock(),
        inward_resolver=inward,
    )
    return resolver, inward


class TestResolveEmptyInput:
    """Empty / unresolvable input short-circuits without touching the resolver."""

    async def test_empty_plays_returns_empty_result(self):
        resolver, inward = _make_resolver()
        uow = make_mock_uow()

        outcome = await resolver.resolve_connector_plays([], uow, user_id="user-1")
        plays, metrics = outcome.track_plays, outcome.metrics

        assert plays == []
        assert metrics == {
            "raw_plays": 0,
            "accepted_plays": 0,
            "error_count": 0,
            "resolution_failures": [],
            "new_tracks_count": 0,
            "updated_tracks_count": 0,
            "spotify_enhanced_count": 0,
        }
        inward.resolve_to_canonical_tracks.assert_not_awaited()

    async def test_all_records_missing_artist_or_track_yield_no_plays(self):
        """No valid identifiers → inward untouched, no track plays, no failures."""
        resolver, inward = _make_resolver()
        uow = make_mock_uow()
        plays_in = [_play("", "Song"), _play("Artist", "")]

        outcome = await resolver.resolve_connector_plays(
            plays_in, uow, user_id="user-1"
        )
        plays, metrics = outcome.track_plays, outcome.metrics

        assert plays == []
        assert metrics["accepted_plays"] == 0
        assert metrics["error_count"] == 0
        assert metrics["new_tracks_count"] == 0
        inward.resolve_to_canonical_tracks.assert_not_awaited()


class TestResolveHappyPath:
    """Plays with valid artist/title resolve into TrackPlays in input order."""

    async def test_single_play_resolves_to_track_play(self):
        track = make_track(title="Creep", artist="Radiohead")
        identifier = make_lastfm_identifier("Radiohead", "Creep")
        resolver, _ = _make_resolver(
            canonical_tracks={identifier: track},
            metrics=TrackResolutionMetrics(existing=1),
        )
        uow = make_mock_uow()

        outcome = await resolver.resolve_connector_plays(
            [_play("Radiohead", "Creep")], uow, user_id="user-1"
        )
        plays, metrics = outcome.track_plays, outcome.metrics

        assert len(plays) == 1
        assert plays[0].track_id == track.id
        assert plays[0].service == "lastfm"
        assert metrics["accepted_plays"] == 1
        assert metrics["updated_tracks_count"] == 1
        assert metrics["new_tracks_count"] == 0
        assert metrics["spotify_enhanced_count"] == 0

    async def test_multiple_plays_preserve_input_order(self):
        t1 = make_track(title="Creep", artist="Radiohead")
        t2 = make_track(title="Karma Police", artist="Radiohead")
        canonical = {
            make_lastfm_identifier("Radiohead", "Creep"): t1,
            make_lastfm_identifier("Radiohead", "Karma Police"): t2,
        }
        resolver, _ = _make_resolver(
            canonical_tracks=canonical,
            metrics=TrackResolutionMetrics(existing=1, created=1),
        )
        uow = make_mock_uow()

        plays_in = [
            _play("Radiohead", "Karma Police", minute=1),
            _play("Radiohead", "Creep", minute=2),
        ]
        outcome = await resolver.resolve_connector_plays(
            plays_in, uow, user_id="user-1"
        )
        plays, metrics = outcome.track_plays, outcome.metrics

        assert [p.track_id for p in plays] == [t2.id, t1.id]
        assert metrics["updated_tracks_count"] == 1
        assert metrics["new_tracks_count"] == 1

    async def test_partial_resolution_records_failure(self):
        resolved = make_track(title="Creep", artist="Radiohead")
        canonical = {make_lastfm_identifier("Radiohead", "Creep"): resolved}
        resolver, _ = _make_resolver(
            canonical_tracks=canonical,
            metrics=TrackResolutionMetrics(existing=1, failed=1),
        )
        uow = make_mock_uow()

        plays_in = [
            _play("Radiohead", "Creep"),
            _play("Unknown", "Track"),
        ]
        outcome = await resolver.resolve_connector_plays(
            plays_in, uow, user_id="user-1"
        )
        plays, metrics = outcome.track_plays, outcome.metrics

        assert len(plays) == 1
        assert plays[0].track_id == resolved.id
        assert metrics["error_count"] == 1
        assert len(metrics["resolution_failures"]) == 1
        assert metrics["resolution_failures"][0]["reason"] == "track_resolution_failed"
        assert metrics["resolution_failures"][0]["track"] == "Unknown - Track"

    async def test_duplicate_plays_map_to_same_track_and_dedupe(self):
        track = make_track(title="Creep", artist="Radiohead")
        canonical = {make_lastfm_identifier("Radiohead", "Creep"): track}
        resolver, inward = _make_resolver(
            canonical_tracks=canonical,
            metrics=TrackResolutionMetrics(existing=1),
        )
        uow = make_mock_uow()

        plays_in = [
            _play("Radiohead", "Creep", minute=1),
            _play("radiohead", "creep", minute=2),  # case variant
            _play("Radiohead", "Creep", minute=3),
        ]
        outcome = await resolver.resolve_connector_plays(
            plays_in, uow, user_id="user-1"
        )
        plays, _metrics = outcome.track_plays, outcome.metrics

        assert [p.track_id for p in plays] == [track.id, track.id, track.id]

        passed_ids = inward.resolve_to_canonical_tracks.call_args.args[0]
        assert len(passed_ids) == 1


class TestResolveMetricsMapping:
    """Inward TrackResolutionMetrics maps to the resolver's output dict."""

    async def test_metrics_mapped_from_inward_resolver(self):
        track = make_track(title="Creep", artist="Radiohead")
        resolver, _ = _make_resolver(
            canonical_tracks={make_lastfm_identifier("Radiohead", "Creep"): track},
            metrics=TrackResolutionMetrics(existing=7, created=3, reused=2, failed=1),
        )
        uow = make_mock_uow()

        outcome = await resolver.resolve_connector_plays(
            [_play("Radiohead", "Creep")], uow, user_id="user-1"
        )
        _plays, metrics = outcome.track_plays, outcome.metrics

        assert metrics["updated_tracks_count"] == 7  # inward "existing"
        assert metrics["new_tracks_count"] == 3  # inward "created"
        # spotify_enhanced_count is intentionally always 0 — inward tracks it internally
        assert metrics["spotify_enhanced_count"] == 0


class TestResolveContextKeys:
    """The persisted context key set is pinned (track_plays.context JSON contract)."""

    async def test_resolved_play_context_keys_are_byte_identical(self):
        track = make_track(title="Creep", artist="Radiohead")
        resolver, _ = _make_resolver(
            canonical_tracks={make_lastfm_identifier("Radiohead", "Creep"): track},
            metrics=TrackResolutionMetrics(existing=1),
        )
        uow = make_mock_uow()
        play = _play("Radiohead", "Creep", mbid="mbid-1", loved="1", extra_key="extra")

        outcome = await resolver.resolve_connector_plays([play], uow, user_id="user-1")
        plays, _metrics = outcome.track_plays, outcome.metrics

        context = plays[0].context
        assert set(context.keys()) == {
            "track_name",
            "artist_name",
            "album_name",
            "lastfm_track_url",
            "lastfm_artist_url",
            "lastfm_album_url",
            "mbid",
            "artist_mbid",
            "album_mbid",
            "streamable",
            "loved",
            "resolution_method",
            "architecture_version",
            "extra_key",  # unrecognized metadata is passed through verbatim
        }
        assert context["architecture_version"] == "connector_plays_deferred_resolution"
        assert context["resolution_method"] == "lastfm_connector_play_resolver"
        assert context["mbid"] == "mbid-1"
        assert context["extra_key"] == "extra"


class TestResolveProgressCallbacks:
    """progress_callback is invoked at each pipeline checkpoint with correct args."""

    async def test_callback_invoked_at_each_step(self):
        track = make_track(title="Creep", artist="Radiohead")
        resolver, _ = _make_resolver(
            canonical_tracks={make_lastfm_identifier("Radiohead", "Creep"): track},
            metrics=TrackResolutionMetrics(existing=1),
        )
        uow = make_mock_uow()
        callback = MagicMock()

        await resolver.resolve_connector_plays(
            [_play("Radiohead", "Creep")],
            uow,
            user_id="user-1",
            progress_callback=callback,
        )

        percents = [c.args[0] for c in callback.call_args_list]
        assert percents == [10, 30, 80, 100]
        assert all(c.args[1] == 100 for c in callback.call_args_list)
        assert "1/1" in callback.call_args_list[-1].args[2]

    async def test_callback_not_required(self):
        track = make_track(title="Creep", artist="Radiohead")
        resolver, _ = _make_resolver(
            canonical_tracks={make_lastfm_identifier("Radiohead", "Creep"): track},
            metrics=TrackResolutionMetrics(existing=1),
        )
        uow = make_mock_uow()

        outcome = await resolver.resolve_connector_plays(
            [_play("Radiohead", "Creep")],
            uow,
            user_id="user-1",
            progress_callback=None,
        )
        plays, _metrics = outcome.track_plays, outcome.metrics

        assert len(plays) == 1


class TestExtractUniqueIdentifiers:
    """_extract_unique_lastfm_identifiers dedupes, normalizes, and skips invalid rows."""

    def test_dedupes_by_normalized_identifier(self):
        resolver, _ = _make_resolver()
        plays = [
            _play("Radiohead", "Creep"),
            _play("RADIOHEAD", "CREEP"),  # case
            _play("  Radiohead  ", "  Creep  "),  # whitespace
            _play("Radiohead", "Karma Police"),
        ]

        result = resolver._extract_unique_lastfm_identifiers(plays)

        assert len(result) == 2
        assert make_lastfm_identifier("Radiohead", "Creep") in result
        assert make_lastfm_identifier("Radiohead", "Karma Police") in result

    def test_skips_records_missing_artist_or_track(self):
        resolver, _ = _make_resolver()
        plays = [
            _play("", "Song A"),
            _play("Artist B", ""),
            _play("Artist C", "Song C"),
        ]

        result = resolver._extract_unique_lastfm_identifiers(plays)

        assert result == {make_lastfm_identifier("Artist C", "Song C")}

    def test_returns_empty_for_empty_input(self):
        resolver, _ = _make_resolver()
        assert resolver._extract_unique_lastfm_identifiers([]) == set()


class TestInwardResolverDelegation:
    """Resolver passes deduped identifier list and user_id to the inward resolver."""

    async def test_delegates_with_deduped_ids_and_user_id(self):
        track = make_track(title="Creep", artist="Radiohead")
        resolver, inward = _make_resolver(
            canonical_tracks={make_lastfm_identifier("Radiohead", "Creep"): track},
            metrics=TrackResolutionMetrics(existing=1),
        )
        uow = make_mock_uow()

        await resolver.resolve_connector_plays(
            [
                _play("Radiohead", "Creep", minute=1),
                _play("Radiohead", "Creep", minute=2),
            ],
            uow,
            user_id="user-42",
        )

        call = inward.resolve_to_canonical_tracks.call_args
        assert call.args[0] == [make_lastfm_identifier("Radiohead", "Creep")]
        assert call.args[1] is uow
        assert call.kwargs["user_id"] == "user-42"


class TestResolverConstruction:
    """LastfmConnectorPlayResolver wires its own client/resolver when omitted."""

    def test_defaults_create_internal_collaborators(self):
        resolver = LastfmConnectorPlayResolver()

        assert resolver.lastfm_client is not None
        assert resolver._inward_resolver is not None

    def test_accepts_injected_client(self):
        client = MagicMock()
        resolver = LastfmConnectorPlayResolver(lastfm_client=client)

        assert resolver.lastfm_client is client
