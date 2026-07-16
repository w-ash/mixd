"""Unit tests for the OAuth redirect-scheme guard (v0.9.5 security fix).

A client could register a ``javascript:`` / ``data:`` ``redirect_uri`` (the
SDK's ``validate_redirect_uri`` only checks registration membership, not
scheme). ``_reject_dangerous_redirect`` is the chokepoint at ``authorize``
time that stops such a URI from ever reaching ``window.location`` — an XSS
sink — at consent.
"""

from mcp.server.auth.provider import AuthorizeError
import pytest

from src.interface.api.oauth.provider import _reject_dangerous_redirect


class TestRedirectSchemeGuard:
    def test_https_any_host_allowed(self):
        _reject_dangerous_redirect("https://client.example/callback")

    def test_http_loopback_allowed(self):
        _reject_dangerous_redirect("http://localhost:53682/callback")
        _reject_dangerous_redirect("http://127.0.0.1/cb")

    def test_http_non_loopback_rejected(self):
        with pytest.raises(AuthorizeError):
            _reject_dangerous_redirect("http://evil.example/steal")

    @pytest.mark.parametrize(
        "uri",
        [
            "javascript:alert(document.domain)",
            "data:text/html,<script>alert(1)</script>",
            "vbscript:msgbox(1)",
            "file:///etc/passwd",
        ],
    )
    def test_dangerous_schemes_rejected(self, uri: str):
        with pytest.raises(AuthorizeError):
            _reject_dangerous_redirect(uri)
