"""Integration tests for POST /api/v1/chat (SSE bridge + registry dispatch).

A scripted fake LLM is injected via ``monkeypatch`` on the route module's
``get_llm_client`` binding, so these exercise the full request → SSE → tool
dispatch path without a live Anthropic key. describe_node needs no DB.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import json
from uuid import uuid4

import httpx
import pytest

from src.application.chat.events import TextDelta
from src.application.chat.protocols import (
    LLMRequest,
    LLMResponse,
    LLMStreamEvent,
    ToolUseBlock,
)
from src.interface.api.rate_limit import InMemoryRateLimiter
import src.interface.api.routes.chat as chat_route

type _Turn = tuple[list[LLMStreamEvent], LLMResponse]


class _FakeStream:
    def __init__(self, events: list[LLMStreamEvent], response: LLMResponse) -> None:
        self._events = events
        self._response = response

    async def __aiter__(self) -> AsyncIterator[LLMStreamEvent]:
        for event in self._events:
            yield event

    async def get_final_response(self) -> LLMResponse:
        return self._response


class _FakeLLM:
    def __init__(self, turns: list[_Turn]) -> None:
        self._turns = list(turns)

    @asynccontextmanager
    async def stream(self, request: LLMRequest) -> AsyncIterator[_FakeStream]:
        events, response = self._turns.pop(0)
        yield _FakeStream(events, response)


def _inject_llm(monkeypatch: pytest.MonkeyPatch, turns: list[_Turn]) -> None:
    monkeypatch.setattr(chat_route, "get_llm_client", lambda: _FakeLLM(turns))


def _parse_sse(text: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for block in text.split("\n\n"):
        line = block.strip()
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: ") :]))
    return events


def _describe_node_then_reply() -> list[_Turn]:
    call = ToolUseBlock(id="t1", name="describe_node", input={})
    return [
        (
            [call],
            LLMResponse(
                stop_reason="tool_use",
                content=[call],
                raw_content=[
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "describe_node",
                        "input": {},
                    }
                ],
            ),
        ),
        (
            [TextDelta(text="Those are the node types.")],
            LLMResponse(stop_reason="end_turn", content=[]),
        ),
    ]


async def test_chat_streams_sse_and_dispatches_tool_through_registry(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _inject_llm(monkeypatch, _describe_node_then_reply())

    resp = await client.post(
        "/api/v1/chat", json={"messages": [{"role": "user", "content": "list nodes"}]}
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]
    assert "tool_start" in types
    assert "tool_result" in types
    assert types[-1] == "done"

    tool_start = next(e for e in events if e["type"] == "tool_start")
    assert tool_start["name"] == "describe_node"
    assert tool_start["kind"] == "read"  # kind rides on the frame from the registry

    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert tool_result["is_error"] is False
    assert "nodes" in tool_result["summary"]

    token = next(e for e in events if e["type"] == "token")
    assert token["text"] == "Those are the node types."


async def test_chat_unavailable_when_key_unset(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.domain.exceptions import ChatUnavailableError

    def _raise() -> object:
        raise ChatUnavailableError("no key")

    monkeypatch.setattr(chat_route, "get_llm_client", _raise)

    resp = await client.post(
        "/api/v1/chat", json={"messages": [{"role": "user", "content": "hi"}]}
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "CHAT_UNAVAILABLE"


async def test_rate_limit_returns_429(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _inject_llm(monkeypatch, _describe_node_then_reply())
    monkeypatch.setattr(
        chat_route,
        "_chat_limiter",
        InMemoryRateLimiter(max_requests=1, window_seconds=60),
    )

    body = {"messages": [{"role": "user", "content": "hi"}]}
    first = await client.post("/api/v1/chat", json=body)
    assert first.status_code == 200
    # Re-inject (the first request consumed the fake's scripted turns).
    _inject_llm(monkeypatch, _describe_node_then_reply())
    second = await client.post("/api/v1/chat", json=body)

    assert second.status_code == 429
    assert second.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"


async def test_expired_confirmation_returns_action_expired(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _inject_llm(monkeypatch, _describe_node_then_reply())

    resp = await client.post(
        "/api/v1/chat",
        json={
            "messages": [{"role": "user", "content": "yes"}],
            "confirmation": {"action_id": str(uuid4()), "approved": True},
        },
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "ACTION_EXPIRED"
