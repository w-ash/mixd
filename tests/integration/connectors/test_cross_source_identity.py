"""Integration tests for cross-source identity resolution.

Simulates the real dual-import scenario: import tracks from Service A,
then resolve the same tracks from Service B, and verify they map to the
same canonical tracks. Uses real database + mocked API clients.
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


def _make_spotify_track(
    spotify_id: str,
    name: str,
    artist: str,
    isrc: str | None = None,
    duration_ms: int = 200000,
    album: str = "Album",
) -> SpotifyTrack:
    """Factory for SpotifyTrack model instances used in mock API responses."""
    return SpotifyTrack(
        id=spotify_id,
        name=name,
        artists=[SpotifyModelArtist(id=f"art_{spotify_id}", name=artist)],
        album=SpotifyAlbum(id=f"alb_{spotify_id}", name=album),
        duration_ms=duration_ms,
        external_ids=SpotifyExternalIds(isrc=isrc),
    )


class TestSpotifyThenLastfm:
    """Import Spotify tracks first, then resolve Last.fm identifiers."""

    async def test_lastfm_reuses_spotify_canonicals_via_title_artist(
        self, db_session, test_data_tracker
    ):
        """Last.fm resolution should find Spotify-created tracks via Phase 1.5."""
        uow = get_unit_of_work(db_session)

        # Step 1: Import 3 tracks via Spotify resolver
        spotify_connector = AsyncMock()
        spotify_connector.get_tracks_by_ids.return_value = {
            "sp_001": _make_spotify_track("sp_001", "Creep", "Radiohead", "GBAYE9300106"),
            "sp_002": _make_spotify_track(
                "sp_002", "Song (feat. X)", "Artist", "USRC17000001"
            ),
            "sp_003": _make_spotify_track("sp_003", "Unique Song", "Band", "USRC17000002"),
        }
        spotify_connector.connector_name = "spotify"

        spotify_resolver = SpotifyInwardResolver(spotify_connector=spotify_connector)
        spotify_result, spotify_metrics = await spotify_resolver.resolve_to_canonical_tracks(
            ["sp_001", "sp_002", "sp_003"], uow
        )

        assert spotify_metrics.created == 3
        for track in spotify_result.values():
            test_data_tracker.add_track(track.id)

        spotify_creep_id = spotify_result["sp_001"].id
        spotify_song_id = spotify_result["sp_002"].id

        # Step 2: Resolve Last.fm identifiers for the same tracks
        lastfm_client = AsyncMock()
        # Track.getInfo won't be called for reused tracks (Phase 1.5 short-circuits)
        lastfm_client.get_track_info_comprehensive.return_value = MagicMock(
            lastfm_url="https://www.last.fm/music/Band/_/Different+Song",
            lastfm_duration=180000,
            lastfm_album_name=None,
        )

        lastfm_resolver = LastfmInwardResolver(lastfm_client=lastfm_client)
        lastfm_result, lastfm_metrics = await lastfm_resolver.resolve_to_canonical_tracks(
            [
                "radiohead::creep",  # Should match sp_001 via title+artist
                "artist::song",  # Should match sp_002 via parenthetical stripping
                "band::different song",  # No match — genuinely different
            ],
            uow,
        )

        # 2 tracks reused from Spotify, 1 new
        assert lastfm_metrics.reused == 2
        assert lastfm_metrics.created == 1

        # Verify correct canonical reuse
        assert lastfm_result["radiohead::creep"].id == spotify_creep_id
        assert lastfm_result["artist::song"].id == spotify_song_id

        # The new track should have a different ID
        assert lastfm_result["band::different song"].id not in (
            spotify_creep_id,
            spotify_song_id,
        )
        test_data_tracker.add_track(lastfm_result["band::different song"].id)


class TestLastfmThenSpotify:
    """Import Last.fm tracks first, then resolve Spotify tracks via ISRC dedup."""

    async def test_spotify_reuses_lastfm_canonicals_via_isrc(
        self, db_session, test_data_tracker
    ):
        """When Last.fm tracks have ISRCs (from cross-discovery enrichment),
        Spotify resolver should find them via ISRC dedup."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        # Step 1: Simulate Last.fm import — create tracks with ISRCs
        # (In reality, ISRCs come from cross-discovery enrichment, but
        # for this test we pre-populate them directly)
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
        await uow.get_connector_repository().map_track_to_connector(
            track_a, "lastfm", "radiohead::creep", "lastfm_import", confidence=85
        )

        track_b = await track_repo.save_track(
            Track(
                id=None,
                title="Everything In Its Right Place",
                artists=[Artist(name="Radiohead")],
                isrc="GBAYE0000289",
                duration_ms=250000,
            )
        )
        test_data_tracker.add_track(track_b.id)
        await uow.get_connector_repository().map_track_to_connector(
            track_b, "lastfm", "radiohead::everything in its right place",
            "lastfm_import", confidence=85,
        )

        # Step 2: Spotify resolver encounters different Spotify IDs but same ISRCs
        spotify_connector = AsyncMock()
        spotify_connector.get_tracks_by_ids.return_value = {
            "sp_new_creep": _make_spotify_track(
                "sp_new_creep", "Creep", "Radiohead", "GBAYE9300106"
            ),
            "sp_new_eiirp": _make_spotify_track(
                "sp_new_eiirp", "Everything In Its Right Place", "Radiohead",
                "GBAYE0000289",
            ),
        }
        spotify_connector.connector_name = "spotify"

        spotify_resolver = SpotifyInwardResolver(spotify_connector=spotify_connector)

        # Phase 1: no existing Spotify mappings
        # Phase 2: ISRC dedup should catch both
        result, metrics = await spotify_resolver.resolve_to_canonical_tracks(
            ["sp_new_creep", "sp_new_eiirp"], uow
        )

        # Both should reuse existing Last.fm canonicals (not create duplicates)
        assert result["sp_new_creep"].id == track_a.id
        assert result["sp_new_eiirp"].id == track_b.id

        # Verify Spotify mappings were created on the existing tracks
        spotify_mappings = await uow.get_connector_repository().find_tracks_by_connectors(
            [("spotify", "sp_new_creep"), ("spotify", "sp_new_eiirp")]
        )
        assert len(spotify_mappings) == 2

    async def test_spotify_creates_new_when_no_isrc_overlap(
        self, db_session, test_data_tracker
    ):
        """When Spotify tracks have ISRCs not in DB, create new canonicals."""
        uow = get_unit_of_work(db_session)

        spotify_connector = AsyncMock()
        spotify_connector.get_tracks_by_ids.return_value = {
            "sp_brand_new": _make_spotify_track(
                "sp_brand_new", "Never Heard Before", "New Artist", "XXXX00000001"
            ),
        }
        spotify_connector.connector_name = "spotify"

        spotify_resolver = SpotifyInwardResolver(spotify_connector=spotify_connector)
        result, metrics = await spotify_resolver.resolve_to_canonical_tracks(
            ["sp_brand_new"], uow
        )

        assert metrics.created == 1
        assert result["sp_brand_new"].title == "Never Heard Before"
        test_data_tracker.add_track(result["sp_brand_new"].id)


class TestMixedResolutionPaths:
    """Multiple resolution paths active simultaneously."""

    async def test_mixed_existing_reused_created(self, db_session, test_data_tracker):
        """Phase 1, Phase 1.5, and Phase 2 all resolve different IDs in one call."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()
        connector_repo = uow.get_connector_repository()

        # Track A: has existing Last.fm connector mapping (Phase 1 hit)
        track_a = await track_repo.save_track(
            Track(
                id=None,
                title="Already Mapped",
                artists=[Artist(name="Band A")],
            )
        )
        test_data_tracker.add_track(track_a.id)
        await connector_repo.map_track_to_connector(
            track_a, "lastfm", "band a::already mapped",
            "lastfm_import", confidence=85,
        )

        # Track B: exists in DB (from Spotify) but no Last.fm mapping (Phase 1.5 hit)
        track_b = await track_repo.save_track(
            Track(
                id=None,
                title="Needs Reuse",
                artists=[Artist(name="Band B")],
                duration_ms=200000,
            )
        )
        test_data_tracker.add_track(track_b.id)

        # Resolve all three at once
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = MagicMock(
            lastfm_url="https://www.last.fm/music/Band+C/_/Brand+New+Track",
            lastfm_duration=180000,
            lastfm_album_name=None,
        )

        resolver = LastfmInwardResolver(lastfm_client=lastfm_client)
        result, metrics = await resolver.resolve_to_canonical_tracks(
            [
                "band a::already mapped",  # Phase 1: existing mapping
                "band b::needs reuse",  # Phase 1.5: title+artist match
                "band c::brand new track",  # Phase 2: create new
            ],
            uow,
        )

        assert len(result) == 3
        assert metrics.existing == 1
        assert metrics.reused == 1
        assert metrics.created == 1

        # Verify correct tracks
        assert result["band a::already mapped"].id == track_a.id
        assert result["band b::needs reuse"].id == track_b.id
        assert result["band c::brand new track"].id not in (track_a.id, track_b.id)
        test_data_tracker.add_track(result["band c::brand new track"].id)
