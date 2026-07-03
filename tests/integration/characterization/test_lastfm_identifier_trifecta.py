"""Characterization test for Last.fm identifier fragmentation (FM3a).

Pins CURRENT (buggy) behavior: three code paths mint Last.fm connector
identifiers with three different schemes, so one recording materializes as
four distinct connector_tracks rows. Flipped by: Last.fm identifier
unification (v0.8.18 epic 4 — every site mints the normalized
make_lastfm_identifier key; one row per recording). The cross-discovery
mint site changes earlier, in epic 3's rewrite — that commit updates this
test's expected set before epic 4 collapses it to one row.

See docs/backlog/identity-resolution-design-space.md §4 (test 5).
"""

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.constants import MatchMethod
from src.domain.entities import Artist, Track
from src.infrastructure.connectors.lastfm.conversions import (
    convert_lastfm_track_to_connector,
)
from src.infrastructure.connectors.lastfm.inward_resolver import LastfmInwardResolver
from src.infrastructure.connectors.lastfm.models import LastFMTrackData
from src.infrastructure.connectors.spotify.cross_discovery import (
    SpotifyCrossDiscoveryProvider,
)
from src.infrastructure.persistence.database.db_models import DBConnectorTrack
from src.infrastructure.persistence.repositories.factories import get_unit_of_work

_URL = "https://www.last.fm/music/Neon+Priest/_/Gold+Rush"
_MBID = "0198a4b6-1111-7222-8333-444455556666"


class TestThreeMintSchemesFragmentOneTrack:
    """Characterization (FM3a): one recording, three mint paths, four rows."""

    async def test_one_recording_materializes_as_four_rows(
        self, db_session: AsyncSession
    ):
        uow = get_unit_of_work(db_session)

        # Path (b) FIRST — the inward resolver, before any canonical exists
        # (otherwise canonical reuse short-circuits the mint). Mints the URL
        # row (primary) plus the lowercased composite row (secondary).
        info = MagicMock()
        info.lastfm_url = _URL
        info.lastfm_album_name = "Debut"
        info.lastfm_duration = 200_000
        info.lastfm_mbid = None
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = info
        resolver = LastfmInwardResolver(lastfm_client=lastfm_client)
        result, _ = await resolver.resolve_to_canonical_tracks(
            ["neon priest::gold rush"], uow, user_id="default"
        )
        resolver_track = result["neon priest::gold rush"]

        # Path (a) — the matching/enrichment conversion: mbid wins the
        # `mbid or url or "lastfm:{title}"` chain, minting an MBID-keyed row.
        ct = convert_lastfm_track_to_connector(
            LastFMTrackData.model_validate({
                "name": "Gold Rush",
                "artist": "Neon Priest",
                "mbid": _MBID,
                "url": _URL,
            })
        )
        assert ct.connector_track_identifier == _MBID
        await uow.get_connector_repository().ingest_external_tracks_bulk(
            "lastfm", [ct], user_id="default"
        )

        # Path (c) — cross-discovery's ListenBrainz reuse: mints a
        # case-PRESERVED composite (strip() without lower()).
        spotify_canonical = await uow.get_track_repository().save_track(
            Track(
                id=None,
                title="Gold Rush",
                artists=[Artist(name="Neon Priest")],
                duration_ms=200_000,
                connector_track_identifiers={"spotify": "sp_lb_001"},
            )
        )
        await uow.get_connector_repository().map_track_to_connector(
            spotify_canonical,
            "spotify",
            "sp_lb_001",
            MatchMethod.DIRECT_IMPORT,
            confidence=100,
        )
        lb_lookup = AsyncMock()
        lb_lookup.spotify_id_from_metadata.return_value = "sp_lb_001"
        provider = SpotifyCrossDiscoveryProvider(
            spotify_connector=AsyncMock(),
            listenbrainz_lookup=lb_lookup,
        )
        discovered = await provider.attempt_discovery(
            resolver_track, "Neon Priest", "Gold Rush", uow, user_id="default"
        )
        assert discovered is True

        # One recording → four distinct Last.fm connector_tracks rows.
        identifiers = set(
            (
                await db_session.execute(
                    select(DBConnectorTrack.connector_track_identifier).where(
                        DBConnectorTrack.connector_name == "lastfm"
                    )
                )
            ).scalars()
        )
        assert identifiers == {
            _URL,  # inward resolver primary (URL scheme)
            "neon priest::gold rush",  # inward resolver secondary (lowercased)
            _MBID,  # conversions path (mbid-or-url scheme)
            "Neon Priest::Gold Rush",  # cross-discovery (case-preserved)
        }
