"""Characterization tests for ISRC merge sites (FM2a, FM3b).

Pins CURRENT (buggy) behavior: save_track's ISRC-keyed upsert silently
replaces the existing canonical's metadata, and the cross-discovery ISRC
collision path leaves the recording split across canonicals. Flipped by:
ISRC guard at merge sites (v0.8.18 epic 3).

See docs/backlog/identity-resolution-design-space.md §4 (tests 6, 7).
"""

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities import Artist, Track
from src.infrastructure.connectors.lastfm.inward_resolver import LastfmInwardResolver
from src.infrastructure.connectors.spotify.cross_discovery import (
    SpotifyCrossDiscoveryProvider,
)
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBTrack,
    DBTrackMapping,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


class TestSaveTrackIsrcGuard:
    """FLIPPED characterization (FM2a, fixed by ISRC guard at merge sites):
    the original pin recorded save_track's ISRC-keyed upsert silently
    replacing the owner's title/album/duration on a suspect (15s-off)
    collision. Now a suspect collision never claims the contested ISRC —
    the incoming track becomes a distinct canonical with isrc NULL and the
    owner is untouched. Non-suspect (same-duration) merges retain the
    pre-v0.8.18 replace behavior.
    """

    async def test_suspect_isrc_collision_defers_instead_of_clobbering(
        self, db_session: AsyncSession
    ):
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        original = await track_repo.save_track(
            Track(
                id=None,
                title="Gold Rush",
                artists=[Artist(name="Neon Priest")],
                album="Debut",
                duration_ms=200_000,
                isrc="USNP12400001",
            )
        )

        # 15s longer than the original: above the 10s suspect threshold.
        returned = await track_repo.save_track(
            Track(
                id=None,
                title="Gold Rush (2024 Remaster)",
                artists=[Artist(name="Neon Priest")],
                album="Remaster Compilation",
                duration_ms=215_000,
                isrc="USNP12400001",
            )
        )

        # A distinct canonical was created — no merge, no clobber...
        assert returned.id != original.id
        # ...and it does not claim the contested ISRC (NULLs are distinct).
        assert returned.isrc is None

        row = (
            await db_session.execute(
                select(DBTrack.title, DBTrack.album, DBTrack.duration_ms).where(
                    DBTrack.id == original.id
                )
            )
        ).one()
        assert row.title == "Gold Rush"
        assert row.album == "Debut"
        assert row.duration_ms == 200_000

    async def test_non_suspect_isrc_collision_still_merges(
        self, db_session: AsyncSession
    ):
        """Retained behavior: same-duration ISRC collision merges/updates."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        original = await track_repo.save_track(
            Track(
                id=None,
                title="Gold Rush",
                artists=[Artist(name="Neon Priest")],
                duration_ms=200_000,
                isrc="USNP12400001",
            )
        )
        returned = await track_repo.save_track(
            Track(
                id=None,
                title="Gold Rush",
                artists=[Artist(name="Neon Priest")],
                album="Debut (Deluxe)",
                duration_ms=200_500,  # within tolerance
                isrc="USNP12400001",
            )
        )

        assert returned.id == original.id
        assert returned.album == "Debut (Deluxe)"


class TestEnrichmentResaveDuplicatesCanonical:
    """Characterization (FM3b-adjacent, found during v0.8.18 baseline): pins
    CURRENT (buggy) behavior — the Last.fm enrichment re-save rebuilds the
    Track without its version (0 → insert path) and with no identity keys,
    so save_track INSERTS a duplicate canonical instead of updating the
    skeletal one. The skeletal row is left orphaned (no mappings); mappings
    and the resolver result attach to the enriched copy. Flipped by: ISRC
    guard at merge sites (epic 3's reuse-before-create reorder builds the
    canonical once, fully enriched).
    """

    async def test_enrichment_orphans_the_skeletal_canonical(
        self, db_session: AsyncSession
    ):
        uow = get_unit_of_work(db_session)

        info = MagicMock()
        info.lastfm_url = "https://www.last.fm/music/Neon+Priest/_/Orphaned"
        info.lastfm_album_name = "Debut"
        info.lastfm_duration = 100_000
        info.lastfm_mbid = None
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = info

        resolver = LastfmInwardResolver(lastfm_client=lastfm_client)
        result, _ = await resolver.resolve_to_canonical_tracks(
            ["neon priest::orphaned"], uow, user_id="default"
        )
        resolved = result["neon priest::orphaned"]

        rows = (
            await db_session.execute(
                select(DBTrack.id, DBTrack.duration_ms).where(
                    DBTrack.title == "orphaned"
                )
            )
        ).all()
        # One resolution produced TWO canonical rows...
        assert len(rows) == 2
        by_id = {row.id: row.duration_ms for row in rows}
        # ...the enriched copy is the resolver's result...
        assert by_id[resolved.id] == 100_000
        # ...and the skeletal original is orphaned: no metadata, no mappings.
        (skeletal_id,) = set(by_id) - {resolved.id}
        assert by_id[skeletal_id] is None
        skeletal_mappings = (
            await db_session.execute(
                select(DBTrackMapping.id).where(DBTrackMapping.track_id == skeletal_id)
            )
        ).all()
        assert skeletal_mappings == []


class TestCrossDiscoveryDanglingCanonical:
    """Characterization (FM3b): pins CURRENT (buggy) behavior — the Last.fm
    inward flow creates and maps a skeletal canonical BEFORE cross-discovery
    runs; when discovery hits the ISRC-collision path it maps the found
    Spotify ID onto the OTHER canonical owning the ISRC, leaving the
    recording split: lastfm mappings on the skeletal side, the spotify
    mapping on the ISRC owner. Flipped by: ISRC guard at merge sites
    (reuse-before-create: one canonical ends up holding both connectors'
    mappings; no dangling skeletal row).
    """

    async def test_isrc_collision_splits_recording_across_canonicals(
        self, db_session: AsyncSession
    ):
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        # Canonical A owns the ISRC. The "- Live" suffix keeps the resolver's
        # canonical-reuse lookup (which only strips parentheticals) from
        # matching it, so the skeletal path runs.
        track_a = await track_repo.save_track(
            Track(
                id=None,
                title="Creep - Live",
                artists=[Artist(name="Radiohead")],
                isrc="GBAYE9300106",
                duration_ms=238_000,
            )
        )

        # Last.fm getInfo enrichment payload (no MBID).
        info = MagicMock()
        info.lastfm_url = "https://www.last.fm/music/Radiohead/_/Creep"
        info.lastfm_album_name = "Pablo Honey"
        info.lastfm_duration = 238_000
        info.lastfm_mbid = None
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = info

        # Spotify search returns a match carrying A's ISRC.
        artist_mock = MagicMock()
        artist_mock.name = "Radiohead"
        spotify_match = MagicMock()
        spotify_match.id = "sp_creep_b"
        spotify_match.name = "Creep"
        spotify_match.artists = [artist_mock]
        spotify_match.duration_ms = 238_000
        spotify_match.album = MagicMock()
        spotify_match.album.name = "Pablo Honey"
        spotify_match.external_ids = MagicMock(isrc="GBAYE9300106")
        spotify_match.model_dump.return_value = {"id": "sp_creep_b"}
        spotify_connector = AsyncMock()
        spotify_connector.connector_name = "spotify"
        spotify_connector.search_track.return_value = [spotify_match]

        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            cross_discovery=SpotifyCrossDiscoveryProvider(
                spotify_connector=spotify_connector
            ),
        )
        result, _metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow, user_id="default"
        )

        resolved = result["radiohead::creep"]
        # Two canonicals: the resolver's track is not the ISRC owner.
        assert resolved.id != track_a.id

        # The lastfm mappings live on the resolver's canonical...
        lastfm_track_ids = set(
            (
                await db_session.execute(
                    select(DBTrackMapping.track_id).where(
                        DBTrackMapping.connector_name == "lastfm"
                    )
                )
            ).scalars()
        )
        assert lastfm_track_ids == {resolved.id}

        # ...while the spotify mapping went to canonical A (the ISRC owner).
        spotify_mapping_track_id = (
            await db_session.execute(
                select(DBTrackMapping.track_id)
                .join(
                    DBConnectorTrack,
                    DBConnectorTrack.id == DBTrackMapping.connector_track_id,
                )
                .where(DBConnectorTrack.connector_track_identifier == "sp_creep_b")
            )
        ).scalar_one()
        assert spotify_mapping_track_id == track_a.id

        # The split recording: resolver's canonical never received the ISRC.
        resolved_isrc = (
            await db_session.execute(
                select(DBTrack.isrc).where(DBTrack.id == resolved.id)
            )
        ).scalar_one()
        assert resolved_isrc is None
