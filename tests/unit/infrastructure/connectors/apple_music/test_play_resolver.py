"""Tests for AppleMusicConnectorPlayResolver.

Validates resolution of ``connector_plays`` rows for service ``"apple"``
through the inward resolver: resolved plays become ``TrackPlay`` rows tagged
``service="apple"``, unresolved plays are excluded as ``"unresolved"`` and
keep no resolution (their ledger rows stay ``resolved_track_id = NULL``).
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from src.domain.entities import ConnectorTrackPlay
from src.infrastructure.connectors._shared.inward_track_resolver import (
    TrackResolutionMetrics,
)
from src.infrastructure.connectors.apple_music.play_resolver import (
    AppleMusicConnectorPlayResolver,
)
from tests.fixtures import make_track


def _play(song_id: str, track_name: str = "Test Song") -> ConnectorTrackPlay:
    return ConnectorTrackPlay(
        service="apple",
        artist_name="Test Artist",
        track_name=track_name,
        played_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        user_id="u1",
        service_metadata={"song_id": song_id},
        import_timestamp=datetime.now(UTC),
        import_batch_id="batch-1",
    )


def _resolver_with(tracks_by_id):
    inward = AsyncMock()
    inward.resolve_to_canonical_tracks.return_value = (
        tracks_by_id,
        TrackResolutionMetrics(created=len(tracks_by_id)),
    )
    return AppleMusicConnectorPlayResolver(
        client=AsyncMock(), inward_resolver=inward
    ), inward


class TestResolveConnectorPlays:
    async def test_resolved_play_becomes_apple_track_play(self):
        track = make_track(1)
        resolver, inward = _resolver_with({"101": track})
        uow = MagicMock()

        outcome = await resolver.resolve_connector_plays(
            [_play("101")], uow, user_id="u1"
        )

        assert len(outcome.track_plays) == 1
        play = outcome.track_plays[0]
        assert play.service == "apple"
        assert play.track_id == track.id
        assert play.user_id == "u1"
        assert outcome.metrics["accepted_plays"] == 1
        (resolved_play, resolved_id) = outcome.resolutions[0]
        assert resolved_id == track.id
        inward.resolve_to_canonical_tracks.assert_awaited_once()
        assert inward.resolve_to_canonical_tracks.await_args.args[0] == ["101"]

    async def test_unresolved_play_is_excluded_not_resolved(self):
        resolver, _ = _resolver_with({})
        uow = MagicMock()
        play = _play("gone404", track_name="Ghost")

        outcome = await resolver.resolve_connector_plays([play], uow, user_id="u1")

        assert outcome.track_plays == []
        assert outcome.resolutions == ()
        assert outcome.exclusions == ((play, "unresolved"),)
        assert outcome.metrics["error_count"] == 1

    async def test_play_without_song_id_is_excluded(self):
        resolver, inward = _resolver_with({})
        uow = MagicMock()
        play = ConnectorTrackPlay(
            service="apple",
            artist_name="Test Artist",
            track_name="No Id",
            played_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            user_id="u1",
        )

        outcome = await resolver.resolve_connector_plays([play], uow, user_id="u1")

        assert outcome.exclusions == ((play, "unresolved"),)
        inward.resolve_to_canonical_tracks.assert_not_awaited()

    async def test_empty_input_short_circuits(self):
        resolver, inward = _resolver_with({})
        outcome = await resolver.resolve_connector_plays([], MagicMock(), user_id="u1")
        assert outcome.track_plays == []
        inward.resolve_to_canonical_tracks.assert_not_awaited()


class TestLifecycle:
    async def test_aclose_closes_the_client_pool(self):
        """The orchestrator closes factory-built resolvers after resolution;
        the resolver's aclose must release its client's httpx2 pool."""
        client = AsyncMock()
        resolver = AppleMusicConnectorPlayResolver(
            client=client, inward_resolver=AsyncMock()
        )

        await resolver.aclose()

        client.aclose.assert_awaited_once()


class TestRegistryWiring:
    async def test_registry_creates_apple_resolver(self):
        from src.infrastructure.services.play_import_registry import (
            get_play_import_registry,
        )

        registry = get_play_import_registry()
        resolver = await registry.create_play_resolver("apple")
        assert isinstance(resolver, AppleMusicConnectorPlayResolver)
