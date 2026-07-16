"""Unit tests for the agentic chat loop (ChatUseCase) with a scripted fake LLM.

The executor injected is the REAL registry executor, so these also exercise
tool dispatch through the registry end-to-end (describe_node) without a live
Anthropic key or a database.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date

import pytest

from src.application.chat.events import TextDelta, ToolResultEvent, ToolStartEvent
from src.application.chat.protocols import (
    LLMRequest,
    LLMResponse,
    LLMStreamEvent,
    ToolUseBlock,
)
from src.application.chat.system_prompt import build_system_prompt
from src.application.chat.use_case import ChatCommand, ChatUseCase
from src.application.tools.registry import build_tools, execute_tool
from src.domain.exceptions import MaxRoundsExceededError, ResponseTruncatedError

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
    """Replays a pre-scripted list of (stream events, final response) turns."""

    def __init__(self, turns: list[_Turn]) -> None:
        self._turns = iter(turns)

    @asynccontextmanager
    async def stream(self, request: LLMRequest) -> AsyncIterator[_FakeStream]:
        events, response = next(self._turns)
        yield _FakeStream(events, response)


def _command(*, max_turns: int = 3) -> ChatCommand:
    return ChatCommand(
        messages=[{"role": "user", "content": "hi"}],
        system=build_system_prompt(None, None, date(2026, 7, 11)),
        tools=build_tools(),
        model_id="test-model",
        max_turns=max_turns,
        max_tokens=1024,
        effort="high",
        user_id="default",
    )


async def _collect(use_case: ChatUseCase, command: ChatCommand) -> list[object]:
    return [event async for event in use_case.execute(command)]


async def test_tool_call_dispatches_through_registry_and_feeds_result() -> None:
    turns: list[_Turn] = [
        (
            [ToolUseBlock(id="t1", name="describe_node", input={})],
            LLMResponse(
                stop_reason="tool_use",
                content=[ToolUseBlock(id="t1", name="describe_node", input={})],
                raw_content=[{"type": "tool_use", "id": "t1"}],
            ),
        ),
        (
            [TextDelta(text="Here are the nodes.")],
            LLMResponse(stop_reason="end_turn", content=[]),
        ),
    ]
    events = await _collect(ChatUseCase(_FakeLLM(turns), execute_tool), _command())

    starts = [e for e in events if isinstance(e, ToolStartEvent)]
    results = [e for e in events if isinstance(e, ToolResultEvent)]
    texts = [e for e in events if isinstance(e, TextDelta)]
    assert [s.name for s in starts] == ["describe_node"]
    assert len(results) == 1
    assert not results[0].is_error
    # Real dispatch: the summary is the node catalog.
    assert isinstance(results[0].summary, dict)
    assert "nodes" in results[0].summary
    assert texts[-1].text == "Here are the nodes."


async def test_pause_turn_continues_the_loop() -> None:
    turns: list[_Turn] = [
        (
            [],
            LLMResponse(
                stop_reason="pause_turn",
                content=[],
                raw_content=[{"type": "text", "text": "thinking"}],
            ),
        ),
        (
            [TextDelta(text="done")],
            LLMResponse(stop_reason="end_turn", content=[]),
        ),
    ]
    events = await _collect(ChatUseCase(_FakeLLM(turns), execute_tool), _command())

    texts = [e for e in events if isinstance(e, TextDelta)]
    assert texts[-1].text == "done"


async def test_paused_rounds_do_not_burn_the_model_turn_budget() -> None:
    # A pause_turn round carries empty content, which would otherwise read as a
    # non-sandbox round and consume a model turn (C2). With max_turns=1 several
    # paused rounds must still resolve to the trailing end_turn, not trip the
    # budget with MaxRoundsExceededError.
    pause_turn: _Turn = (
        [],
        LLMResponse(
            stop_reason="pause_turn",
            content=[],
            raw_content=[{"type": "text", "text": "thinking"}],
        ),
    )
    turns: list[_Turn] = [
        pause_turn,
        pause_turn,
        pause_turn,
        ([TextDelta(text="done")], LLMResponse(stop_reason="end_turn", content=[])),
    ]
    events = await _collect(
        ChatUseCase(_FakeLLM(turns), execute_tool), _command(max_turns=1)
    )
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["done"]


async def test_max_tokens_raises_truncation_error() -> None:
    turns: list[_Turn] = [([], LLMResponse(stop_reason="max_tokens", content=[]))]
    with pytest.raises(ResponseTruncatedError):
        await _collect(ChatUseCase(_FakeLLM(turns), execute_tool), _command())


async def test_end_turn_stops_cleanly() -> None:
    turns: list[_Turn] = [
        (
            [TextDelta(text="hello")],
            LLMResponse(stop_reason="end_turn", content=[]),
        )
    ]
    events = await _collect(ChatUseCase(_FakeLLM(turns), execute_tool), _command())
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["hello"]


async def test_runaway_tool_loop_hits_the_round_budget() -> None:
    # Every turn requests a tool and never ends — must terminate at max_turns.
    tool_turn: _Turn = (
        [ToolUseBlock(id="t", name="describe_node", input={})],
        LLMResponse(
            stop_reason="tool_use",
            content=[ToolUseBlock(id="t", name="describe_node", input={})],
            raw_content=[{"type": "tool_use", "id": "t"}],
        ),
    )
    turns = [tool_turn for _ in range(10)]
    with pytest.raises(MaxRoundsExceededError):
        await _collect(
            ChatUseCase(_FakeLLM(turns), execute_tool), _command(max_turns=2)
        )


async def test_sandbox_only_rounds_hit_the_larger_backstop() -> None:
    # Sandbox-called rounds (caller != "direct") are cheap and don't count as
    # model turns, so a runaway sandbox loop must still terminate — at the
    # max_turns * _SANDBOX_ROUNDS_PER_TURN backstop, not the model-turn budget.
    # With max_turns=1 the model-turn budget alone would allow a single round;
    # five sandbox-only rounds run before the backstop trips.
    sandbox_turn: _Turn = (
        [
            ToolUseBlock(
                id="t",
                name="describe_node",
                input={},
                caller="code_execution_20260120",
            )
        ],
        LLMResponse(
            stop_reason="tool_use",
            content=[
                ToolUseBlock(
                    id="t",
                    name="describe_node",
                    input={},
                    caller="code_execution_20260120",
                )
            ],
            raw_content=[{"type": "tool_use", "id": "t"}],
        ),
    )
    turns = [sandbox_turn for _ in range(20)]
    with pytest.raises(MaxRoundsExceededError):
        await _collect(
            ChatUseCase(_FakeLLM(turns), execute_tool), _command(max_turns=1)
        )
