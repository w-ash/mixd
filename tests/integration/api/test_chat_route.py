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
        self.requests: list[LLMRequest] = []

    @asynccontextmanager
    async def stream(self, request: LLMRequest) -> AsyncIterator[_FakeStream]:
        self.requests.append(request)
        events, response = self._turns.pop(0)
        yield _FakeStream(events, response)


def _inject_llm(monkeypatch: pytest.MonkeyPatch, turns: list[_Turn]) -> _FakeLLM:
    fake = _FakeLLM(turns)

    async def _get(_user_id: str) -> _FakeLLM:
        return fake

    monkeypatch.setattr(chat_route, "get_llm_client", _get)
    return fake


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

    async def _raise(_user_id: str) -> object:
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


def _reply_only(text: str = "ok") -> list[_Turn]:
    return [
        (
            [TextDelta(text=text)],
            LLMResponse(stop_reason="end_turn", content=[]),
        )
    ]


def _system_text(request: LLMRequest) -> str:
    return "\n".join(str(block["text"]) for block in request.system)


async def test_system_prompt_carries_user_context_block(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _inject_llm(monkeypatch, _reply_only())

    resp = await client.post(
        "/api/v1/chat", json={"messages": [{"role": "user", "content": "hi"}]}
    )

    assert resp.status_code == 200
    system = _system_text(fake.requests[0])
    assert "<user_context>" in system
    assert "The user has this workflow open" not in system
    # Stats resolve against the real (seeded-or-empty) test DB — the block
    # must render either real numbers or the explicit fallback, never vanish.
    assert "tracks" in system or "unavailable" in system


async def test_system_prompt_carries_current_workflow_when_id_sent(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.integration.api.conftest import valid_workflow_definition

    created = await client.post(
        "/api/v1/workflows", json={"definition": valid_workflow_definition()}
    )
    assert created.status_code == 201
    workflow_id = created.json()["id"]

    fake = _inject_llm(monkeypatch, _reply_only())
    resp = await client.post(
        "/api/v1/chat",
        json={
            "messages": [{"role": "user", "content": "tweak it"}],
            "current_workflow_id": workflow_id,
        },
    )

    assert resp.status_code == 200
    system = _system_text(fake.requests[0])
    assert "The user has this workflow open" in system
    assert workflow_id in system


async def test_stale_current_workflow_id_degrades_to_no_context(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _inject_llm(monkeypatch, _reply_only())

    resp = await client.post(
        "/api/v1/chat",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "current_workflow_id": str(uuid4()),
        },
    )

    assert resp.status_code == 200
    assert "The user has this workflow open" not in _system_text(fake.requests[0])


_VALID_DEF = {
    "id": "chill-weekend",
    "name": "Chill Weekend",
    "tasks": [
        {"id": "src", "type": "source.liked_tracks", "config": {"limit": 100}},
        {
            "id": "dest",
            "type": "destination.create_playlist",
            "config": {"name": "Chill Weekend"},
            "upstream": ["src"],
        },
    ],
}


def _tool_turn(*calls: ToolUseBlock) -> _Turn:
    return (
        list(calls),
        LLMResponse(
            stop_reason="tool_use",
            content=list(calls),
            raw_content=[
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input}
                for c in calls
            ],
        ),
    )


def _end_turn(text: str) -> _Turn:
    return ([TextDelta(text=text)], LLMResponse(stop_reason="end_turn", content=[]))


async def test_generate_result_carries_workflow_def(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    generate = ToolUseBlock(
        id="g1", name="generate_workflow_def", input={"workflow_def": _VALID_DEF}
    )
    _inject_llm(monkeypatch, [_tool_turn(generate), _end_turn("Here's the preview.")])

    resp = await client.post(
        "/api/v1/chat", json={"messages": [{"role": "user", "content": "build it"}]}
    )

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["is_error"] is False
    assert result["summary"]["status"] == "valid"
    assert result["summary"]["workflow_def"]["name"] == "Chill Weekend"
    # Normalized echo: config/upstream present on every task for the graph.
    assert result["summary"]["workflow_def"]["tasks"][0]["upstream"] == []


async def test_invalid_generate_streams_error_result_and_loop_continues(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = ToolUseBlock(
        id="g1",
        name="generate_workflow_def",
        input={
            "workflow_def": {
                "id": "x",
                "name": "X",
                "tasks": [{"id": "a", "type": "source.bogus", "config": {}}],
            }
        },
    )
    _inject_llm(monkeypatch, [_tool_turn(bad), _end_turn("Let me fix that.")])

    resp = await client.post(
        "/api/v1/chat", json={"messages": [{"role": "user", "content": "build it"}]}
    )

    events = _parse_sse(resp.text)
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["is_error"] is True
    assert "unknown node type" in str(result["summary"])
    # The loop survived the error result and streamed the follow-up turn.
    assert any(e["type"] == "token" for e in events)
    assert events[-1]["type"] == "done"


async def test_generate_and_save_in_one_turn_then_confirm_persists_once(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    generate = ToolUseBlock(
        id="g1", name="generate_workflow_def", input={"workflow_def": _VALID_DEF}
    )
    save = ToolUseBlock(
        id="s1", name="save_workflow", input={"workflow_def": _VALID_DEF}
    )
    _inject_llm(
        monkeypatch, [_tool_turn(generate, save), _end_turn("Preview + save ready.")]
    )

    resp = await client.post(
        "/api/v1/chat", json={"messages": [{"role": "user", "content": "build it"}]}
    )
    events = _parse_sse(resp.text)
    save_result = next(
        e for e in events if e["type"] == "tool_result" and e["name"] == "save_workflow"
    )
    assert save_result["summary"]["status"] == "pending_confirmation"
    assert save_result["summary"]["details"]["mode"] == "create"
    action_id = save_result["summary"]["action_id"]

    # Nothing persisted before confirmation.
    listing = await client.get("/api/v1/workflows")
    names_before = [w["name"] for w in listing.json()["data"]]
    assert "Chill Weekend" not in names_before

    # Approve: the route claims + executes before the model turn.
    _inject_llm(monkeypatch, [_end_turn("Saved!")])
    confirm = await client.post(
        "/api/v1/chat",
        json={
            "messages": [{"role": "user", "content": "build it"}],
            "confirmation": {"action_id": action_id, "approved": True},
        },
    )
    assert confirm.status_code == 200

    listing = await client.get("/api/v1/workflows")
    names_after = [w["name"] for w in listing.json()["data"]]
    assert names_after.count("Chill Weekend") == 1

    # A second approval of the same action is single-use → 409.
    _inject_llm(monkeypatch, [_end_turn("again?")])
    replay = await client.post(
        "/api/v1/chat",
        json={
            "messages": [{"role": "user", "content": "again"}],
            "confirmation": {"action_id": action_id, "approved": True},
        },
    )
    assert replay.status_code == 409


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
