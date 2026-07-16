"""Integration tests for the remote MCP transport at ``/mcp`` (v0.9.5 Phase C).

Drives the **SDK's own Streamable-HTTP client** (``streamable_http_client`` +
``ClientSession``) against the FastAPI app in-process via ``ASGITransport`` —
the same protocol path Claude Desktop/Cursor take, minus the network. This is
also the live verification of the plan's R1/R2 spikes: the auth contextvar
must reach tool handlers through the transport's task spawning, and the
``/mcp`` mount must match without redirects.

Covers: tools/list parity with ``exposed_specs()``, per-request identity +
user scoping, the two-phase write flow over HTTP (exercising the durable
pending-action store), 401 challenges with RFC 9728 pointers, audience/
allowlist rejection, host-header (DNS-rebinding) rejection, and the
protected-resource-metadata documents.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import json

from fastapi import FastAPI
import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp_types import TextContent
import pytest

from src.config import settings
from src.infrastructure.persistence.database.db_connection import get_session
from src.interface.api.oauth.tokens import mint_access_token
from src.interface.mcp.server import exposed_specs
from tests.fixtures import seed_db_track

RESOURCE_URI = "http://localhost/mcp"


@pytest.fixture
async def mcp_app(mcp_enabled_app: FastAPI) -> FastAPI:
    """Alias over the shared conftest fixture (transport already running)."""
    return mcp_enabled_app


def _token(sub: str = "user-a", email: str = "a@example.com") -> str:
    return mint_access_token(sub=sub, email=email, client_id="test-client")


def _raw_client(
    app: FastAPI, token: str | None = None, host: str = "localhost"
) -> httpx.AsyncClient:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=f"http://{host}",
        headers=headers,
        timeout=30,
    )


@asynccontextmanager
async def _mcp_session(app: FastAPI, token: str) -> AsyncIterator[ClientSession]:
    """A real SDK client session over the in-process app."""
    async with _raw_client(app, token) as http_client:
        async with streamable_http_client(RESOURCE_URI, http_client=http_client) as (
            read_stream,
            write_stream,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session


def _result_payload(result: object) -> dict[str, object]:
    """Extract the JSON payload from a single-text-block tool result."""
    content = getattr(result, "content", [])
    assert content
    assert isinstance(content[0], TextContent)
    parsed = json.loads(content[0].text)
    assert isinstance(parsed, dict)
    return parsed


class TestToolSurface:
    async def test_tools_list_matches_exposed_specs(self, mcp_app: FastAPI) -> None:
        async with _mcp_session(mcp_app, _token()) as session:
            listed = await session.list_tools()
        assert sorted(t.name for t in listed.tools) == sorted(
            spec.name for spec in exposed_specs()
        )

    async def test_read_tool_returns_request_users_rows_only(
        self, mcp_app: FastAPI
    ) -> None:
        async with get_session() as session:
            await seed_db_track(session, title="Alpha Ray", user_id="user-a")
            await seed_db_track(session, title="Beta Ray", user_id="user-b")

        async with _mcp_session(mcp_app, _token(sub="user-a")) as session_a:
            result_a = await session_a.call_tool("query_library", {"scope": "all"})
        async with _mcp_session(mcp_app, _token(sub="user-b")) as session_b:
            result_b = await session_b.call_tool("query_library", {"scope": "all"})

        titles_a = json.dumps(_result_payload(result_a))
        titles_b = json.dumps(_result_payload(result_b))
        assert "Alpha Ray" in titles_a
        assert "Beta Ray" not in titles_a
        assert "Beta Ray" in titles_b
        assert "Alpha Ray" not in titles_b


class TestTwoPhaseWriteOverHttp:
    async def test_preview_then_confirm_commits_once(self, mcp_app: FastAPI) -> None:
        create_args: dict[str, object] = {
            "operation": "create",
            "name": "Remote MCP Playlist",
        }
        async with _mcp_session(mcp_app, _token()) as session:
            preview = _result_payload(
                await session.call_tool("manage_playlist", create_args)
            )
            assert preview["status"] == "needs_confirmation"
            token = preview["confirm_token"]
            assert isinstance(token, str)

            committed = _result_payload(
                await session.call_tool(
                    "manage_playlist",
                    {**create_args, "confirm": True, "confirm_token": token},
                )
            )
            assert committed.get("status") != "needs_confirmation"

            # A replayed confirm must not commit twice: the claimed token is
            # consumed, so the call degrades to a fresh preview.
            replay = _result_payload(
                await session.call_tool(
                    "manage_playlist",
                    {**create_args, "confirm": True, "confirm_token": token},
                )
            )
            assert replay["status"] == "needs_confirmation"

            listing = _result_payload(
                await session.call_tool("query_playlists", {"view": "list"})
            )
        assert json.dumps(listing).count("Remote MCP Playlist") >= 1


class TestAuthChallenges:
    async def test_no_token_401_names_resource_metadata(self, mcp_app: FastAPI) -> None:
        async with _raw_client(mcp_app) as client:
            resp = await client.post("/mcp", json={"jsonrpc": "2.0"})
        assert resp.status_code == 401
        challenge = resp.headers["www-authenticate"]
        assert challenge.startswith("Bearer ")
        assert (
            "resource_metadata="
            '"http://localhost/.well-known/oauth-protected-resource/mcp"' in challenge
        )

    async def test_wrong_audience_token_401(
        self, mcp_app: FastAPI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings.mcp_oauth, "issuer", "http://localhost")
        monkeypatch.setattr(
            settings.mcp_oauth, "resource_uri", "http://other.example/mcp"
        )
        wrong_aud = _token()
        monkeypatch.setattr(settings.mcp_oauth, "resource_uri", RESOURCE_URI)

        async with _raw_client(mcp_app, wrong_aud) as client:
            resp = await client.post("/mcp", json={"jsonrpc": "2.0"})
        assert resp.status_code == 401

    async def test_disallowed_email_401(
        self, mcp_app: FastAPI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings.server, "allowed_emails", "owner@example.com")
        token = _token(email="intruder@example.com")
        async with _raw_client(mcp_app, token) as client:
            resp = await client.post("/mcp", json={"jsonrpc": "2.0"})
        assert resp.status_code == 401

    async def test_wrong_host_rejected(self, mcp_app: FastAPI) -> None:
        async with _raw_client(mcp_app, _token(), host="evil.example") as client:
            resp = await client.post("/mcp", json={"jsonrpc": "2.0"})
        assert resp.status_code == 421


class TestDiscoveryDocuments:
    async def test_protected_resource_metadata(self, mcp_app: FastAPI) -> None:
        async with _raw_client(mcp_app) as client:
            for path in (
                "/.well-known/oauth-protected-resource/mcp",
                "/.well-known/oauth-protected-resource",
            ):
                resp = await client.get(path)
                assert resp.status_code == 200, path
                doc = resp.json()
                assert doc["resource"] == RESOURCE_URI
                # Canonical no-trailing-slash issuer (RFC 8414 exact-string
                # comparison): the metadata models preserve empty URL paths
                # when fed strings, so both documents advertise the same form.
                assert doc["authorization_servers"] == ["http://localhost"]

    async def test_jwks_served(self, mcp_app: FastAPI) -> None:
        async with _raw_client(mcp_app) as client:
            resp = await client.get("/.well-known/jwks.json")
        assert resp.status_code == 200
        keys = resp.json()["keys"]
        assert keys[0]["kty"] == "OKP"
        assert "d" not in keys[0]
