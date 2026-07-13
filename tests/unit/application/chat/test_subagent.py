"""Unit tests for the delegate_analysis research subagent.

The subagent reuses ChatUseCase with a fresh context and a read-only toolset.
These drive it with a scripted fake LLM and a stub executor — no live key, no
database — and assert the output contract: one dense summary, bounded, never
raising on a turn-limit stop.
"""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

import pytest

from src.application.chat.events import TextDelta
from src.application.chat.protocols import (
    LLMClientProtocol,
    LLMRequest,
    LLMResponse,
    LLMStreamEvent,
    ToolContext,
    ToolUseBlock,
)
from src.application.chat.subagent import _TRUNCATION_PREFIX, run_subagent
from src.application.tools.registry import TOOLS, build_subagent_tools
from src.config.settings import ChatConfig
from src.domain.entities.shared import JsonValue

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
        self._turns = iter(turns)

    @asynccontextmanager
    async def stream(self, request: LLMRequest) -> AsyncIterator[_FakeStream]:
        events, response = next(self._turns)
        yield _FakeStream(events, response)


async def _noop_exec(
    name: str, args: Mapping[str, JsonValue], ctx: ToolContext
) -> JsonValue:
    return {}


async def _run(turns: list[_Turn], *, cfg: ChatConfig) -> dict[str, object]:
    llm: LLMClientProtocol = _FakeLLM(turns)
    ctx = ToolContext(user_id="default", llm=llm)
    return await run_subagent(
        "why did my listening change?",
        None,
        ctx,
        tools=build_subagent_tools(),
        execute_fn=_noop_exec,
        cfg=cfg,
    )


def _cfg(**over: object) -> ChatConfig:
    return ChatConfig(**over)


# --- toolset -------------------------------------------------------------


def test_subagent_toolset_is_read_only() -> None:
    tools = build_subagent_tools()
    assert tools, "expected a non-empty read-only toolset"
    names = {t["name"] for t in tools}
    read_names = {s.name for s in TOOLS if s.kind == "read"}
    # Every read tool is present (curated hot set + deferred tail); the search
    # tool is added so the subagent can surface the deferred reads.
    assert read_names <= names
    assert names == read_names | {"tool_search_tool_bm25"}
    assert "delegate_analysis" not in names  # one level of delegation only
    assert "code_execution" not in names  # subagent has no sandbox
    assert all("allowed_callers" not in t for t in tools)


def test_subagent_hot_set_stays_small_with_search() -> None:
    # The subagent runs at low effort, so its loaded set must stay under the ~10
    # ceiling: a curated read hot set plus the always-loaded search tool, with
    # the rest of the reads deferred behind it.
    tools = build_subagent_tools()
    loaded = [t for t in tools if "defer_loading" not in t]
    assert len(loaded) <= 10, [t["name"] for t in loaded]
    assert "tool_search_tool_bm25" in {t["name"] for t in loaded}
    # Exactly one cache breakpoint, and it sits on a loaded read tool.
    breakpoints = [t for t in tools if "cache_control" in t]
    assert len(breakpoints) == 1
    assert "input_schema" in breakpoints[0]


# --- output contract -----------------------------------------------------


async def test_returns_the_final_answer_as_summary() -> None:

    turns: list[_Turn] = [
        ([TextDelta(text="Spring plays doubled.")], LLMResponse("end_turn", [])),
    ]
    result = await _run(turns, cfg=_cfg())
    assert result == {"summary": "Spring plays doubled."}


async def test_narration_before_a_tool_call_is_dropped() -> None:

    turns: list[_Turn] = [
        (
            [TextDelta(text="Let me check…"), ToolUseBlock(id="t", name="x", input={})],
            LLMResponse(
                "tool_use",
                [ToolUseBlock(id="t", name="query_library", input={})],
                raw_content=[{"type": "tool_use", "id": "t"}],
            ),
        ),
        ([TextDelta(text="The answer is 42.")], LLMResponse("end_turn", [])),
    ]
    result = await _run(turns, cfg=_cfg())
    # Only text after the last tool call is the answer — preamble is process.
    assert result == {"summary": "The answer is 42."}


async def test_empty_output_reports_no_findings() -> None:
    turns: list[_Turn] = [([], LLMResponse("end_turn", []))]
    result = await _run(turns, cfg=_cfg())
    assert result == {"summary": "The analysis produced no findings."}


async def test_turn_limit_returns_partial_with_prefix() -> None:

    tool_turn: _Turn = (
        [TextDelta(text="partial finding")],
        LLMResponse(
            "tool_use",
            [ToolUseBlock(id="t", name="query_library", input={})],
            raw_content=[{"type": "tool_use", "id": "t"}],
        ),
    )
    # subagent_max_turns=1: the loop tool-calls once then trips the budget and
    # raises MaxRoundsExceededError, which run_subagent converts to a partial.
    result = await _run([tool_turn, tool_turn], cfg=_cfg(subagent_max_turns=1))
    summary = result["summary"]
    assert isinstance(summary, str)
    assert summary.startswith(_TRUNCATION_PREFIX)
    assert "partial finding" in summary


async def test_runs_at_subagent_effort_independent_of_parent() -> None:
    # The subagent always runs at cfg.subagent_effort — run_subagent never
    # receives the parent request's user-selected effort, so a long/thorough
    # main turn can't make the (many) subagent turns expensive.
    seen: list[str] = []

    class _CapturingLLM:
        @asynccontextmanager
        async def stream(self, request: LLMRequest) -> AsyncIterator[_FakeStream]:
            seen.append(request.effort)
            yield _FakeStream([TextDelta(text="done")], LLMResponse("end_turn", []))

    llm: LLMClientProtocol = _CapturingLLM()
    await run_subagent(
        "q",
        None,
        ToolContext(user_id="default", llm=llm),
        tools=build_subagent_tools(),
        execute_fn=_noop_exec,
        cfg=_cfg(subagent_effort="low"),
    )
    assert seen == ["low"]


async def test_missing_llm_handle_raises() -> None:
    with pytest.raises(RuntimeError, match="requires an LLM handle"):
        await run_subagent(
            "q",
            None,
            ToolContext(user_id="default", llm=None),
            tools=build_subagent_tools(),
            execute_fn=_noop_exec,
            cfg=_cfg(),
        )
