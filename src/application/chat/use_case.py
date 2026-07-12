"""Chat use case — the agentic tool-use loop yielding stream events."""

from collections.abc import AsyncGenerator
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
)
from src.application.chat.user_data import strip_user_data
from src.config import get_logger
from src.config.settings import EffortLevel
from src.domain.entities.shared import JsonValue
from src.domain.exceptions import MaxRoundsExceededError, ResponseTruncatedError

logger = get_logger(__name__)

# Hard backstop on total client round-trips, as a multiple of max_turns.
# Sandbox-called rounds (v0.9.2) are cheap (cache reads, no context growth) but
# a runaway code loop must still terminate. Inert until the sandbox is enabled.
_SANDBOX_ROUNDS_PER_TURN = 5

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
    """

    def __init__(
        self, llm_client: LLMClientProtocol, tool_executor: ToolExecutorFn
    ) -> None:
        self._llm = llm_client
        self._execute_tool = tool_executor

    async def execute(self, command: ChatCommand) -> AsyncGenerator[ChatEvent]:
        messages = list(command.messages)
        ctx = ToolContext(user_id=command.user_id)
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
            sandbox_only = bool(response.content) and all(
                tu.caller != "direct" for tu in response.content
            )
            if not sandbox_only:
                model_turns += 1

            if response.stop_reason == "pause_turn":
                # A paused turn carries no client tool_use blocks, so handle it
                # before the empty-content return below. Echo the assistant turn
                # back and re-request; the API resumes it.
                messages.append({
                    "role": "assistant",
                    "content": response.raw_content,
                })
                continue

            if response.stop_reason == "max_tokens":
                raise ResponseTruncatedError(
                    f"Response hit the {command.max_tokens}-token limit"
                )

            if response.stop_reason == "end_turn":
                return

            if not response.content:
                return

            tool_results: list[dict[str, object]] = []
            for tu in response.content:
                try:
                    summary = await self._execute_tool(tu.name, tu.input, ctx)
                    # Dispatchers eagerly wrap attacker-controllable library text
                    # in <user_data> tags (see user_data.py). The model content
                    # keeps the tags (quoted as data); the event boundary strips
                    # them so the frontend renders the raw values.
                    yield ToolResultEvent(
                        name=tu.name,
                        tool_use_id=tu.id,
                        summary=cast("JsonValue", strip_user_data(summary)),
                    )
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": json.dumps(summary),
                    })
                except Exception as e:
                    error_summary = cast(
                        "JsonValue", strip_user_data({"error": str(e)})
                    )
                    yield ToolResultEvent(
                        name=tu.name,
                        tool_use_id=tu.id,
                        summary=error_summary,
                        is_error=True,
                    )
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": str(e),
                        "is_error": True,
                    })

            messages.extend([
                {"role": "assistant", "content": response.raw_content},
                {"role": "user", "content": tool_results},
            ])

        raise MaxRoundsExceededError(f"Exceeded {command.max_turns} tool rounds")
