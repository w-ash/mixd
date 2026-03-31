"""Tests for SpotifyCrossDiscoveryProvider.

Validates that the extracted cross-discovery logic correctly searches Spotify,
evaluates match quality via the domain service, and creates connector mappings.
"""

from unittest.mock import AsyncMock, MagicMock

from src.infrastructure.connectors.spotify.cross_discovery import (
    SpotifyCrossDiscoveryProvider,
)
from tests.fixtures import make_track
from tests.fixtures.mocks import make_mock_uow


def _make_uow() -> MagicMock:
    uow = make_mock_uow()
    connector_repo = uow.get_connector_repository()
    connector_repo.map_track_to_connector.return_value = make_track(1)
    track_repo = uow.get_track_repository()
    track_repo.save_track.return_value = make_track(1)
    track_repo.find_tracks_by_isrcs.return_value = {}
    return uow


class TestSuccessfulDiscovery:
    """High-confidence matches should create a Spotify connector mapping."""

    async def test_creates_mapping_for_matching_track(self):
        artist_mock = MagicMock()
        artist_mock.name = "Radiohead"

        album_mock = MagicMock()
        album_mock.name = "Pablo Honey"

        spotify_match = MagicMock()
        spotify_match.id = "spotify123"
        spotify_match.name = "Creep"
        spotify_match.artists = [artist_mock]
        spotify_match.duration_ms = 238000
        spotify_match.album = album_mock
        spotify_match.external_ids = MagicMock(isrc="GBAYE9300106")
        spotify_match.model_dump.return_value = {"id": "spotify123", "name": "Creep"}

        connector = AsyncMock()
        connector.search_track.return_value = [spotify_match]

        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        track = make_track(id=42, title="Creep", artist="Radiohead")
        uow = _make_uow()

        result = await provider.attempt_discovery(
            track, "Radiohead", "Creep", uow, user_id="test-user"
        )

        assert result is True
        connector.search_track.assert_called_once_with("Radiohead", "Creep")
        uow.get_connector_repository().map_track_to_connector.assert_called()


class TestNoResults:
    """Empty search results should return False without creating mappings."""

    async def test_returns_false_when_no_candidates(self):
        connector = AsyncMock()
        connector.search_track.return_value = []

        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        track = make_track(id=42)
        uow = _make_uow()

        result = await provider.attempt_discovery(
            track, "Unknown", "Song", uow, user_id="test-user"
        )

        assert result is False
        uow.get_connector_repository().map_track_to_connector.assert_not_called()


class TestLowConfidence:
    """Poor matches should be rejected by the domain evaluation service."""

    async def test_rejects_dissimilar_track(self):
        spotify_match = MagicMock()
        spotify_match.id = "spotify456"
        spotify_match.name = "Completely Different Song"
        spotify_match.artists = [MagicMock(name="Someone Else")]
        spotify_match.duration_ms = 120000
        spotify_match.album = None
        spotify_match.external_ids = None
        spotify_match.model_dump.return_value = {
            "id": "spotify456",
            "name": "Completely Different Song",
        }

        connector = AsyncMock()
        connector.search_track.return_value = [spotify_match]

        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        track = make_track(id=42, title="Creep", artist="Radiohead")
        uow = _make_uow()

        result = await provider.attempt_discovery(
            track, "Radiohead", "Creep", uow, user_id="test-user"
        )

        assert result is False
        uow.get_connector_repository().map_track_to_connector.assert_not_called()


class TestExceptionHandling:
    """API errors should be caught and return False."""

    async def test_returns_false_on_search_error(self):
        connector = AsyncMock()
        connector.search_track.side_effect = RuntimeError("API down")

        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        track = make_track(id=42)
        uow = _make_uow()

        result = await provider.attempt_discovery(
            track, "Radiohead", "Creep", uow, user_id="test-user"
        )

        assert result is False


class TestISRCCollision:
    """ISRC collision check prevents duplicate canonicals during cross-discovery."""

    async def test_isrc_collision_maps_to_existing_canonical(self):
        """When Spotify match's ISRC already belongs to another canonical, map there."""
        existing_track = make_track(id=99, title="Same Song", artist="Same Artist")

        artist_mock = MagicMock()
        artist_mock.name = "Same Artist"

        spotify_match = MagicMock()
        spotify_match.id = "spotify123"
        spotify_match.name = "Same Song"
        spotify_match.artists = [artist_mock]
        spotify_match.duration_ms = 200000
        spotify_match.album = MagicMock()
        spotify_match.album.name = "Album"
        spotify_match.external_ids = MagicMock(isrc="USRC17000001")
        spotify_match.model_dump.return_value = {"id": "spotify123"}

        connector = AsyncMock()
        connector.search_track.return_value = [spotify_match]
        connector.connector_name = "spotify"

        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        track = make_track(id=42, title="Same Song", artist="Same Artist")
        uow = _make_uow()

        # Existing canonical already owns this ISRC
        track_repo = uow.get_track_repository()
        track_repo.find_tracks_by_isrcs.return_value = {"USRC17000001": existing_track}

        result = await provider.attempt_discovery(
            track,
            "Same Artist",
            "Same Song",
            uow,
            user_id="test-user",
        )

        assert result is True
        # Mapping should be on the existing track (99), not the current track (42)
        map_call = uow.get_connector_repository().map_track_to_connector
        map_call.assert_called()
        first_call = map_call.call_args
        assert first_call.args[0].id == 99  # existing_track

    async def test_no_isrc_collision_proceeds_normally(self):
        """When ISRC is not found in DB, normal discovery proceeds."""
        artist_mock = MagicMock()
        artist_mock.name = "Radiohead"

        spotify_match = MagicMock()
        spotify_match.id = "spotify123"
        spotify_match.name = "Creep"
        spotify_match.artists = [artist_mock]
        spotify_match.duration_ms = 238000
        spotify_match.album = MagicMock()
        spotify_match.album.name = "Pablo Honey"
        spotify_match.external_ids = MagicMock(isrc="GBAYE9300106")
        spotify_match.model_dump.return_value = {"id": "spotify123"}

        connector = AsyncMock()
        connector.search_track.return_value = [spotify_match]
        connector.connector_name = "spotify"

        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        track = make_track(id=42, title="Creep", artist="Radiohead")
        uow = _make_uow()

        # No existing track with this ISRC
        track_repo = uow.get_track_repository()
        track_repo.find_tracks_by_isrcs.return_value = {}

        result = await provider.attempt_discovery(
            track, "Radiohead", "Creep", uow, user_id="test-user"
        )

        assert result is True


class TestListenBrainzIntegration:
    """ListenBrainz lookup resolves tracks before Spotify search."""

    async def test_listenbrainz_match_maps_to_existing_canonical(self):
        """When ListenBrainz returns a Spotify ID already in DB, reuse it."""
        existing_track = make_track(id=99, title="Song", artist="Artist")

        lb_lookup = AsyncMock()
        lb_lookup.spotify_id_from_metadata.return_value = "existing_spotify_id"

        connector = AsyncMock()

        provider = SpotifyCrossDiscoveryProvider(
            spotify_connector=connector,
            listenbrainz_lookup=lb_lookup,
        )
        track = make_track(id=42, title="Song", artist="Artist")
        uow = _make_uow()

        # ListenBrainz-returned ID already has a canonical
        connector_repo = uow.get_connector_repository()
        connector_repo.find_tracks_by_connectors.return_value = {
            ("spotify", "existing_spotify_id"): existing_track,
        }

        result = await provider.attempt_discovery(
            track, "Artist", "Song", uow, user_id="test-user"
        )

        assert result is True
        # Should NOT have searched Spotify
        connector.search_track.assert_not_called()
        # Should have created a lastfm mapping on the existing canonical
        connector_repo.map_track_to_connector.assert_called()

    async def test_listenbrainz_miss_falls_back_to_search(self):
        """When ListenBrainz returns None, Spotify search is used."""
        lb_lookup = AsyncMock()
        lb_lookup.spotify_id_from_metadata.return_value = None

        artist_mock = MagicMock()
        artist_mock.name = "Artist"
        spotify_match = MagicMock()
        spotify_match.id = "spotify123"
        spotify_match.name = "Song"
        spotify_match.artists = [artist_mock]
        spotify_match.duration_ms = 200000
        spotify_match.album = MagicMock()
        spotify_match.album.name = "Album"
        spotify_match.external_ids = MagicMock(isrc=None)
        spotify_match.model_dump.return_value = {"id": "spotify123"}

        connector = AsyncMock()
        connector.search_track.return_value = [spotify_match]
        connector.connector_name = "spotify"

        provider = SpotifyCrossDiscoveryProvider(
            spotify_connector=connector,
            listenbrainz_lookup=lb_lookup,
        )
        track = make_track(id=42, title="Song", artist="Artist")
        uow = _make_uow()
        track_repo = uow.get_track_repository()
        track_repo.find_tracks_by_isrcs.return_value = {}

        result = await provider.attempt_discovery(
            track, "Artist", "Song", uow, user_id="test-user"
        )

        assert result is True
        connector.search_track.assert_called_once()

    async def test_no_listenbrainz_proceeds_to_search(self):
        """When no ListenBrainz lookup is configured, Spotify search is used directly."""
        artist_mock = MagicMock()
        artist_mock.name = "Radiohead"
        spotify_match = MagicMock()
        spotify_match.id = "spotify123"
        spotify_match.name = "Creep"
        spotify_match.artists = [artist_mock]
        spotify_match.duration_ms = 238000
        spotify_match.album = MagicMock()
        spotify_match.album.name = "Album"
        spotify_match.external_ids = MagicMock(isrc=None)
        spotify_match.model_dump.return_value = {"id": "spotify123"}

        connector = AsyncMock()
        connector.search_track.return_value = [spotify_match]
        connector.connector_name = "spotify"

        # No listenbrainz_lookup parameter — default None
        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        track = make_track(id=42, title="Creep", artist="Radiohead")
        uow = _make_uow()
        track_repo = uow.get_track_repository()
        track_repo.find_tracks_by_isrcs.return_value = {}

        result = await provider.attempt_discovery(
            track, "Radiohead", "Creep", uow, user_id="test-user"
        )

        assert result is True
        connector.search_track.assert_called_once()
