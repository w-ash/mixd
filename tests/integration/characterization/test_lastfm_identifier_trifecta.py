"""Characterization test for Last.fm identifier unification (FM3a → epic 4).

Originally pinned CURRENT (buggy) behavior: three code paths minted Last.fm
connector identifiers with three different schemes, so one recording
materialized as up to four distinct connector_tracks rows. FLIPPED by v0.8.18
epic 4 (Last.fm identifier unification): every mint site now produces
``make_lastfm_identifier(artist, title)`` on the Last.fm-CORRECTED names, so
one recording collapses to exactly ONE Last.fm connector_tracks row.

See docs/backlog/identity-resolution-design-space.md §4 (test 5).
"""

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.constants import MatchMethod
from src.domain.entities import Artist, Track
from src.domain.matching.protocols import ReuseExisting
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
_COMPOSITE = "neon priest::gold rush"


class TestAllMintSchemesConvergeOnOneRow:
    """FLIPPED characterization (FM3a, fixed by Last.fm identifier unification):
    the original pin recorded one recording fragmenting into THREE distinct
    Last.fm connector_tracks rows — the inward resolver's URL-primary +
    lowercased-composite-secondary, and the conversions path's MBID key. Every
    mint site now produces the same normalized ``artist::title`` composite
    (from Last.fm-CORRECTED names), and cross-discovery mints no Last.fm row
    of its own — so the same three paths now converge on ONE row.
    """

    async def test_one_recording_materializes_as_one_row(
        self, db_session: AsyncSession
    ):
        uow = get_unit_of_work(db_session)

        # Path (b) FIRST — the inward resolver, before any canonical exists
        # (otherwise canonical reuse short-circuits the mint). getInfo's
        # autocorrect=1 returns corrected names matching the raw ones here
        # (same words, just case), so only a single (primary) mapping mints.
        info = MagicMock()
        info.lastfm_url = _URL
        info.lastfm_album_name = "Debut"
        info.lastfm_duration = 200_000
        info.lastfm_mbid = None
        info.lastfm_artist_name = "Neon Priest"
        info.lastfm_title = "Gold Rush"
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = info
        resolver = LastfmInwardResolver(lastfm_client=lastfm_client)
        result, _ = await resolver.resolve_to_canonical_tracks(
            ["neon priest::gold rush"], uow, user_id="default"
        )
        resolver_track = result["neon priest::gold rush"]

        # Path (a) — the matching/enrichment conversion: now mints the same
        # normalized composite regardless of mbid/url (FM4a collapses the
        # mbid-or-url-or-name fallback chain to a single scheme).
        ct = convert_lastfm_track_to_connector(
            LastFMTrackData.model_validate({
                "name": "Gold Rush",
                "artist": "Neon Priest",
                "mbid": _MBID,
                "url": _URL,
            })
        )
        assert ct.connector_track_identifier == _COMPOSITE
        await uow.get_connector_repository().ingest_external_tracks_bulk(
            "lastfm", [ct], user_id="default"
        )

        # Path (c) — cross-discovery's ListenBrainz reuse. Post-epic-3 this
        # returns a ReuseExisting *decision* and mints NO Last.fm row of its own
        # (the resolver owns Last.fm mapping); the former case-preserved
        # composite is gone.
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
        discovered = await provider.discover(
            resolver_track, "Neon Priest", "Gold Rush", uow, user_id="default"
        )
        # Reuse decision points at the existing Spotify canonical; no mint.
        assert isinstance(discovered, ReuseExisting)
        assert discovered.track.id == spotify_canonical.id

        # One recording → ONE Last.fm connector_tracks row: every mint site
        # (inward resolver primary, conversions path, and cross-discovery's
        # no-op) now agrees on the same normalized composite.
        identifiers = set(
            (
                await db_session.execute(
                    select(DBConnectorTrack.connector_track_identifier).where(
                        DBConnectorTrack.connector_name == "lastfm"
                    )
                )
            ).scalars()
        )
        assert identifiers == {_COMPOSITE}
