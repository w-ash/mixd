"""Integration tests for identity resolution pipeline.

Tests the full resolver flow (Mapping Lookup → Canonical Reuse → Track Creation)
with real database operations and mocked API clients, verifying that each identity
resolution strategy (ISRC dedup, parenthetical stripping, MBID upsert, cross-discovery
ISRC collision) correctly resolves tracks in realistic scenarios.
"""

from unittest.mock import AsyncMock, MagicMock

from src.config.constants import MatchMethod
from src.domain.entities import Artist, Track
from src.infrastructure.connectors.lastfm.inward_resolver import LastfmInwardResolver
from src.infrastructure.connectors.spotify.inward_resolver import SpotifyInwardResolver
from src.infrastructure.connectors.spotify.models import (
    SpotifyAlbum,
    SpotifyArtist as SpotifyModelArtist,
    SpotifyExternalIds,
    SpotifyTrack,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


class TestLastfmCanonicalParentheticalReuse:
    """Canonical reuse should reuse existing tracks via title_stripped matching."""

    async def test_finds_existing_track_via_stripped_title(
        self, db_session, test_data_tracker
    ):
        """Last.fm 'artist::song' should match existing 'Song (feat. X)' by Artist."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        # Pre-populate: track with parenthetical (as if imported from Spotify)
        existing = await track_repo.save_track(
            Track(
                id=None,
                title="New Kind of Soft (feat. Neon Priest)",
                artists=[Artist(name="Ultraviolet")],
                duration_ms=220000,
                connector_track_identifiers={"spotify": "sp_123"},
            )
        )
        test_data_tracker.add_track(existing.id)

        # Create connector mapping so Mapping Lookup knows this is a Spotify track
        await uow.get_connector_repository().map_track_to_connector(
            existing, "spotify", "sp_123", MatchMethod.DIRECT_IMPORT, confidence=100
        )

        # Resolve Last.fm identifier — no parenthetical in the ID
        lastfm_client = AsyncMock()
        resolver = LastfmInwardResolver(lastfm_client=lastfm_client)

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["ultraviolet::new kind of soft"], uow
        )

        # Canonical reuse should find the existing track via title_stripped
        assert "ultraviolet::new kind of soft" in result
        assert result["ultraviolet::new kind of soft"].id == existing.id
        assert metrics.reused == 1
        assert metrics.created == 0

        # No API call needed — found via DB lookup
        lastfm_client.get_track_info_comprehensive.assert_not_called()

    async def test_reverse_parenthetical_match(self, db_session, test_data_tracker):
        """Last.fm 'artist::song (feat. x)' should match existing 'Song' by Artist."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        # Pre-populate: bare title (as if imported from Spotify without parenthetical)
        existing = await track_repo.save_track(
            Track(
                id=None,
                title="New Kind of Soft",
                artists=[Artist(name="Ultraviolet")],
                duration_ms=220000,
            )
        )
        test_data_tracker.add_track(existing.id)

        lastfm_client = AsyncMock()
        resolver = LastfmInwardResolver(lastfm_client=lastfm_client)

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["ultraviolet::new kind of soft (feat. neon priest)"], uow
        )

        assert metrics.reused == 1
        assert metrics.created == 0


class TestSpotifyISRCDedup:
    """Spotify resolver should reuse existing tracks by ISRC in Track Creation."""

    async def test_reuses_track_with_same_isrc(self, db_session, test_data_tracker):
        """When Spotify API returns a track with an ISRC already in DB, reuse it."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        # Pre-populate: track with ISRC (e.g. from Last.fm cross-discovery)
        existing = await track_repo.save_track(
            Track(
                id=None,
                title="Creep",
                artists=[Artist(name="Radiohead")],
                isrc="GBAYE9300106",
                duration_ms=238000,
            )
        )
        test_data_tracker.add_track(existing.id)

        # Mock Spotify connector — different Spotify ID but same ISRC
        spotify_connector = AsyncMock()
        spotify_connector.get_tracks_by_ids.return_value = {
            "new_spotify_id_456": SpotifyTrack(
                id="new_spotify_id_456",
                name="Creep",
                artists=[SpotifyModelArtist(id="a1", name="Radiohead")],
                album=SpotifyAlbum(id="al1", name="Pablo Honey"),
                duration_ms=238000,
                external_ids=SpotifyExternalIds(isrc="GBAYE9300106"),
            ),
        }
        spotify_connector.connector_name = "spotify"

        resolver = SpotifyInwardResolver(spotify_connector=spotify_connector)

        # Mapping Lookup finds nothing (no Spotify mapping exists)
        # Track Creation should detect ISRC collision and reuse existing
        result = await resolver._create_tracks_batch(["new_spotify_id_456"], uow)

        assert "new_spotify_id_456" in result
        assert result["new_spotify_id_456"].id == existing.id

        # Verify connector mapping was created with ISRC_MATCH method
        connector_repo = uow.get_connector_repository()
        mappings = await connector_repo.find_tracks_by_connectors(
            [("spotify", "new_spotify_id_456")]
        )
        assert ("spotify", "new_spotify_id_456") in mappings

    async def test_creates_new_track_when_isrc_not_in_db(
        self, db_session, test_data_tracker
    ):
        """When ISRC is not in DB, create a new canonical track normally."""
        uow = get_unit_of_work(db_session)

        spotify_connector = AsyncMock()
        spotify_connector.get_tracks_by_ids.return_value = {
            "sp_new_001": SpotifyTrack(
                id="sp_new_001",
                name="Everything In Its Right Place",
                artists=[SpotifyModelArtist(id="a1", name="Radiohead")],
                album=SpotifyAlbum(id="al1", name="Kid A"),
                duration_ms=250000,
                external_ids=SpotifyExternalIds(isrc="GBAYE0000289"),
            ),
        }
        spotify_connector.connector_name = "spotify"

        resolver = SpotifyInwardResolver(spotify_connector=spotify_connector)
        result = await resolver._create_tracks_batch(["sp_new_001"], uow)

        assert "sp_new_001" in result
        new_track = result["sp_new_001"]
        assert new_track.title == "Everything In Its Right Place"
        test_data_tracker.add_track(new_track.id)


class TestCrossDiscoveryISRCCollision:
    """Cross-discovery should map to existing canonical when ISRC collides."""

    async def test_maps_to_existing_track_on_isrc_collision(
        self, db_session, test_data_tracker
    ):
        """When Spotify search finds a match with an ISRC owned by another
        canonical, map to that canonical instead of enriching the current one."""
        from src.infrastructure.connectors.spotify.cross_discovery import (
            SpotifyCrossDiscoveryProvider,
        )

        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        # Track A: existing canonical with ISRC (from Spotify import)
        track_a = await track_repo.save_track(
            Track(
                id=None,
                title="Creep",
                artists=[Artist(name="Radiohead")],
                isrc="GBAYE9300106",
                duration_ms=238000,
            )
        )
        test_data_tracker.add_track(track_a.id)

        # Track B: new Last.fm skeletal track (no ISRC yet)
        track_b = await track_repo.save_track(
            Track(
                id=None,
                title="Creep",
                artists=[Artist(name="Radiohead")],
            )
        )
        test_data_tracker.add_track(track_b.id)

        # Mock Spotify search returning a match with Track A's ISRC
        artist_mock = MagicMock()
        artist_mock.name = "Radiohead"
        spotify_match = MagicMock()
        spotify_match.id = "sp_different_release"
        spotify_match.name = "Creep"
        spotify_match.artists = [artist_mock]
        spotify_match.duration_ms = 238000
        spotify_match.album = MagicMock()
        spotify_match.album.name = "Pablo Honey"
        spotify_match.external_ids = MagicMock(isrc="GBAYE9300106")
        spotify_match.model_dump.return_value = {"id": "sp_different_release"}

        connector = AsyncMock()
        connector.search_track.return_value = [spotify_match]
        connector.connector_name = "spotify"

        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        result = await provider.attempt_discovery(
            track_b, "Radiohead", "Creep", uow
        )

        assert result is True

        # Verify the mapping was created on Track A (ISRC owner), NOT Track B
        mappings = await uow.get_connector_repository().find_tracks_by_connectors(
            [("spotify", "sp_different_release")]
        )
        assert ("spotify", "sp_different_release") in mappings
        mapped_track = mappings["spotify", "sp_different_release"]
        assert mapped_track.id == track_a.id


class TestMBIDUpsertMerge:
    """MBID-based upsert should merge tracks with the same MusicBrainz ID."""

    async def test_same_mbid_upserts_not_duplicates(
        self, db_session, test_data_tracker
    ):
        """Saving two tracks with the same MBID should upsert to one row."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        mbid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

        track1 = await track_repo.save_track(
            Track(
                id=None,
                title="Creep",
                artists=[Artist(name="Radiohead")],
                connector_track_identifiers={"musicbrainz": mbid},
            )
        )
        test_data_tracker.add_track(track1.id)

        # Second save with same MBID but enriched metadata
        track2 = await track_repo.save_track(
            Track(
                id=None,
                title="Creep",
                artists=[Artist(name="Radiohead")],
                album="Pablo Honey",
                duration_ms=238000,
                connector_track_identifiers={"musicbrainz": mbid},
            )
        )

        # Should be the same track (upserted)
        assert track1.id == track2.id
        assert track2.album == "Pablo Honey"
        assert track2.duration_ms == 238000
