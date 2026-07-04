"""Tests for SpotifyCrossDiscoveryProvider.

Validates that the extracted cross-discovery logic searches Spotify, evaluates
match quality via the domain service, and returns the right ``DiscoveryOutcome``
decision (ReuseExisting / NewMapping / Nothing) for its caller to apply. The
provider itself does NOT mutate the caller's canonical — it only reports the
decision (and performs its own side effects, e.g. queuing an ISRC review).
"""

from unittest.mock import AsyncMock, MagicMock

from src.config.constants import MatchMethod
from src.domain.matching.protocols import NewMapping, Nothing, ReuseExisting
from src.infrastructure.connectors.spotify.cross_discovery import (
    SpotifyCrossDiscoveryProvider,
)
from tests.fixtures import make_track
from tests.fixtures.mocks import make_mock_uow


def _make_uow() -> MagicMock:
    uow = make_mock_uow()
    track_repo = uow.get_track_repository()
    track_repo.find_tracks_by_isrcs.return_value = {}
    return uow


class TestSuccessfulDiscovery:
    """High-confidence matches should return a NewMapping decision."""

    async def test_returns_new_mapping_for_matching_track(self):
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
        connector.connector_name = "spotify"

        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        track = make_track(id=42, title="Creep", artist="Radiohead")
        uow = _make_uow()

        outcome = await provider.discover(
            track, "Radiohead", "Creep", uow, user_id="test-user"
        )

        assert isinstance(outcome, NewMapping)
        assert outcome.spotify_id == "spotify123"
        assert outcome.match_method == MatchMethod.LASTFM_DISCOVERY
        # ISRC carried for backfill (normalized).
        assert outcome.isrc == "GBAYE9300106"
        connector.search_track.assert_called_once_with("Radiohead", "Creep")


class TestNoResults:
    """Empty search results should return Nothing."""

    async def test_returns_nothing_when_no_candidates(self):
        connector = AsyncMock()
        connector.search_track.return_value = []

        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        track = make_track(id=42)
        uow = _make_uow()

        outcome = await provider.discover(
            track, "Unknown", "Song", uow, user_id="test-user"
        )

        assert isinstance(outcome, Nothing)


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

        outcome = await provider.discover(
            track, "Radiohead", "Creep", uow, user_id="test-user"
        )

        assert isinstance(outcome, Nothing)


class TestExceptionHandling:
    """API errors should be caught and return Nothing."""

    async def test_returns_nothing_on_search_error(self):
        connector = AsyncMock()
        connector.search_track.side_effect = RuntimeError("API down")

        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        track = make_track(id=42)
        uow = _make_uow()

        outcome = await provider.discover(
            track, "Radiohead", "Creep", uow, user_id="test-user"
        )

        assert isinstance(outcome, Nothing)


class TestISRCCollision:
    """ISRC collision check prevents duplicate canonicals during cross-discovery."""

    async def test_isrc_collision_reuses_existing_canonical(self):
        """When the Spotify match's ISRC already belongs to another canonical and
        the collision is non-suspect, return a ReuseExisting decision pointing at
        the owner, carrying the Spotify mapping to create on it."""
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

        # Existing canonical already owns this ISRC (no duration → non-suspect).
        track_repo = uow.get_track_repository()
        track_repo.find_tracks_by_isrcs.return_value = {"USRC17000001": existing_track}

        outcome = await provider.discover(
            track, "Same Artist", "Same Song", uow, user_id="test-user"
        )

        # Reuse the ISRC owner (99), with the found Spotify id to map onto it.
        assert isinstance(outcome, ReuseExisting)
        assert outcome.track.id == 99
        assert outcome.spotify_id == "spotify123"
        assert outcome.match_method == MatchMethod.ISRC_MATCH
        # No review queued for a non-suspect (clean) collision.
        uow.get_connector_repository().queue_isrc_collision_review.assert_not_called()

    async def test_suspect_isrc_collision_queues_review_and_strips_isrc(self):
        """When the ISRC owner's duration diverges past the threshold, the
        collision is suspect: queue a review and return a NewMapping whose ISRC
        is stripped, so the new canonical never claims the contested code."""
        existing_track = make_track(
            id=99, title="Gold Rush", artist="Neon Priest", duration_ms=200000
        )

        artist_mock = MagicMock()
        artist_mock.name = "Neon Priest"

        spotify_match = MagicMock()
        spotify_match.id = "sp_remaster"
        spotify_match.name = "Gold Rush"
        spotify_match.artists = [artist_mock]
        spotify_match.duration_ms = 220000  # 20s off — above the 10s suspect threshold
        spotify_match.album = MagicMock()
        spotify_match.album.name = "Remaster"
        spotify_match.external_ids = MagicMock(isrc="USNP12400001")
        spotify_match.model_dump.return_value = {"id": "sp_remaster"}

        connector = AsyncMock()
        connector.search_track.return_value = [spotify_match]
        connector.connector_name = "spotify"

        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        track = make_track(id=42, title="Gold Rush", artist="Neon Priest")
        uow = _make_uow()

        track_repo = uow.get_track_repository()
        track_repo.find_tracks_by_isrcs.return_value = {"USNP12400001": existing_track}

        outcome = await provider.discover(
            track, "Neon Priest", "Gold Rush", uow, user_id="test-user"
        )

        # New canonical (not a merge), but the contested ISRC is stripped...
        assert isinstance(outcome, NewMapping)
        assert outcome.spotify_id == "sp_remaster"
        assert outcome.isrc is None
        # ...and a review was queued against the ISRC owner.
        connector_repo = uow.get_connector_repository()
        connector_repo.queue_isrc_collision_review.assert_called_once()
        review_call = connector_repo.queue_isrc_collision_review.call_args
        assert review_call.args[0].id == 99  # existing owner
        assert review_call.kwargs["user_id"] == "test-user"

    async def test_no_isrc_collision_proceeds_normally(self):
        """When the ISRC is not in the DB, a normal NewMapping is returned."""
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

        outcome = await provider.discover(
            track, "Radiohead", "Creep", uow, user_id="test-user"
        )

        assert isinstance(outcome, NewMapping)
        assert outcome.spotify_id == "spotify123"


class TestListenBrainzIntegration:
    """ListenBrainz lookup resolves tracks before Spotify search."""

    async def test_listenbrainz_match_reuses_existing_canonical(self):
        """When ListenBrainz returns a Spotify ID already in DB, reuse it. The
        existing canonical already carries the Spotify mapping, so spotify_id is
        None and no Spotify search happens."""
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

        outcome = await provider.discover(
            track, "Artist", "Song", uow, user_id="test-user"
        )

        assert isinstance(outcome, ReuseExisting)
        assert outcome.track.id == 99
        # The reused canonical already carries the Spotify mapping.
        assert outcome.spotify_id is None
        # Should NOT have searched Spotify
        connector.search_track.assert_not_called()

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

        outcome = await provider.discover(
            track, "Artist", "Song", uow, user_id="test-user"
        )

        assert isinstance(outcome, NewMapping)
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

        # No listenbrainz_lookup parameter — default None
        connector = AsyncMock()
        connector.search_track.return_value = [spotify_match]
        connector.connector_name = "spotify"

        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        track = make_track(id=42, title="Creep", artist="Radiohead")
        uow = _make_uow()

        outcome = await provider.discover(
            track, "Radiohead", "Creep", uow, user_id="test-user"
        )

        assert isinstance(outcome, NewMapping)
        connector.search_track.assert_called_once()
