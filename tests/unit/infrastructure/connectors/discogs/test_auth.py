"""Discogs auth strategy: personal-access-token header injection.

The auth seam is any ``httpx2.Auth`` — ``DiscogsTokenAuth`` is the
personal-access-token strategy (``Authorization: Discogs token=<token>``),
with OAuth 1.0a slottable later without touching the client.
"""

import httpx2

from src.infrastructure.connectors.discogs.auth import DiscogsTokenAuth


class TestDiscogsTokenAuth:
    def test_injects_discogs_token_authorization_header(self):
        auth = DiscogsTokenAuth("abc123secret")
        request = httpx2.Request("GET", "https://api.discogs.com/oauth/identity")

        flow = auth.auth_flow(request)
        prepared = next(flow)

        assert prepared.headers["Authorization"] == "Discogs token=abc123secret"

    def test_flow_yields_the_original_request_exactly_once(self):
        auth = DiscogsTokenAuth("t")
        request = httpx2.Request("GET", "https://api.discogs.com/releases/1")

        flow = auth.auth_flow(request)

        assert next(flow) is request
        assert list(flow) == []
