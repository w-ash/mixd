"""Characterization: domain play-context builders match the resolver builders.

The projection (v0.10.0) rebuilds canonical play context from the ledger via
``src.domain.matching.play_projection.build_play_context`` — a byte-identical
port of the resolvers' ``_build_context``. The persisted key set is
user-visible data; this test pins the two implementations together so a drift
in either shows up as a failure, not as silent context divergence between
imported and rebuilt plays.

Known, deliberate deviation: the spotify resolver stamps a per-run
``resolution_method`` (redirect/fallback) that is not reconstructible from
the ledger — the domain builder always records the stable resolver marker,
which equals the resolver's default for IDs it did not specially resolve.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from src.domain.entities import ConnectorTrackPlay
from src.domain.matching.play_projection import build_play_context
from src.infrastructure.connectors.lastfm.play_resolver import (
    LastfmConnectorPlayResolver,
)
from src.infrastructure.connectors.spotify.play_resolver import (
    SpotifyConnectorPlayResolver,
)

_PLAYED_AT = datetime(2024, 11, 5, 9, 15, 0, tzinfo=UTC)


class TestLastfmContextParity:
    def test_domain_builder_matches_resolver_builder(self):
        play = ConnectorTrackPlay(
            service="lastfm",
            artist_name="Carwash",
            track_name="Striptease",
            album_name="Shimmer",
            played_at=_PLAYED_AT,
            service_metadata={
                "lastfm_track_url": "https://last.fm/music/Carwash/_/Striptease",
                "mbid": "abc-123",
                "loved": True,
                "streamable": False,
                "extra_field": "passes through",
            },
            import_source="lastfm_api",
        )
        resolver = LastfmConnectorPlayResolver(
            lastfm_client=MagicMock(), inward_resolver=MagicMock()
        )

        assert build_play_context(play) == resolver._build_context(play)


class TestSpotifyContextParity:
    def test_domain_builder_matches_resolver_builder(self):
        track_uri = "spotify:track:4iV5W9uYEdYUVa79Axb7Rh"
        play = ConnectorTrackPlay(
            service="spotify",
            artist_name="Carwash",
            track_name="Striptease",
            album_name="Shimmer",
            played_at=_PLAYED_AT,
            ms_played=201_000,
            service_metadata={
                "track_uri": track_uri,
                "platform": "ios",
                "country": "US",
                "reason_start": "clickrow",
                "reason_end": "trackdone",
                "shuffle": True,
                "skipped": False,
                "offline": False,
                "incognito_mode": False,
                "extra_field": "passes through",
            },
            import_source="spotify_export",
        )
        resolver = SpotifyConnectorPlayResolver(spotify_connector=MagicMock())
        spotify_id = track_uri.rsplit(":", 1)[-1]

        assert build_play_context(play) == resolver._build_context(play, spotify_id)
