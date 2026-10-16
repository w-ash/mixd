"""Integration tests for identity resolution pipeline.

Tests the full resolver flow (Mapping Lookup → Canonical Reuse → Track Creation)
with real database operations and mocked API clients, verifying that each identity
resolution strategy (parenthetical stripping, cross-discovery ISRC collision)
correctly resolves tracks in realistic scenarios.
"""

from unittest.mock import AsyncMock, MagicMock

from src.domain.entities import ArtistCredit, Track
from src.infrastructure.connectors.lastfm.inward_resolver import LastfmInwardResolver
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import TEST_USER_ID, discover_one


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
                artists=[ArtistCredit(credited_name="Ultraviolet")],
                duration_ms=220000,
                connector_track_identifiers={"spotify": "sp_123"},
                user_id=TEST_USER_ID,
            )
        )
        test_data_tracker.add_track(existing.id)

        # Create connector mapping so Mapping Lookup knows this is a Spotify track
        await uow.get_connector_repository().map_track_to_connector(
            existing, "spotify", "sp_123", "direct_import", confidence=100
        )

        # Resolve Last.fm identifier — no parenthetical in the ID
        lastfm_client = AsyncMock()
        resolver = LastfmInwardResolver(lastfm_client=lastfm_client)

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["ultraviolet::new kind of soft"], uow, user_id="default"
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
                artists=[ArtistCredit(credited_name="Ultraviolet")],
                duration_ms=220000,
                user_id=TEST_USER_ID,
            )
        )
        test_data_tracker.add_track(existing.id)

        lastfm_client = AsyncMock()
        resolver = LastfmInwardResolver(lastfm_client=lastfm_client)

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["ultraviolet::new kind of soft (feat. neon priest)"],
            uow,
            user_id="default",
        )

        assert result["ultraviolet::new kind of soft (feat. neon priest)"].id == (
            existing.id
        )
        assert metrics.reused == 1
        assert metrics.created == 0


class TestCrossDiscoveryISRCCollision:
    """Cross-discovery should decide to reuse the ISRC owner when it collides."""

    async def test_returns_reuse_of_isrc_owner_on_collision(
        self, db_session, test_data_tracker
    ):
        """When Spotify search finds a match whose ISRC is owned by another
        canonical (non-suspect), discover() returns a ReuseExisting decision
        pointing at that owner, carrying the Spotify mapping to create on it —
        rather than minting a mapping onto the probe. The resolver is what
        applies the decision (covered end-to-end in the characterization tests)."""
        from src.domain.matching.protocols import ReuseExisting
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
                artists=[ArtistCredit(credited_name="Radiohead")],
                isrc="GBAYE9300106",
                duration_ms=238000,
                user_id=TEST_USER_ID,
            )
        )
        test_data_tracker.add_track(track_a.id)

        # Probe: unsaved in-memory Last.fm canonical (no ISRC yet), same duration
        # as Track A so the collision is non-suspect.
        probe = Track(
            id=None,
            title="Creep",
            artists=[ArtistCredit(credited_name="Radiohead")],
            duration_ms=238000,
            user_id=TEST_USER_ID,
        )

        # Mock Spotify search returning a match with Track A's ISRC
        artist_mock = MagicMock()
        artist_mock.name = "Radiohead"
        artist_mock.id = "sp-radiohead"
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
        outcome = await discover_one(
            provider, probe, "Radiohead", "Creep", uow, user_id="default"
        )

        # Reuse the ISRC owner (Track A), with the found Spotify id to map onto it.
        assert isinstance(outcome, ReuseExisting)
        assert outcome.track.id == track_a.id
        assert outcome.spotify_id == "sp_different_release"
        assert outcome.match_method == "isrc_match"
