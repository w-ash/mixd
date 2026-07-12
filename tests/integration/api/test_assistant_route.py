"""Integration tests for the per-user assistant credential routes (v0.9.0.1).

Exercises the full stack (routes → credential storage → real DB). The live
Anthropic validation call is stubbed on the route module's
``validate_anthropic_key`` binding so no network/key is needed.

Each test gets a **unique acting user** via a ``get_current_user_id`` override.
This matters because ``oauth_tokens`` is a preserved table (not truncated
between tests) and holds exactly one row per ``(user, service)`` — sharing
``DEFAULT_USER_ID`` would let a concurrent test's key leak in under xdist.
"""

from collections.abc import AsyncGenerator
import uuid

import httpx
from pydantic import SecretStr
import pytest

from src.config.settings import settings
from src.interface.api.app import create_app
from src.interface.api.deps import get_current_user_id
import src.interface.api.routes.assistant as assistant_route
from tests.integration.api.conftest import _test_db_env

_VALID_KEY = "sk-ant-api03-test0000000000000000000000"


@pytest.fixture
async def user_client(
    postgres_url: str,
    _init_test_schema: None,
) -> AsyncGenerator[httpx.AsyncClient]:
    """An httpx client acting as a fresh, unique user (isolated credential row)."""
    with _test_db_env(postgres_url):
        app = create_app()
        uid = f"assist-{uuid.uuid4().hex[:12]}"
        app.dependency_overrides[get_current_user_id] = lambda: uid
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.fixture(autouse=True)
def _no_server_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test to no deployment-wide fallback key."""
    monkeypatch.setattr(settings.credentials, "anthropic_api_key", SecretStr(""))


def _stub_validate(monkeypatch: pytest.MonkeyPatch, result: bool) -> None:
    async def _fake(_key: str) -> bool:
        return result

    monkeypatch.setattr(assistant_route, "validate_anthropic_key", _fake)


class TestStatus:
    async def test_unavailable_without_any_key(
        self, user_client: httpx.AsyncClient
    ) -> None:
        resp = await user_client.get("/api/v1/assistant/status")
        assert resp.status_code == 200
        assert resp.json() == {"connected": False, "source": None}

    async def test_server_fallback_reports_connected(
        self, user_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            settings.credentials, "anthropic_api_key", SecretStr("sk-ant-server")
        )
        resp = await user_client.get("/api/v1/assistant/status")
        assert resp.json() == {"connected": True, "source": "server"}


class TestConnect:
    async def test_malformed_key_rejected_without_calling_anthropic(
        self, user_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called = False

        async def _fake(_key: str) -> bool:
            nonlocal called
            called = True
            return True

        monkeypatch.setattr(assistant_route, "validate_anthropic_key", _fake)

        resp = await user_client.put(
            "/api/v1/assistant/key", json={"api_key": "not-a-key"}
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_API_KEY"
        assert called is False  # format guard short-circuits the network probe
        status = await user_client.get("/api/v1/assistant/status")
        assert status.json()["connected"] is False

    async def test_key_rejected_by_anthropic_not_stored(
        self, user_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_validate(monkeypatch, result=False)
        resp = await user_client.put(
            "/api/v1/assistant/key", json={"api_key": _VALID_KEY}
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_API_KEY"
        status = await user_client.get("/api/v1/assistant/status")
        assert status.json()["connected"] is False

    async def test_valid_key_connects(
        self, user_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_validate(monkeypatch, result=True)
        resp = await user_client.put(
            "/api/v1/assistant/key", json={"api_key": _VALID_KEY}
        )
        assert resp.status_code == 200
        assert resp.json() == {"connected": True, "source": "user"}

        status = await user_client.get("/api/v1/assistant/status")
        assert status.json() == {"connected": True, "source": "user"}

    async def test_key_never_echoed_back(
        self, user_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_validate(monkeypatch, result=True)
        put = await user_client.put(
            "/api/v1/assistant/key", json={"api_key": _VALID_KEY}
        )
        status = await user_client.get("/api/v1/assistant/status")
        assert _VALID_KEY not in put.text
        assert "sk-ant" not in put.text
        assert "sk-ant" not in status.text


class TestTestAndDelete:
    async def test_probe_stored_key(
        self, user_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_validate(monkeypatch, result=True)
        await user_client.put("/api/v1/assistant/key", json={"api_key": _VALID_KEY})
        resp = await user_client.post("/api/v1/assistant/key/test", json={})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_probe_without_stored_key(
        self, user_client: httpx.AsyncClient
    ) -> None:
        resp = await user_client.post("/api/v1/assistant/key/test", json={})
        assert resp.json() == {"ok": False, "detail": "No API key stored to test."}

    async def test_delete_removes_key(
        self, user_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_validate(monkeypatch, result=True)
        await user_client.put("/api/v1/assistant/key", json={"api_key": _VALID_KEY})
        resp = await user_client.delete("/api/v1/assistant/key")
        assert resp.status_code == 204
        status = await user_client.get("/api/v1/assistant/status")
        assert status.json()["connected"] is False


class TestPerUserScoping:
    async def test_key_scoped_per_user(self, user_client: httpx.AsyncClient) -> None:
        # `user_client` establishes the test DB env; call the storage layer with
        # two distinct users to assert credential scoping directly.
        from src.infrastructure.chat.credentials import (
            load_user_anthropic_key,
            save_user_anthropic_key,
        )
        from src.interface.api.deps import resolve_chat_source

        user_a = f"scope-a-{uuid.uuid4().hex[:8]}"
        user_b = f"scope-b-{uuid.uuid4().hex[:8]}"
        await save_user_anthropic_key(user_a, _VALID_KEY)

        assert await load_user_anthropic_key(user_a) == _VALID_KEY
        assert await load_user_anthropic_key(user_b) is None
        assert await resolve_chat_source(user_a) == "user"
        assert await resolve_chat_source(user_b) is None
