"""Integration tests for the in-app OAuth 2.1 authorization server (v0.9.5).

Drives the complete external-client journey over real HTTP against the app:
DCR registration → /authorize (302 to the consent page) → consent
approve/deny via the session-gated API → /token exchange with PKCE +
RFC 8707 ``resource`` → the minted access token actually works against
``/mcp``. Plus every rejection the epic promises: wrong ``resource`` →
``invalid_target``, bad PKCE → ``invalid_grant``, code single-use, refresh
rotation with family revocation on replay, and RFC 8414 metadata advertising
CIMD + ``"none"`` token auth.
"""

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI
import httpx

from src.interface.api.oauth.tokens import verify_access_token

RESOURCE_URI = "http://localhost/mcp"
REDIRECT_URI = "http://localhost:33418/callback"


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://localhost",
        timeout=30,
    )


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


async def _register(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        "/register",
        json={
            "redirect_uris": [REDIRECT_URI],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "client_name": "Integration Test Client",
        },
    )
    assert resp.status_code == 201, resp.text
    client_id = resp.json()["client_id"]
    assert isinstance(client_id, str)
    return client_id


async def _authorize_to_consent(
    client: httpx.AsyncClient,
    client_id: str,
    challenge: str,
    *,
    resource: str = RESOURCE_URI,
    state: str = "st-123",
) -> str:
    """GET /authorize and return the consent request_id from the redirect."""
    resp = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": resource,
        },
    )
    assert resp.status_code == 302, resp.text
    location = resp.headers["location"]
    assert location.startswith("http://localhost/oauth/consent?request_id=")
    return parse_qs(urlparse(location).query)["request_id"][0]


async def _approve(client: httpx.AsyncClient, request_id: str) -> dict[str, str]:
    """Approve consent (as the dev-default session user); return redirect params."""
    resp = await client.post(f"/api/v1/oauth/consent/{request_id}/approve")
    assert resp.status_code == 200, resp.text
    redirect_url = resp.json()["redirect_url"]
    assert redirect_url.startswith(REDIRECT_URI)
    return {k: v[0] for k, v in parse_qs(urlparse(redirect_url).query).items()}


async def _exchange(
    client: httpx.AsyncClient, client_id: str, code: str, verifier: str
) -> httpx.Response:
    return await client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": RESOURCE_URI,
        },
    )


class TestFullAuthorizationFlow:
    async def test_code_flow_yields_working_mcp_token(
        self, mcp_enabled_app: FastAPI
    ) -> None:
        async with _client(mcp_enabled_app) as client:
            client_id = await _register(client)
            verifier, challenge = _pkce()
            request_id = await _authorize_to_consent(client, client_id, challenge)

            consent = await client.get(f"/api/v1/oauth/consent/{request_id}")
            assert consent.status_code == 200
            assert consent.json()["client_name"] == "Integration Test Client"
            assert consent.json()["resource"] == RESOURCE_URI

            redirect_params = await _approve(client, request_id)
            assert redirect_params["state"] == "st-123"
            # RFC 9207: the redirect names the issuer for mix-up detection.
            assert redirect_params["iss"] == "http://localhost"

            token_resp = await _exchange(
                client, client_id, redirect_params["code"], verifier
            )
            assert token_resp.status_code == 200, token_resp.text
            body = token_resp.json()
            assert body["token_type"] == "Bearer"
            assert body["refresh_token"]

            claims = verify_access_token(body["access_token"])
            assert claims["aud"] == RESOURCE_URI
            assert claims["sub"] == "default"  # the dev-default session user
            assert claims["client_id"] == client_id

            # The minted token opens the actual MCP transport.
            mcp_resp = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0"},
                headers={"Authorization": f"Bearer {body['access_token']}"},
            )
            assert mcp_resp.status_code != 401

    async def test_code_is_single_use(self, mcp_enabled_app: FastAPI) -> None:
        async with _client(mcp_enabled_app) as client:
            client_id = await _register(client)
            verifier, challenge = _pkce()
            request_id = await _authorize_to_consent(client, client_id, challenge)
            code = (await _approve(client, request_id))["code"]

            first = await _exchange(client, client_id, code, verifier)
            assert first.status_code == 200
            replay = await _exchange(client, client_id, code, verifier)
            assert replay.status_code == 400
            assert replay.json()["error"] == "invalid_grant"

    async def test_wrong_pkce_verifier_rejected(self, mcp_enabled_app: FastAPI) -> None:
        async with _client(mcp_enabled_app) as client:
            client_id = await _register(client)
            _, challenge = _pkce()
            request_id = await _authorize_to_consent(client, client_id, challenge)
            code = (await _approve(client, request_id))["code"]

            resp = await _exchange(client, client_id, code, "wrong-verifier-string")
            assert resp.status_code == 400
            assert resp.json()["error"] == "invalid_grant"

    async def test_wrong_resource_is_invalid_target(
        self, mcp_enabled_app: FastAPI
    ) -> None:
        async with _client(mcp_enabled_app) as client:
            client_id = await _register(client)
            verifier, challenge = _pkce()
            request_id = await _authorize_to_consent(
                client, client_id, challenge, resource="https://other.example/mcp"
            )
            code = (await _approve(client, request_id))["code"]

            resp = await _exchange(client, client_id, code, verifier)
            assert resp.status_code == 400
            assert resp.json()["error"] == "invalid_target"


class TestRefreshRotation:
    async def _token_pair(
        self, client: httpx.AsyncClient, client_id: str
    ) -> dict[str, str]:
        verifier, challenge = _pkce()
        request_id = await _authorize_to_consent(client, client_id, challenge)
        code = (await _approve(client, request_id))["code"]
        resp = await _exchange(client, client_id, code, verifier)
        assert resp.status_code == 200
        return resp.json()

    async def _refresh(
        self, client: httpx.AsyncClient, client_id: str, refresh_token: str
    ) -> httpx.Response:
        return await client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
        )

    async def test_rotation_invalidates_predecessor_and_replay_kills_family(
        self, mcp_enabled_app: FastAPI
    ) -> None:
        async with _client(mcp_enabled_app) as client:
            client_id = await _register(client)
            pair = await self._token_pair(client, client_id)
            first_refresh = pair["refresh_token"]

            rotated = await self._refresh(client, client_id, first_refresh)
            assert rotated.status_code == 200
            second_refresh = rotated.json()["refresh_token"]
            assert second_refresh != first_refresh

            # Replaying the rotated-out token is replay evidence...
            replay = await self._refresh(client, client_id, first_refresh)
            assert replay.status_code == 400
            assert replay.json()["error"] == "invalid_grant"

            # ...and it kills the whole family, including the live generation.
            dead = await self._refresh(client, client_id, second_refresh)
            assert dead.status_code == 400
            assert dead.json()["error"] == "invalid_grant"


class TestConsentEdges:
    async def test_deny_redirects_with_access_denied(
        self, mcp_enabled_app: FastAPI
    ) -> None:
        async with _client(mcp_enabled_app) as client:
            client_id = await _register(client)
            _, challenge = _pkce()
            request_id = await _authorize_to_consent(client, client_id, challenge)

            resp = await client.post(f"/api/v1/oauth/consent/{request_id}/deny")
            assert resp.status_code == 200
            params = {
                k: v[0]
                for k, v in parse_qs(
                    urlparse(resp.json()["redirect_url"]).query
                ).items()
            }
            assert params["error"] == "access_denied"
            assert params["state"] == "st-123"

    async def test_unknown_request_is_404_and_decided_request_is_consumed(
        self, mcp_enabled_app: FastAPI
    ) -> None:
        async with _client(mcp_enabled_app) as client:
            client_id = await _register(client)
            _, challenge = _pkce()
            request_id = await _authorize_to_consent(client, client_id, challenge)

            assert (
                await client.get(f"/api/v1/oauth/consent/{request_id}")
            ).status_code == 200
            await _approve(client, request_id)
            # Once decided, the request is consumed — a second decision 404s.
            second = await client.post(f"/api/v1/oauth/consent/{request_id}/approve")
            assert second.status_code == 404

    async def test_unregistered_redirect_uri_rejected(
        self, mcp_enabled_app: FastAPI
    ) -> None:
        async with _client(mcp_enabled_app) as client:
            client_id = await _register(client)
            _, challenge = _pkce()
            resp = await client.get(
                "/authorize",
                params={
                    "response_type": "code",
                    "client_id": client_id,
                    "redirect_uri": "http://evil.example/steal",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "resource": RESOURCE_URI,
                },
            )
            assert resp.status_code == 400


class TestMetadata:
    async def test_rfc8414_document(self, mcp_enabled_app: FastAPI) -> None:
        async with _client(mcp_enabled_app) as client:
            resp = await client.get("/.well-known/oauth-authorization-server")
        assert resp.status_code == 200
        doc = resp.json()
        assert doc["issuer"] == "http://localhost"
        assert doc["authorization_endpoint"] == "http://localhost/authorize"
        assert doc["token_endpoint"] == "http://localhost/token"
        assert doc["registration_endpoint"] == "http://localhost/register"
        assert doc["code_challenge_methods_supported"] == ["S256"]
        # The two flags Anthropic clients key CIMD usage on:
        assert doc["client_id_metadata_document_supported"] is True
        assert "none" in doc["token_endpoint_auth_methods_supported"]
        assert doc["authorization_response_iss_parameter_supported"] is True
