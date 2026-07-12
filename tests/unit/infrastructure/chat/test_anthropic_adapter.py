"""Unit tests for per-key adapter caching and key validation (v0.9.0.1)."""

from types import SimpleNamespace
from typing import Any

from anthropic import AuthenticationError, BadRequestError
import httpx
import pytest

import src.infrastructure.chat.anthropic_adapter as adapter_mod
from src.infrastructure.chat.anthropic_adapter import (
    aclose_all_adapters,
    evict_adapter_cache,
    get_anthropic_adapter_for_key,
    validate_anthropic_key,
)


class TestAdapterCache:
    def setup_method(self) -> None:
        evict_adapter_cache()

    def teardown_method(self) -> None:
        evict_adapter_cache()

    def test_same_key_returns_cached_instance(self) -> None:
        first = get_anthropic_adapter_for_key("sk-ant-aaa")
        second = get_anthropic_adapter_for_key("sk-ant-aaa")
        assert first is second

    def test_different_keys_get_different_adapters(self) -> None:
        # Rotation/BYO isolation: one user's credential never reuses another's.
        assert get_anthropic_adapter_for_key("sk-ant-aaa") is not (
            get_anthropic_adapter_for_key("sk-ant-bbb")
        )

    def test_evict_forces_rebuild(self) -> None:
        before = get_anthropic_adapter_for_key("sk-ant-aaa")
        evict_adapter_cache()
        assert get_anthropic_adapter_for_key("sk-ant-aaa") is not before


class _FakeClient:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self._raises = raises
        self.closed = False

        async def _create(**_kwargs: Any) -> object:
            if self._raises is not None:
                raise self._raises
            return SimpleNamespace(content=[])

        self.messages = SimpleNamespace(create=_create)

    async def close(self) -> None:
        self.closed = True


def _status_error(exc_type: type[Any], status: int) -> Any:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request)
    return exc_type("nope", response=response, body=None)


class TestValidateKey:
    async def test_valid_key_returns_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _FakeClient()
        monkeypatch.setattr(adapter_mod, "AsyncAnthropic", lambda **_: fake)
        assert await validate_anthropic_key("sk-ant-good") is True
        assert fake.closed is True  # client always cleaned up

    async def test_auth_error_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _FakeClient(raises=_status_error(AuthenticationError, 401))
        monkeypatch.setattr(adapter_mod, "AsyncAnthropic", lambda **_: fake)
        assert await validate_anthropic_key("sk-ant-bad") is False
        assert fake.closed is True

    async def test_no_billing_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A well-formed key whose account has no credit fails the live probe
        # (400 "credit balance too low") — caught so connect doesn't "succeed"
        # and then fail on the user's first real message.
        fake = _FakeClient(raises=_status_error(BadRequestError, 400))
        monkeypatch.setattr(adapter_mod, "AsyncAnthropic", lambda **_: fake)
        assert await validate_anthropic_key("sk-ant-nobilling") is False
        assert fake.closed is True

    async def test_transport_error_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A network blip is not a "bad key" — it must not silently store/accept.
        fake = _FakeClient(raises=RuntimeError("boom"))
        monkeypatch.setattr(adapter_mod, "AsyncAnthropic", lambda **_: fake)
        with pytest.raises(RuntimeError):
            await validate_anthropic_key("sk-ant-x")
        assert fake.closed is True


class TestAcloseAll:
    async def test_closes_and_clears_cached_clients(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        evict_adapter_cache()
        clients: list[_FakeClient] = []

        def _make(**_kwargs: Any) -> _FakeClient:
            client = _FakeClient()
            clients.append(client)
            return client

        monkeypatch.setattr(adapter_mod, "AsyncAnthropic", _make)
        get_anthropic_adapter_for_key("sk-ant-a")
        get_anthropic_adapter_for_key("sk-ant-b")

        await aclose_all_adapters()

        assert [c.closed for c in clients] == [True, True]  # every pool closed
        # Cache cleared → the next resolve builds a fresh client.
        get_anthropic_adapter_for_key("sk-ant-a")
        assert len(clients) == 3
