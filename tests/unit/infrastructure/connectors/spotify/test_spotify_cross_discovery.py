"""Tests for SpotifyCrossDiscoveryProvider.

Validates that cross-discovery searches Spotify, evaluates match quality via
the domain service, and returns the right ``DiscoveryOutcome`` decision
(ReuseExisting / NewMapping / Nothing) for its caller to apply — through the
single-call ``discover`` contract and the batched ``discover_batch`` pass
(concurrent pure-API probes, one ISRC prefetch per chunk, per-request
decisions). The provider itself does NOT mutate the caller's canonicals — it
only reports decisions (and performs its own side effects, e.g. queuing an
ISRC review).
"""

import asyncio
from collections.abc import Callable, Coroutine
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

from src.config import settings
from src.config.constants import MatchMethod, SpotifyConstants
from src.domain.matching.protocols import (
    DiscoveryRequest,
    NewMapping,
    Nothing,
    ReuseExisting,
)
from src.infrastructure.connectors.spotify.cross_discovery import (
    SpotifyCrossDiscoveryProvider,
)
from tests.fixtures import discover_one, make_track
from tests.fixtures.mocks import make_mock_uow


def _make_uow() -> MagicMock:
    uow = make_mock_uow()
    track_repo = uow.get_track_repository()
    track_repo.find_tracks_by_isrcs.return_value = {}
    return uow


def _spotify_track_mock(
    spotify_id: str,
    name: str,
    artist: str,
    *,
    isrc: str | None = None,
    duration_ms: int = 200000,
) -> MagicMock:
    """A validated-SpotifyTrack stand-in shaped like the search results."""
    artist_mock = MagicMock()
    artist_mock.name = artist
    match = MagicMock()
    match.id = spotify_id
    match.name = name
    match.artists = [artist_mock]
    match.duration_ms = duration_ms
    match.album = MagicMock()
    match.album.name = "Album"
    match.external_ids = MagicMock(isrc=isrc)
    match.model_dump.return_value = {"id": spotify_id, "name": name}
    return match


def _search_router(
    matches_by_artist: dict[str, list[MagicMock]],
) -> Callable[[str, int], Coroutine[None, None, list[MagicMock]]]:
    """Route each search query to its artist's canned candidates."""

    async def _search(query: str, limit: int) -> list[MagicMock]:
        for artist, matches in matches_by_artist.items():
            if f'artist:"{artist}"' in query:
                return matches
        return []

    return _search


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

        outcome = await discover_one(
            provider, track, "Radiohead", "Creep", uow, user_id="test-user"
        )

        assert isinstance(outcome, NewMapping)
        assert outcome.spotify_id == "spotify123"
        assert outcome.match_method == MatchMethod.LASTFM_DISCOVERY
        # ISRC carried for backfill (normalized).
        assert outcome.isrc == "GBAYE9300106"
        connector.search_track.assert_called_once_with(
            'artist:"Radiohead" track:"Creep"', SpotifyConstants.SEARCH_DEFAULT_LIMIT
        )


class TestNoResults:
    """Empty search results should return Nothing."""

    async def test_returns_nothing_when_no_candidates(self):
        connector = AsyncMock()
        connector.search_track.return_value = []

        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        track = make_track(id=42)
        uow = _make_uow()

        outcome = await discover_one(
            provider, track, "Unknown", "Song", uow, user_id="test-user"
        )

        assert isinstance(outcome, Nothing)

    async def test_a_miss_costs_exactly_one_search(self):
        """Discovery searches once per unmatched play; widening would double a
        30k-miss run's /search volume against the shared limiter for calls that
        still return nothing."""
        connector = AsyncMock()
        connector.search_track.return_value = []

        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        uow = _make_uow()

        outcome = await discover_one(
            provider, make_track(id=42), "Unknown", "Song", uow, user_id="test-user"
        )

        assert isinstance(outcome, Nothing)
        assert connector.search_track.await_count == 1


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

        outcome = await discover_one(
            provider, track, "Radiohead", "Creep", uow, user_id="test-user"
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

        outcome = await discover_one(
            provider, track, "Radiohead", "Creep", uow, user_id="test-user"
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

        outcome = await discover_one(
            provider, track, "Same Artist", "Same Song", uow, user_id="test-user"
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

        outcome = await discover_one(
            provider, track, "Neon Priest", "Gold Rush", uow, user_id="test-user"
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

        outcome = await discover_one(
            provider, track, "Radiohead", "Creep", uow, user_id="test-user"
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
        lb_lookup.spotify_ids_from_metadata.return_value = {
            ("Artist", "Album", "Song"): "existing_spotify_id"
        }

        connector = AsyncMock()

        provider = SpotifyCrossDiscoveryProvider(
            spotify_connector=connector,
            listenbrainz_lookup=lb_lookup,
        )
        track = make_track(id=42, title="Song", artist="Artist", album="Album")
        uow = _make_uow()

        # ListenBrainz-returned ID already has a canonical
        connector_repo = uow.get_connector_repository()
        connector_repo.find_tracks_by_connectors.return_value = {
            ("spotify", "existing_spotify_id"): existing_track,
        }

        outcome = await discover_one(
            provider, track, "Artist", "Song", uow, user_id="test-user"
        )

        # The lookup got the full (artist, release, track) triple.
        lb_lookup.spotify_ids_from_metadata.assert_awaited_once_with([
            ("Artist", "Album", "Song")
        ])
        assert isinstance(outcome, ReuseExisting)
        assert outcome.track.id == 99
        # The reused canonical already carries the Spotify mapping.
        assert outcome.spotify_id is None
        # Should NOT have searched Spotify
        connector.search_track.assert_not_called()

    async def test_listenbrainz_miss_falls_back_to_search(self):
        """When ListenBrainz returns no hit, Spotify search is used."""
        lb_lookup = AsyncMock()
        lb_lookup.spotify_ids_from_metadata.return_value = {}

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
        track = make_track(id=42, title="Song", artist="Artist", album="Album")
        uow = _make_uow()

        outcome = await discover_one(
            provider, track, "Artist", "Song", uow, user_id="test-user"
        )

        assert isinstance(outcome, NewMapping)
        connector.search_track.assert_called_once()

    async def test_no_album_skips_listenbrainz_and_still_searches(self):
        """The Labs endpoint requires a release name, so a probe without an
        album skips the ListenBrainz arm — the search ladder still runs."""
        lb_lookup = AsyncMock()

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
        track = make_track(id=42, title="Song", artist="Artist")  # no album
        uow = _make_uow()

        outcome = await discover_one(
            provider, track, "Artist", "Song", uow, user_id="test-user"
        )

        lb_lookup.spotify_ids_from_metadata.assert_not_awaited()
        assert isinstance(outcome, NewMapping)
        connector.search_track.assert_called_once()

    async def test_lookup_failure_degrades_to_search(self):
        """A ListenBrainz failure is a 'no LB hit', never a failed request —
        the search ladder still decides the batch."""
        lb_lookup = AsyncMock()
        lb_lookup.spotify_ids_from_metadata.side_effect = RuntimeError("LB down")

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
        track = make_track(id=42, title="Song", artist="Artist", album="Album")
        uow = _make_uow()

        outcome = await discover_one(
            provider, track, "Artist", "Song", uow, user_id="test-user"
        )

        assert isinstance(outcome, NewMapping)
        connector.search_track.assert_called_once()

    async def test_canonical_prefetch_failure_degrades_to_search(self):
        """A failed canonical read for ListenBrainz hits degrades those
        requests to the search ladder rather than failing them."""
        lb_lookup = AsyncMock()
        lb_lookup.spotify_ids_from_metadata.return_value = {
            ("Artist", "Album", "Song"): "lb_spotify_id"
        }

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
        track = make_track(id=42, title="Song", artist="Artist", album="Album")
        uow = _make_uow()
        connector_repo = uow.get_connector_repository()
        connector_repo.find_tracks_by_connectors.side_effect = RuntimeError("DB down")

        outcome = await discover_one(
            provider, track, "Artist", "Song", uow, user_id="test-user"
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

        outcome = await discover_one(
            provider, track, "Radiohead", "Creep", uow, user_id="test-user"
        )

        assert isinstance(outcome, NewMapping)
        connector.search_track.assert_called_once()


class TestDiscoverBatch:
    """The batched pass: I/O restructured, per-request decisions unchanged."""

    async def test_isrc_prefetch_is_one_call_with_all_collected_isrcs(self):
        """Every candidate ISRC in the chunk reaches ONE find_tracks_by_isrcs
        call — not one single-ISRC query per request."""
        connector = AsyncMock()
        connector.connector_name = "spotify"
        connector.search_track.side_effect = _search_router({
            "Radiohead": [
                _spotify_track_mock("sp1", "Creep", "Radiohead", isrc="GBAYE9300106")
            ],
            "Muse": [_spotify_track_mock("sp2", "Bliss", "Muse", isrc="GBAHT0100060")],
            "Elbow": [_spotify_track_mock("sp3", "Newborn", "Elbow")],
        })

        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        uow = _make_uow()
        requests = [
            DiscoveryRequest(
                make_track(id=1, title="Creep", artist="Radiohead"),
                "Radiohead",
                "Creep",
            ),
            DiscoveryRequest(
                make_track(id=2, title="Bliss", artist="Muse"), "Muse", "Bliss"
            ),
            DiscoveryRequest(
                make_track(id=3, title="Newborn", artist="Elbow"), "Elbow", "Newborn"
            ),
        ]

        outcomes = await provider.discover_batch(requests, uow, user_id="test-user")

        track_repo = uow.get_track_repository()
        track_repo.find_tracks_by_isrcs.assert_awaited_once()
        prefetch_call = track_repo.find_tracks_by_isrcs.await_args
        assert sorted(prefetch_call.args[0]) == ["GBAHT0100060", "GBAYE9300106"]
        assert prefetch_call.kwargs["user_id"] == "test-user"
        # No collisions: every request decides into its own NewMapping.
        assert [type(outcome) for outcome in outcomes] == [NewMapping] * 3

    async def test_outcomes_return_in_request_order(self):
        """Each request's decision matches its sequential-discover outcome and
        lands at its own input position."""
        existing = make_track(id=99, title="Bliss", artist="Muse", duration_ms=200000)

        connector = AsyncMock()
        connector.connector_name = "spotify"
        connector.search_track.side_effect = _search_router({
            "Radiohead": [_spotify_track_mock("sp1", "Creep", "Radiohead")],
            "Muse": [
                _spotify_track_mock(
                    "sp2", "Bliss", "Muse", isrc="GBAHT0100060", duration_ms=200000
                )
            ],
        })

        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        uow = _make_uow()
        # The Muse candidate's ISRC already has an owner (non-suspect: same
        # duration) → ReuseExisting; the unknown artist finds nothing.
        uow.get_track_repository().find_tracks_by_isrcs.return_value = {
            "GBAHT0100060": existing
        }
        requests = [
            DiscoveryRequest(
                make_track(id=1, title="Creep", artist="Radiohead"),
                "Radiohead",
                "Creep",
            ),
            DiscoveryRequest(
                make_track(id=2, title="Bliss", artist="Muse"), "Muse", "Bliss"
            ),
            DiscoveryRequest(
                make_track(id=3, title="Ghost", artist="Unknown"), "Unknown", "Ghost"
            ),
        ]

        outcomes = await provider.discover_batch(requests, uow, user_id="test-user")

        assert isinstance(outcomes[0], NewMapping)
        assert outcomes[0].spotify_id == "sp1"
        assert isinstance(outcomes[1], ReuseExisting)
        assert outcomes[1].track.id == 99
        assert outcomes[1].spotify_id == "sp2"
        assert isinstance(outcomes[2], Nothing)

    async def test_one_failed_search_degrades_only_its_request(self):
        """A search exception fails its own request into Nothing without
        cancelling the batch's sibling probes."""

        async def _search(query: str, limit: int) -> list[MagicMock]:
            if 'artist:"Bad"' in query:
                raise RuntimeError("API down")
            return [_spotify_track_mock("sp_good", "Good Song", "Good")]

        connector = AsyncMock()
        connector.connector_name = "spotify"
        connector.search_track.side_effect = _search

        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        uow = _make_uow()
        requests = [
            DiscoveryRequest(
                make_track(id=1, title="Bad Song", artist="Bad"), "Bad", "Bad Song"
            ),
            DiscoveryRequest(
                make_track(id=2, title="Good Song", artist="Good"), "Good", "Good Song"
            ),
        ]

        outcomes = await provider.discover_batch(requests, uow, user_id="test-user")

        assert isinstance(outcomes[0], Nothing)
        assert isinstance(outcomes[1], NewMapping)
        assert outcomes[1].spotify_id == "sp_good"

    async def test_listenbrainz_lookup_is_one_batched_call(self):
        """The chunk's album-carrying requests resolve through ONE
        spotify_ids_from_metadata call of (artist, release, track) triples;
        an album-less request never reaches it. A hit reuses its canonical
        without searching, a miss (or skip) falls back to search."""
        existing = make_track(id=99, title="Song", artist="Artist")

        lb_lookup = AsyncMock()
        lb_lookup.spotify_ids_from_metadata.return_value = {
            ("Artist", "Album A", "Song"): "existing_spotify_id"
        }

        connector = AsyncMock()
        connector.connector_name = "spotify"
        connector.search_track.side_effect = _search_router({
            "Other": [_spotify_track_mock("sp_other", "Tune", "Other")],
            "Bare": [_spotify_track_mock("sp_bare", "Solo", "Bare")],
        })

        provider = SpotifyCrossDiscoveryProvider(
            spotify_connector=connector,
            listenbrainz_lookup=lb_lookup,
        )
        uow = _make_uow()
        uow.get_connector_repository().find_tracks_by_connectors.return_value = {
            ("spotify", "existing_spotify_id"): existing
        }
        requests = [
            DiscoveryRequest(
                make_track(id=1, title="Song", artist="Artist", album="Album A"),
                "Artist",
                "Song",
            ),
            DiscoveryRequest(
                make_track(id=2, title="Tune", artist="Other", album="Album B"),
                "Other",
                "Tune",
            ),
            DiscoveryRequest(
                make_track(id=3, title="Solo", artist="Bare"), "Bare", "Solo"
            ),
        ]

        outcomes = await provider.discover_batch(requests, uow, user_id="test-user")

        lb_lookup.spotify_ids_from_metadata.assert_awaited_once_with([
            ("Artist", "Album A", "Song"),
            ("Other", "Album B", "Tune"),
        ])
        assert isinstance(outcomes[0], ReuseExisting)
        assert outcomes[0].track.id == 99
        assert isinstance(outcomes[1], NewMapping)
        assert isinstance(outcomes[2], NewMapping)
        # The LB miss and the album-less skip searched Spotify; the hit didn't.
        assert connector.search_track.await_count == 2

    async def test_repeat_triples_collapse_to_one_lookup_row(self):
        """Fifty repeat plays of one track cost one lookup row, not fifty —
        and every request still resolves from the shared answer."""
        existing = make_track(id=99, title="Song", artist="Artist")

        lb_lookup = AsyncMock()
        lb_lookup.spotify_ids_from_metadata.return_value = {
            ("Artist", "Album", "Song"): "existing_spotify_id"
        }

        connector = AsyncMock()
        connector.connector_name = "spotify"

        provider = SpotifyCrossDiscoveryProvider(
            spotify_connector=connector,
            listenbrainz_lookup=lb_lookup,
        )
        uow = _make_uow()
        uow.get_connector_repository().find_tracks_by_connectors.return_value = {
            ("spotify", "existing_spotify_id"): existing
        }
        probe = make_track(id=1, title="Song", artist="Artist", album="Album")
        requests = [DiscoveryRequest(probe, "Artist", "Song") for _ in range(3)]

        outcomes = await provider.discover_batch(requests, uow, user_id="test-user")

        lb_lookup.spotify_ids_from_metadata.assert_awaited_once_with([
            ("Artist", "Album", "Song")
        ])
        assert all(isinstance(outcome, ReuseExisting) for outcome in outcomes)
        connector.search_track.assert_not_called()

    async def test_searches_fan_out_concurrently_under_the_limit(self, monkeypatch):
        monkeypatch.setattr(settings.api.spotify, "concurrency", 2)

        inflight = 0
        max_inflight = 0

        async def _slow_search(query: str, limit: int) -> list[MagicMock]:
            nonlocal inflight, max_inflight
            inflight += 1
            max_inflight = max(max_inflight, inflight)
            await asyncio.sleep(0.01)
            inflight -= 1
            return []

        connector = AsyncMock()
        connector.connector_name = "spotify"
        connector.search_track.side_effect = _slow_search

        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        uow = _make_uow()
        requests = [
            DiscoveryRequest(
                make_track(id=n, title=f"T{n}", artist=f"A{n}"), f"A{n}", f"T{n}"
            )
            for n in range(6)
        ]

        outcomes = await provider.discover_batch(requests, uow, user_id="test-user")

        assert [type(outcome) for outcome in outcomes] == [Nothing] * 6
        assert connector.search_track.await_count == 6
        assert max_inflight <= 2
        # It actually ran concurrently — a sequential loop would peak at 1.
        assert max_inflight == 2


class TestSavepointPlacement:
    """Savepoints guard writes only — pure decisions never open one."""

    async def test_pure_decisions_open_no_savepoint(self):
        """A no-collision chunk opens exactly one savepoint (the ISRC
        prefetch); deciding N requests from prefetched state adds none."""
        connector = AsyncMock()
        connector.connector_name = "spotify"
        connector.search_track.side_effect = _search_router({
            "Radiohead": [
                _spotify_track_mock("sp1", "Creep", "Radiohead", isrc="GBAYE9300106")
            ],
            "Muse": [_spotify_track_mock("sp2", "Bliss", "Muse", isrc="GBAHT0100060")],
        })

        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        uow = _make_uow()
        requests = [
            DiscoveryRequest(
                make_track(id=1, title="Creep", artist="Radiohead"),
                "Radiohead",
                "Creep",
            ),
            DiscoveryRequest(
                make_track(id=2, title="Bliss", artist="Muse"), "Muse", "Bliss"
            ),
        ]

        outcomes = await provider.discover_batch(requests, uow, user_id="test-user")

        assert [type(outcome) for outcome in outcomes] == [NewMapping] * 2
        assert uow.savepoint.call_count == 1

    async def test_suspect_review_write_runs_inside_a_savepoint(self):
        """The chunk's only write — queuing the suspect review — is
        savepoint-wrapped, so a SQL failure rolls back the review alone."""
        existing_track = make_track(
            id=99, title="Gold Rush", artist="Neon Priest", duration_ms=200000
        )
        connector = AsyncMock()
        connector.connector_name = "spotify"
        connector.search_track.return_value = [
            _spotify_track_mock(
                "sp_remaster",
                "Gold Rush",
                "Neon Priest",
                isrc="USNP12400001",
                duration_ms=220000,  # 20s off — above the suspect threshold
            )
        ]

        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        uow = _make_uow()
        uow.get_track_repository().find_tracks_by_isrcs.return_value = {
            "USNP12400001": existing_track
        }

        depth = 0

        @asynccontextmanager
        async def _tracking_savepoint():
            nonlocal depth
            depth += 1
            try:
                yield
            finally:
                depth -= 1

        uow.savepoint = MagicMock(side_effect=_tracking_savepoint)

        queue_depths: list[int] = []

        async def _queue(*args: object, **kwargs: object) -> MagicMock:
            queue_depths.append(depth)
            return MagicMock()

        connector_repo = uow.get_connector_repository()
        connector_repo.queue_isrc_collision_review.side_effect = _queue

        outcome = await discover_one(
            provider,
            make_track(id=42, title="Gold Rush", artist="Neon Priest"),
            "Neon Priest",
            "Gold Rush",
            uow,
            user_id="test-user",
        )

        assert isinstance(outcome, NewMapping)
        assert outcome.isrc is None
        # The review write ran inside an open savepoint.
        assert queue_depths == [1]
