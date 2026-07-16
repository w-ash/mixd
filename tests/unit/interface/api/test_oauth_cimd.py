"""Unit tests for CIMD client resolution (v0.9.5).

Covers the two custom behaviors the SDK doesn't provide: RFC 8252 §7.3.3
port-agnostic loopback redirect matching on ``CIMDClient``, and the
SSRF-guarded metadata fetch (https-only, public-IP-only, document
invariants). Network and DNS are stubbed — no real I/O.
"""

import json
import socket
from typing import Self

from mcp.shared.auth import InvalidRedirectUriError
from pydantic import AnyUrl
import pytest

from src.interface.api.oauth import cimd
from src.interface.api.oauth.cimd import (
    CIMDClient,
    CIMDResolutionError,
    _assert_public_https,
    is_cimd_client_id,
    resolve_cimd_client,
)

CLIENT_URL = "https://client.example/oauth/metadata.json"


def _client(redirect_uris: list[str]) -> CIMDClient:
    return CIMDClient.model_validate({
        "client_id": CLIENT_URL,
        "redirect_uris": redirect_uris,
        "token_endpoint_auth_method": "none",
        "client_name": "Example",
    })


class TestClientIdShape:
    def test_https_url_is_cimd(self):
        assert is_cimd_client_id(CLIENT_URL) is True

    def test_uuid_is_not_cimd(self):
        assert is_cimd_client_id("7fbe4d18-0c19-4d") is False


class TestLoopbackRedirectMatching:
    def test_exact_match_still_works(self):
        client = _client(["https://client.example/callback"])
        uri = AnyUrl("https://client.example/callback")
        assert client.validate_redirect_uri(uri) == uri

    def test_loopback_matches_any_port(self):
        client = _client(["http://localhost/callback"])
        uri = AnyUrl("http://localhost:53682/callback")
        assert client.validate_redirect_uri(uri) == uri

    def test_loopback_ipv4_matches_any_port(self):
        client = _client(["http://127.0.0.1:8080/cb"])
        uri = AnyUrl("http://127.0.0.1:61999/cb")
        assert client.validate_redirect_uri(uri) == uri

    def test_loopback_requires_same_path(self):
        client = _client(["http://localhost/callback"])
        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(AnyUrl("http://localhost:53682/other"))

    def test_loopback_requires_same_scheme(self):
        client = _client(["http://localhost/callback"])
        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(AnyUrl("https://localhost:53682/callback"))

    def test_non_loopback_host_never_port_agnostic(self):
        client = _client(["https://client.example/callback"])
        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(AnyUrl("https://client.example:8443/callback"))

    def test_public_host_cannot_impersonate_loopback(self):
        client = _client(["http://localhost/callback"])
        with pytest.raises(InvalidRedirectUriError):
            client.validate_redirect_uri(AnyUrl("http://evil.example/callback"))


class TestSsrfGuards:
    def test_http_rejected(self):
        with pytest.raises(CIMDResolutionError, match="must be https"):
            _assert_public_https("http://client.example/meta.json")

    def test_private_address_rejected(self, monkeypatch: pytest.MonkeyPatch):
        def _resolve_private(*args: object, **kwargs: object):
            return [(socket.AF_INET, None, None, "", ("10.0.0.5", 443))]

        monkeypatch.setattr(socket, "getaddrinfo", _resolve_private)
        with pytest.raises(CIMDResolutionError, match="non-public"):
            _assert_public_https(CLIENT_URL)

    def test_loopback_address_rejected(self, monkeypatch: pytest.MonkeyPatch):
        def _resolve_loopback(*args: object, **kwargs: object):
            return [(socket.AF_INET, None, None, "", ("127.0.0.1", 443))]

        monkeypatch.setattr(socket, "getaddrinfo", _resolve_loopback)
        with pytest.raises(CIMDResolutionError, match="non-public"):
            _assert_public_https(CLIENT_URL)

    def test_public_address_accepted(self, monkeypatch: pytest.MonkeyPatch):
        def _resolve_public(*args: object, **kwargs: object):
            return [(socket.AF_INET, None, None, "", ("93.184.216.34", 443))]

        monkeypatch.setattr(socket, "getaddrinfo", _resolve_public)
        _assert_public_https(CLIENT_URL)  # no raise

    def test_unresolvable_host_rejected(self, monkeypatch: pytest.MonkeyPatch):
        def _fail(*args: object, **kwargs: object):
            raise socket.gaierror("nope")

        monkeypatch.setattr(socket, "getaddrinfo", _fail)
        with pytest.raises(CIMDResolutionError, match="does not resolve"):
            _assert_public_https(CLIENT_URL)


class _FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self.content = json.dumps(payload).encode()


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient — returns a canned metadata response."""

    response: _FakeResponse = _FakeResponse(200, {})

    def __init__(self, **kwargs: object) -> None: ...

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None: ...

    async def get(self, url: str, **kwargs: object) -> _FakeResponse:
        return self.response


@pytest.fixture
def fetch(monkeypatch: pytest.MonkeyPatch):
    """Stub DNS + HTTP + storage; return a setter for the fetched document."""
    monkeypatch.setattr(cimd, "_assert_public_https", lambda _url: None)
    monkeypatch.setattr(cimd.httpx, "AsyncClient", _FakeAsyncClient)

    async def _no_cache(_client_id: str) -> None:
        return None

    async def _no_store(*args: object) -> None: ...

    monkeypatch.setattr(cimd, "get_client", _no_cache)
    monkeypatch.setattr(cimd, "upsert_client", _no_store)

    def _set(payload: object, status_code: int = 200) -> None:
        _FakeAsyncClient.response = _FakeResponse(status_code, payload)

    return _set


class TestResolveDocument:
    async def test_valid_document_resolves(self, fetch) -> None:
        fetch({
            "client_id": CLIENT_URL,
            "redirect_uris": ["http://localhost/callback"],
            "client_name": "Example",
        })
        client = await resolve_cimd_client(CLIENT_URL)
        assert client.client_id == CLIENT_URL
        # CIMD clients are always public, whatever the document claims.
        assert client.token_endpoint_auth_method == "none"

    async def test_client_id_mismatch_rejected(self, fetch) -> None:
        fetch({
            "client_id": "https://other.example/meta.json",
            "redirect_uris": ["http://localhost/callback"],
        })
        with pytest.raises(CIMDResolutionError, match="must equal its own URL"):
            await resolve_cimd_client(CLIENT_URL)

    async def test_non_object_document_rejected(self, fetch) -> None:
        fetch(["not", "an", "object"])
        with pytest.raises(CIMDResolutionError, match="JSON object"):
            await resolve_cimd_client(CLIENT_URL)

    async def test_error_status_rejected(self, fetch) -> None:
        fetch({}, status_code=404)
        with pytest.raises(CIMDResolutionError, match="returned 404"):
            await resolve_cimd_client(CLIENT_URL)
