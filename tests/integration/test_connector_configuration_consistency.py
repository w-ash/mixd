"""Integration tests for connector configuration consistency.

Pins the configuration facts a connector's runtime behavior depends on: the
Last.fm pacing ceiling and each connector's own error classifier.
"""

from src.config.settings import APIConfig
from src.infrastructure.connectors.lastfm import LastFMConnector
from src.infrastructure.connectors.lastfm.models import LastFMAPIError
from src.infrastructure.connectors.spotify import SpotifyConnector


class TestConnectorConfigurationConsistency:
    """Connector configuration that drives runtime behavior."""

    def test_lastfm_default_pacing_stays_under_the_tos_ceiling(self):
        """Last.fm's API terms cap a client at 5 requests per second per IP."""
        rate_limit = APIConfig().lastfm.rate_limit

        assert rate_limit is not None
        assert 0 < rate_limit <= 5.0

    def test_each_connector_wires_its_own_error_classifier(self):
        """Service-specific error codes classify, not the generic fallback.

        Last.fm reports errors as HTTP 200 bodies with code 29 for rate
        limiting; Spotify reports HTTP 429. The default classifier calls both
        ``unknown``, which would retry blindly instead of honoring the brake.
        """
        import httpx2

        lastfm_result = LastFMConnector().error_classifier.classify_error(
            LastFMAPIError(29, "Rate Limit Exceeded")
        )

        request = httpx2.Request("GET", "https://api.spotify.com/v1/tracks")
        response = httpx2.Response(429, request=request)
        spotify_result = SpotifyConnector().error_classifier.classify_error(
            httpx2.HTTPStatusError("429", request=request, response=response)
        )

        assert lastfm_result[:2] == ("rate_limit", "29")
        assert spotify_result[:2] == ("rate_limit", "429")
