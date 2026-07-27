"""Unit tests for the Anthropic adapter: request build, cache stamping, caching.

The request build is the load-bearing part — it is the only place the app names
Anthropic API parameters, and a rename or a re-introduced sampling parameter is
a 400 on every request that no other test would catch.
"""

from types import SimpleNamespace
from typing import Any

from anthropic import AuthenticationError, BadRequestError
import httpx
import pytest

from src.application.chat.protocols import LLMRequest
import src.infrastructure.chat.anthropic_adapter as adapter_mod
from src.infrastructure.chat.anthropic_adapter import (
    AnthropicAdapter,
    _with_incremental_cache,
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


class _FakeSDKStream:
    """Stands in for BetaAsyncMessageStream — iterates nothing, returns a stub."""

    def __aiter__(self) -> Any:
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration

    async def get_final_message(self) -> Any:
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[],
            container=None,
            stop_details=None,
        )


class _FakeStreamManager:
    def __init__(self, stream: _FakeSDKStream) -> None:
        self._stream = stream

    async def __aenter__(self) -> _FakeSDKStream:
        return self._stream

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _RecordingMessages:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    # Sync, mirroring the SDK: stream() returns a manager, it is not a coroutine.
    # An `async def` here makes `async with` fail on a coroutine object.
    def stream(self, **kwargs: Any) -> _FakeStreamManager:
        self.kwargs = kwargs
        return _FakeStreamManager(_FakeSDKStream())


class _RecordingClient:
    def __init__(self) -> None:
        self.messages = _RecordingMessages()
        self.beta = SimpleNamespace(messages=self.messages)

    async def close(self) -> None:
        return None


def _request(**overrides: Any) -> LLMRequest:
    base: dict[str, Any] = {
        "model": "claude-opus-5",
        "max_tokens": 64_000,
        "effort": "xhigh",
        "system": [{"type": "text", "text": "sys"}],
        "tools": [{"name": "t", "description": "d", "input_schema": {}}],
        "messages": [{"role": "user", "content": "hi"}],
    }
    return LLMRequest(**(base | overrides))


class TestRequestBuild:
    async def _capture(self, **overrides: Any) -> dict[str, Any]:
        client = _RecordingClient()
        adapter = AnthropicAdapter(client)
        async with adapter.stream(_request(**overrides)):
            pass
        return client.messages.kwargs

    async def test_request_fields_pass_through(self) -> None:
        kwargs = await self._capture()
        assert kwargs["model"] == "claude-opus-5"
        assert kwargs["max_tokens"] == 64_000
        assert kwargs["output_config"] == {"effort": "xhigh"}

    async def test_thinking_is_explicitly_adaptive(self) -> None:
        # Adaptive is Opus 5's default but not Sonnet 5's, so it stays explicit.
        assert (await self._capture())["thinking"] == {"type": "adaptive"}

    async def test_context_management_beta_is_requested(self) -> None:
        kwargs = await self._capture()
        assert kwargs["betas"] == ["context-management-2025-06-27"]
        edits = kwargs["context_management"]["edits"]
        assert edits[0]["type"] == "clear_tool_uses_20250919"

    async def test_no_sampling_or_budget_parameters(self) -> None:
        # temperature/top_p/top_k and budget_tokens are all 400s on Opus 5.
        # This is the assertion that stops the next migration shipping one.
        kwargs = await self._capture()
        banned = {"temperature", "top_p", "top_k", "budget_tokens"}
        assert banned & kwargs.keys() == set()


class TestIncrementalCache:
    def test_stamps_exactly_one_block(self) -> None:
        messages: list[dict[str, object]] = [
            {"role": "user", "content": [{"type": "text", "text": "a"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "b"}]},
        ]
        result = _with_incremental_cache(messages)
        stamped = [
            block
            for message in result
            for block in message["content"]
            if "cache_control" in block
        ]
        assert len(stamped) == 1

    def test_does_not_mutate_the_caller(self) -> None:
        # The use case reuses one list and re-echoes raw_content on pause_turn,
        # so a leaked stamp would accumulate across turns.
        block: dict[str, object] = {"type": "text", "text": "a"}
        messages: list[dict[str, object]] = [{"role": "user", "content": [block]}]
        _with_incremental_cache(messages)
        assert "cache_control" not in block

    def test_is_idempotent(self) -> None:
        messages: list[dict[str, object]] = [
            {"role": "user", "content": [{"type": "text", "text": "a"}]}
        ]
        once = _with_incremental_cache(messages)
        assert _with_incremental_cache(once) == once

    def test_skips_unstampable_blocks(self) -> None:
        # Thinking blocks reject cache_control; the stamp must fall back to the
        # preceding text block rather than landing on the thinking block.
        messages: list[dict[str, object]] = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "a"},
                    {"type": "thinking", "thinking": "", "signature": "sig"},
                ],
            }
        ]
        blocks = _with_incremental_cache(messages)[0]["content"]
        assert isinstance(blocks, list)
        assert "cache_control" in blocks[0]
        assert "cache_control" not in blocks[1]


class TestFinalResponse:
    async def _response(self, **final: Any) -> Any:
        client = _RecordingClient()

        async def _get_final_message() -> Any:
            return SimpleNamespace(
                stop_reason="end_turn", content=[], container=None, **final
            )

        stream = _FakeSDKStream()
        stream.get_final_message = _get_final_message
        client.messages.stream = lambda **_: _FakeStreamManager(stream)
        adapter = AnthropicAdapter(client)
        async with adapter.stream(_request()) as adapter_stream:
            return await adapter_stream.get_final_response()

    async def test_refusal_category_is_none_without_stop_details(self) -> None:
        # stop_details is nullable even on a genuine refusal — this pins that
        # the adapter reads it defensively rather than branching on it.
        assert (await self._response(stop_details=None)).refusal_category is None

    async def test_refusal_category_is_surfaced(self) -> None:
        details = SimpleNamespace(category="cyber", explanation="…")
        assert (await self._response(stop_details=details)).refusal_category == "cyber"


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
