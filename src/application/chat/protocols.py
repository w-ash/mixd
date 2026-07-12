"""LLM client protocol and shared types for the chat-assistant tool layer.

The application layer talks to any LLM provider through ``LLMClientProtocol``,
never the Anthropic SDK directly (the adapter lives in infrastructure). The
request/response/stream types bundle one turn's inputs and outputs so the
protocol surface stays stable as the loop grows.

``ToolContext`` is the injection point threaded from the FastAPI route through
the agentic loop into every dispatcher. It carries the acting user's id so RLS
scoping matches the web UI (every dispatcher runs through
``execute_use_case(..., user_id=ctx.user_id)``). The LLM handle that v0.9.2's
agentic tools (delegate_analysis) will need is added to this context then —
kept out now so the surface stays minimal.
"""

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from attrs import define, field

from src.application.chat.events import (
    ServerToolResultEvent,
    ServerToolStartEvent,
    TextDelta,
)
from src.application.chat.pending_actions import PendingAction
from src.config.settings import EffortLevel
from src.domain.entities.shared import JsonDict, JsonValue


@define(frozen=True, slots=True)
class ToolContext:
    """Per-request context injected into every tool dispatcher."""

    user_id: str


class OperationLauncher(Protocol):
    """Launches the long-running operation a confirmed chat action describes.

    Long-running tools (imports, playlist syncs, workflow runs) cannot be
    launched from the application layer — the SSE operation machinery (operation
    registry, progress broker, background dispatch) is interface-layer. So the
    FastAPI chat route injects this launcher into the confirmed-action path; the
    implementation maps the action's ``tool_name`` + ``details`` to the right
    interface launcher and returns the ``{operation_id, run_id}`` handle the
    chat panel subscribes to via the existing ``/operations/{id}/progress`` SSE.
    """

    async def __call__(self, action: PendingAction, user_id: str) -> JsonDict: ...


# An async tool dispatcher: validated args + context -> JSON-serializable result.
type ToolDispatch = Callable[
    [Mapping[str, JsonValue], ToolContext], Awaitable[JsonValue]
]

# The executor the loop calls per tool_use block. Injected (not imported from
# the registry) so agentic tools can reuse the loop without an import cycle:
# registry -> subagent -> use_case must never lead back to registry.
type ToolExecutorFn = Callable[
    [str, Mapping[str, JsonValue], ToolContext], Awaitable[JsonValue]
]


@define(frozen=True, slots=True)
class ToolUseBlock:
    """A tool invocation requested by the LLM."""

    id: str
    name: str
    input: JsonDict
    # Who invoked the tool: "direct" (the model) or the code-execution tool
    # type when the sandbox called it programmatically (v0.9.2).
    caller: str = "direct"


def _empty_block_list() -> list[dict[str, object]]:
    """Typed factory for the ``raw_content`` default (frozen attrs field)."""
    return []


@define(frozen=True, slots=True)
class LLMResponse:
    """Final response from a single LLM turn."""

    stop_reason: str
    content: list[ToolUseBlock]
    # Full content blocks, byte-faithful, for round-tripping the assistant turn
    # back into the next request (thinking signatures, server-tool blocks).
    raw_content: list[dict[str, object]] = field(factory=_empty_block_list)
    # Sandbox container for this turn; echoed on the next request when returning
    # results for sandbox-called tools (v0.9.2). None while the sandbox is off.
    container_id: str | None = None


type LLMStreamEvent = (
    TextDelta | ToolUseBlock | ServerToolStartEvent | ServerToolResultEvent
)


class LLMStream(Protocol):
    """Async iterator over LLM stream events with access to the final response."""

    def __aiter__(self) -> AsyncIterator[LLMStreamEvent]: ...

    async def get_final_response(self) -> LLMResponse: ...


@define(frozen=True, slots=True)
class LLMRequest:
    """One LLM turn's inputs, bundled so the protocol surface stays stable."""

    model: str
    max_tokens: int
    effort: EffortLevel
    system: list[dict[str, object]]
    tools: list[dict[str, object]]
    messages: list[dict[str, object]]
    # Sandbox container to resume; required by the API when returning results
    # for sandbox-called tools (v0.9.2).
    container: str | None = None


class LLMClientProtocol(Protocol):
    """Protocol for streaming LLM interactions."""

    def stream(self, request: LLMRequest) -> AbstractAsyncContextManager[LLMStream]: ...
