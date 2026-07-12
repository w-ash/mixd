"""Unit tests for the single chat-credential resolver (v0.9.0.1, findings #4/#5).

``resolve_chat_credential`` is the one place the precedence rule lives (user key
→ server fallback → none), so these cover the precedence and the fail-closed
behavior when a stored key won't decrypt.
"""

from pydantic import SecretStr
import pytest

from src.config.settings import settings
import src.infrastructure.chat.credentials as credentials_mod
from src.infrastructure.chat.credentials import resolve_chat_credential
from src.infrastructure.connectors._shared.token_storage import StoredToken


class _FakeStorage:
    def __init__(self, token: StoredToken | None) -> None:
        self._token = token

    async def load_token(self, _service: str, _user_id: str) -> StoredToken | None:
        return self._token


def _stub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    token: StoredToken | None,
    server_key: str,
) -> None:
    monkeypatch.setattr(
        credentials_mod, "get_token_storage", lambda: _FakeStorage(token)
    )
    monkeypatch.setattr(
        settings.credentials, "anthropic_api_key", SecretStr(server_key)
    )


class TestResolveChatCredential:
    async def test_user_key_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub(
            monkeypatch,
            token=StoredToken(access_token="sk-ant-user", token_type="api_key"),
            server_key="sk-ant-server",
        )
        assert await resolve_chat_credential("u1") == ("sk-ant-user", "user")

    async def test_server_fallback_when_no_user_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub(monkeypatch, token=None, server_key="sk-ant-server")
        assert await resolve_chat_credential("u1") == ("sk-ant-server", "server")

    async def test_none_when_nothing_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub(monkeypatch, token=None, server_key="")
        assert await resolve_chat_credential("u1") is None

    async def test_undecryptable_key_does_not_fall_back_to_server(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A row exists (the user opted into their own key) but it won't decrypt,
        # so the storage layer returns a token dict WITHOUT access_token. We must
        # report None — not silently bill the shared server key (finding #4).
        _stub(
            monkeypatch,
            token=StoredToken(token_type="api_key"),
            server_key="sk-ant-server",
        )
        assert await resolve_chat_credential("u1") is None
