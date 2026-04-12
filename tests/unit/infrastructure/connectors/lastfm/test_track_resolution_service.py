"""Tests for LastfmTrackResolutionService.

Validates the thin orchestration that extracts unique artist::title
identifiers from play records, delegates bulk resolution to a
LastfmInwardResolver, maps canonical tracks back to input order, and
exposes resolution metrics plus progress callbacks.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from src.domain.entities import PlayRecord
from src.infrastructure.connectors._shared.inward_track_resolver import (
    TrackResolutionMetrics,
)
from src.infrastructure.connectors.lastfm.identifiers import make_lastfm_identifier
from src.infrastructure.connectors.lastfm.track_resolution_service import (
    LastfmTrackResolutionService,
)
from tests.fixtures import make_track
from tests.fixtures.mocks import make_mock_uow


def _play(artist: str, track: str, *, minute: int = 0) -> PlayRecord:
    """Build a minimal Last.fm PlayRecord."""
    return PlayRecord(
        artist_name=artist,
        track_name=track,
        played_at=datetime(2024, 3, 15, 12, minute, tzinfo=UTC),
        service="lastfm",
    )


def _make_service(
    *,
    canonical_tracks: dict | None = None,
    metrics: TrackResolutionMetrics | None = None,
) -> tuple[LastfmTrackResolutionService, AsyncMock]:
    """Build a LastfmTrackResolutionService with its inward resolver mocked.

    Returns the service and the AsyncMock passed in as the inward resolver
    so callers can assert on delegation behavior.
    """
    inward = AsyncMock()
    inward.resolve_to_canonical_tracks.return_value = (
        canonical_tracks or {},
        metrics or TrackResolutionMetrics(),
    )
    service = LastfmTrackResolutionService(
        cross_discovery=AsyncMock(),
        lastfm_client=MagicMock(),
        inward_resolver=inward,
    )
    return service, inward


class TestResolveEmptyInput:
    """An empty play_records list short-circuits without touching the resolver."""

    async def test_empty_play_records_returns_empty_result(self):
        service, inward = _make_service()
        uow = make_mock_uow()

        tracks, metrics = await service.resolve_plays_to_canonical_tracks(
            [], uow, user_id="user-1"
        )

        assert tracks == []
        assert metrics == {
            "existing_mappings": 0,
            "new_tracks": 0,
            "spotify_enhanced": 0,
        }
        inward.resolve_to_canonical_tracks.assert_not_awaited()

    async def test_all_records_missing_artist_or_track_returns_empty(self):
        service, inward = _make_service()
        uow = make_mock_uow()
        plays = [_play("", "Song"), _play("Artist", "")]

        tracks, metrics = await service.resolve_plays_to_canonical_tracks(
            plays, uow, user_id="user-1"
        )

        assert tracks == []
        assert metrics["existing_mappings"] == 0
        assert metrics["new_tracks"] == 0
        inward.resolve_to_canonical_tracks.assert_not_awaited()


class TestResolveHappyPath:
    """Plays with valid artist/title resolve into canonical tracks in order."""

    async def test_single_play_resolves_to_track(self):
        track = make_track(title="Creep", artist="Radiohead")
        identifier = make_lastfm_identifier("Radiohead", "Creep")
        service, inward = _make_service(
            canonical_tracks={identifier: track},
            metrics=TrackResolutionMetrics(existing=1),
        )
        uow = make_mock_uow()

        tracks, metrics = await service.resolve_plays_to_canonical_tracks(
            [_play("Radiohead", "Creep")], uow, user_id="user-1"
        )

        assert tracks == [track]
        assert metrics == {
            "existing_mappings": 1,
            "new_tracks": 0,
            "spotify_enhanced": 0,
        }

    async def test_multiple_plays_preserve_input_order(self):
        t1 = make_track(title="Creep", artist="Radiohead")
        t2 = make_track(title="Karma Police", artist="Radiohead")
        canonical = {
            make_lastfm_identifier("Radiohead", "Creep"): t1,
            make_lastfm_identifier("Radiohead", "Karma Police"): t2,
        }
        service, _ = _make_service(
            canonical_tracks=canonical,
            metrics=TrackResolutionMetrics(existing=1, created=1),
        )
        uow = make_mock_uow()

        plays = [
            _play("Radiohead", "Karma Police", minute=1),
            _play("Radiohead", "Creep", minute=2),
        ]
        tracks, metrics = await service.resolve_plays_to_canonical_tracks(
            plays, uow, user_id="user-1"
        )

        assert tracks == [t2, t1]
        assert metrics["existing_mappings"] == 1
        assert metrics["new_tracks"] == 1

    async def test_partial_resolution_yields_none_for_unresolved(self):
        resolved = make_track(title="Creep", artist="Radiohead")
        canonical = {make_lastfm_identifier("Radiohead", "Creep"): resolved}
        service, _ = _make_service(
            canonical_tracks=canonical,
            metrics=TrackResolutionMetrics(existing=1, failed=1),
        )
        uow = make_mock_uow()

        plays = [
            _play("Radiohead", "Creep"),
            _play("Unknown", "Track"),
        ]
        tracks, _metrics = await service.resolve_plays_to_canonical_tracks(
            plays, uow, user_id="user-1"
        )

        assert tracks == [resolved, None]

    async def test_duplicate_plays_map_to_same_resolved_track(self):
        track = make_track(title="Creep", artist="Radiohead")
        canonical = {make_lastfm_identifier("Radiohead", "Creep"): track}
        service, inward = _make_service(
            canonical_tracks=canonical,
            metrics=TrackResolutionMetrics(existing=1),
        )
        uow = make_mock_uow()

        plays = [
            _play("Radiohead", "Creep", minute=1),
            _play("radiohead", "creep", minute=2),  # case variant
            _play("Radiohead", "Creep", minute=3),
        ]
        tracks, _metrics = await service.resolve_plays_to_canonical_tracks(
            plays, uow, user_id="user-1"
        )

        assert tracks == [track, track, track]

        call_args = inward.resolve_to_canonical_tracks.call_args
        passed_ids = call_args.args[0]
        assert len(passed_ids) == 1


class TestResolveMetricsMapping:
    """Inward TrackResolutionMetrics maps to the service's output dict."""

    async def test_metrics_mapped_from_inward_resolver(self):
        track = make_track(title="Creep", artist="Radiohead")
        service, _ = _make_service(
            canonical_tracks={make_lastfm_identifier("Radiohead", "Creep"): track},
            metrics=TrackResolutionMetrics(existing=7, created=3, reused=2, failed=1),
        )
        uow = make_mock_uow()

        _tracks, metrics = await service.resolve_plays_to_canonical_tracks(
            [_play("Radiohead", "Creep")], uow, user_id="user-1"
        )

        assert metrics["existing_mappings"] == 7
        assert metrics["new_tracks"] == 3
        # spotify_enhanced is intentionally always 0 — inward tracks it internally
        assert metrics["spotify_enhanced"] == 0


class TestResolveProgressCallbacks:
    """progress_callback is invoked at each pipeline checkpoint with correct args."""

    async def test_callback_invoked_at_each_step(self):
        track = make_track(title="Creep", artist="Radiohead")
        service, _ = _make_service(
            canonical_tracks={make_lastfm_identifier("Radiohead", "Creep"): track},
            metrics=TrackResolutionMetrics(existing=1),
        )
        uow = make_mock_uow()
        callback = MagicMock()

        await service.resolve_plays_to_canonical_tracks(
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
        service, _ = _make_service(
            canonical_tracks={make_lastfm_identifier("Radiohead", "Creep"): track},
            metrics=TrackResolutionMetrics(existing=1),
        )
        uow = make_mock_uow()

        tracks, _metrics = await service.resolve_plays_to_canonical_tracks(
            [_play("Radiohead", "Creep")],
            uow,
            user_id="user-1",
            progress_callback=None,
        )

        assert tracks == [track]


class TestExtractUniqueIdentifiers:
    """_extract_unique_lastfm_identifiers dedupes, normalizes, and skips invalid rows."""

    def test_dedupes_by_normalized_identifier(self):
        service = LastfmTrackResolutionService(
            cross_discovery=AsyncMock(), lastfm_client=MagicMock()
        )
        plays = [
            _play("Radiohead", "Creep"),
            _play("RADIOHEAD", "CREEP"),  # case
            _play("  Radiohead  ", "  Creep  "),  # whitespace
            _play("Radiohead", "Karma Police"),
        ]

        result = service._extract_unique_lastfm_identifiers(plays)

        assert len(result) == 2
        assert make_lastfm_identifier("Radiohead", "Creep") in result
        assert make_lastfm_identifier("Radiohead", "Karma Police") in result

    def test_skips_records_missing_artist_or_track(self):
        service = LastfmTrackResolutionService(
            cross_discovery=AsyncMock(), lastfm_client=MagicMock()
        )
        plays = [
            _play("", "Song A"),
            _play("Artist B", ""),
            _play("Artist C", "Song C"),
        ]

        result = service._extract_unique_lastfm_identifiers(plays)

        assert result == {make_lastfm_identifier("Artist C", "Song C")}

    def test_returns_empty_for_empty_input(self):
        service = LastfmTrackResolutionService(
            cross_discovery=AsyncMock(), lastfm_client=MagicMock()
        )
        assert service._extract_unique_lastfm_identifiers([]) == set()


class TestInwardResolverDelegation:
    """Service passes deduped identifier set and user_id to the inward resolver."""

    async def test_delegates_with_deduped_ids_and_user_id(self):
        track = make_track(title="Creep", artist="Radiohead")
        service, inward = _make_service(
            canonical_tracks={make_lastfm_identifier("Radiohead", "Creep"): track},
            metrics=TrackResolutionMetrics(existing=1),
        )
        uow = make_mock_uow()

        await service.resolve_plays_to_canonical_tracks(
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


class TestServiceConstruction:
    """LastfmTrackResolutionService wires its own client/resolver when omitted."""

    def test_defaults_create_internal_collaborators(self):
        service = LastfmTrackResolutionService()

        assert service.lastfm_client is not None
        assert service._inward_resolver is not None

    def test_accepts_injected_client(self):
        client = MagicMock()
        service = LastfmTrackResolutionService(lastfm_client=client)

        assert service.lastfm_client is client
