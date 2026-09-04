"""Chat use case — the agentic tool-use loop yielding stream events."""

import asyncio
from collections.abc import AsyncGenerator, Mapping, Sequence, Set as AbstractSet
import json
from typing import cast

from attrs import define

from src.application.chat.events import (
    ServerToolResultEvent,
    ServerToolStartEvent,
    TextDelta,
    ToolResultEvent,
    ToolStartEvent,
)
from src.application.chat.protocols import (
    LLMClientProtocol,
    LLMRequest,
    ToolContext,
    ToolExecutorFn,
    ToolUseBlock,
)
from src.application.chat.user_data import strip_user_data
from src.config import get_logger
from src.config.settings import EffortLevel
from src.domain.entities.shared import JsonValue
from src.domain.exceptions import (
    ChatRefusedError,
    MaxRoundsExceededError,
    ResponseTruncatedError,
)

logger = get_logger(__name__)

# Hard backstop on total client round-trips, as a multiple of max_turns.
# Sandbox-called rounds (v0.9.2) are cheap (cache reads, no context growth) but
# a runaway code loop must still terminate. Inert until the sandbox is enabled.
_SANDBOX_ROUNDS_PER_TURN = 5

# Concurrent read tools per round — below the DB pool's base size (5) so one
# chat round leaves connections for every other request in flight.
_READ_ROUND_CONCURRENCY = 4

type ChatEvent = (
    TextDelta
    | ToolStartEvent
    | ToolResultEvent
    | ServerToolStartEvent
    | ServerToolResultEvent
)


@define(frozen=True, slots=True)
class ChatCommand:
    """One chat request's inputs for the agentic loop."""

    messages: list[dict[str, object]]
    system: list[dict[str, object]]
    tools: list[dict[str, object]]
    model_id: str
    max_turns: int
    max_tokens: int
    effort: EffortLevel
    user_id: str


class ChatUseCase:
    """Runs the model turn -> tool dispatch -> feed-results loop.

    The executor is injected (not imported from the registry) so v0.9.2's
    subagent can reuse this loop without an import cycle:
    ``registry -> subagent -> use_case`` must never lead back to ``registry``.
    The tool-kind map takes the same injected route for the same reason, and
    falls back to a function-scoped registry import on first use.
    """

    def __init__(
        self,
        llm_client: LLMClientProtocol,
        tool_executor: ToolExecutorFn,
        tool_kinds: Mapping[str, str] | None = None,
        sequential_reads: AbstractSet[str] | None = None,
    ) -> None:
        self._llm = llm_client
        self._execute_tool = tool_executor
        self._tool_kinds = tool_kinds
        self._sequential_reads = sequential_reads

    def _kinds(self) -> Mapping[str, str]:
        """Return the tool name -> kind map, loading it from the registry once.

        The import is function-scoped: ``registry`` imports ``subagent``, which
        imports this module, so a module-level registry import is a cycle.
        """
        if self._tool_kinds is None:
            from src.application.tools.registry import TOOLS

            self._tool_kinds = {spec.name: spec.kind for spec in TOOLS}
        return self._tool_kinds

    def _kept_sequential(self) -> AbstractSet[str]:
        """Read tools that opted out of concurrent rounds (``parallel_safe=False``)."""
        if self._sequential_reads is None:
            from src.application.tools.registry import TOOLS

            self._sequential_reads = {
                spec.name for spec in TOOLS if not spec.parallel_safe
            }
        return self._sequential_reads

    def _all_reads(self, blocks: Sequence[ToolUseBlock]) -> bool:
        """Return True when every block is a known read-only tool.

        Read tools own no transaction across the round, so they are safe to run
        concurrently. Writes, agentic tools, and unknown names are not.
        """
        kinds = self._kinds()
        sequential = self._kept_sequential()
        return all(
            kinds.get(block.name) == "read" and block.name not in sequential
            for block in blocks
        )

    async def _run_block(
        self, block: ToolUseBlock, ctx: ToolContext
    ) -> tuple[ToolResultEvent, dict[str, object]]:
        """Execute one tool_use block into its stream event and result dict.

        Failures are captured, never raised, so a concurrent sibling's task
        group is not cancelled by one tool's error.
        """
        try:
            summary = await self._execute_tool(block.name, block.input, ctx)
            # Dispatchers eagerly wrap attacker-controllable library text in
            # <user_data> tags (see user_data.py). The model content keeps the
            # tags (quoted as data); the event boundary strips them so the
            # frontend renders the raw values.
            return (
                ToolResultEvent(
                    name=block.name,
                    tool_use_id=block.id,
                    summary=cast("JsonValue", strip_user_data(summary)),
                ),
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(summary),
                },
            )
        except Exception as e:
            return (
                ToolResultEvent(
                    name=block.name,
                    tool_use_id=block.id,
                    summary=cast("JsonValue", strip_user_data({"error": str(e)})),
                    is_error=True,
                ),
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(e),
                    "is_error": True,
                },
            )

    async def _run_round_tools(
        self, blocks: Sequence[ToolUseBlock], ctx: ToolContext
    ) -> AsyncGenerator[tuple[ToolResultEvent, dict[str, object]]]:
        """Execute a round's blocks, yielding outcomes in block order.

        An all-read round runs concurrently — each tool opens its own short
        transaction, so they share no session — behind a semaphore so one round
        cannot drain the connection pool. Any write, agentic, unknown, or
        ``parallel_safe=False`` tool keeps the whole round sequential (those may
        depend on each other's effects), and the sequential path yields each
        outcome as its tool finishes so the stream stays live.
        """
        if not self._all_reads(blocks):
            for block in blocks:
                yield await self._run_block(block, ctx)
            return

        limiter = asyncio.Semaphore(_READ_ROUND_CONCURRENCY)

        async def _bounded(
            block: ToolUseBlock,
        ) -> tuple[ToolResultEvent, dict[str, object]]:
            async with limiter:
                return await self._run_block(block, ctx)

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_bounded(block)) for block in blocks]
        for task in tasks:
            yield task.result()

    async def execute(self, command: ChatCommand) -> AsyncGenerator[ChatEvent]:
        messages = list(command.messages)
        ctx = ToolContext(user_id=command.user_id, llm=self._llm)
        # Sandbox container carried across turns of this loop (v0.9.2); the API
        # requires it back when a sandbox-called tool's result is returned.
        container_id: str | None = None
        # Rounds whose tool calls all came from the sandbox count against a
        # larger budget than the model-turn budget (v0.9.2). Inert while the
        # sandbox is off — every round is a model turn.
        model_turns = 0

        for round_index in range(command.max_turns * _SANDBOX_ROUNDS_PER_TURN):
            if model_turns >= command.max_turns:
                break
            request = LLMRequest(
                model=command.model_id,
                max_tokens=command.max_tokens,
                effort=command.effort,
                system=command.system,
                tools=command.tools,
                messages=messages,
                container=container_id,
            )
            async with self._llm.stream(request) as stream:
                async for event in stream:
                    if isinstance(
                        event,
                        TextDelta | ServerToolStartEvent | ServerToolResultEvent,
                    ):
                        yield event
                    else:
                        yield ToolStartEvent(name=event.name, tool_use_id=event.id)
                response = await stream.get_final_response()

            logger.info(
                "chat_turn",
                round=round_index,
                model_turns=model_turns,
                stop_reason=response.stop_reason,
            )
            container_id = response.container_id or container_id

            if response.stop_reason == "pause_turn":
                # A paused turn carries no client tool_use blocks and burns no
                # model turn — its empty content would otherwise read as a
                # sandbox_only=False round and wrongly consume the budget. Handle
                # it before the counting and the empty-content return below: echo
                # the assistant turn back and re-request; the API resumes it. The
                # range(max_turns * _SANDBOX_ROUNDS_PER_TURN) cap still backstops
                # a pathological pause loop.
                messages.append({
                    "role": "assistant",
                    "content": response.raw_content,
                })
                continue

            sandbox_only = bool(response.content) and all(
                tu.caller != "direct" for tu in response.content
            )
            if not sandbox_only:
                model_turns += 1

            if response.stop_reason == "refusal":
                # Must precede the empty-content return below, which would
                # otherwise end the turn silently: the client renders an empty
                # bubble, keeps the un-errored message in history, and every
                # later send replays an empty assistant block the API rejects.
                # Only the enum-valued category is surfaced — stop_details'
                # explanation is model-written free text and reaches the user
                # verbatim via the SSE error frame.
                logger.warning("chat_refused", category=response.refusal_category)
                suffix = (
                    f" (category: {response.refusal_category})"
                    if response.refusal_category
                    else ""
                )
                raise ChatRefusedError(
                    f"Anthropic's safety systems declined this request{suffix}. "
                    "Try rephrasing it."
                )

            if response.stop_reason == "max_tokens":
                raise ResponseTruncatedError(
                    f"Response hit the {command.max_tokens}-token limit"
                )

            if response.stop_reason == "end_turn":
                return

            if not response.content:
                return

            # Block order is load-bearing: the API requires exactly one
            # tool_result per tool_use in the next user message, so events and
            # results are emitted in the order the model asked for them even
            # when the tools ran concurrently.
            tool_results: list[dict[str, object]] = []
            async for event_out, result in self._run_round_tools(response.content, ctx):
                yield event_out
                tool_results.append(result)

            messages.extend([
                {"role": "assistant", "content": response.raw_content},
                {"role": "user", "content": tool_results},
            ])

        raise MaxRoundsExceededError(
            f"Exceeded the {command.max_turns}-turn budget "
            f"({command.max_turns * _SANDBOX_ROUNDS_PER_TURN} rounds including "
            "sandbox-called rounds)"
        )
