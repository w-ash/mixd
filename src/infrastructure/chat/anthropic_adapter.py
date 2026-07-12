"""Anthropic SDK adapter — implements ``LLMClientProtocol``.

Wraps ``AsyncAnthropic`` behind the application-layer protocol. The SDK owns its
own transport, timeouts, and retries; this adapter's job is translating between
mixd's ``LLMRequest``/``LLMStream`` types and the beta Messages streaming API,
plus the incremental prompt-cache stamping that keeps a growing tool loop cheap.

The sandbox/container/code-execution handling is ported whole but dormant in
v0.9.0 (no server tool is in the tool list), so v0.9.2 inherits it verified.
"""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from anthropic import (
    AsyncAnthropic,
    AuthenticationError,
    BadRequestError,
    PermissionDeniedError,
)
from anthropic.lib.streaming import BetaAsyncMessageStream
from anthropic.types.beta import (
    BetaMessageParam,
    BetaServerToolUseBlock,
    BetaTextBlockParam,
    BetaToolUnionParam,
    BetaToolUseBlock as SDKToolUseBlock,
)
from anthropic.types.beta.beta_context_management_config_param import (
    BetaContextManagementConfigParam,
)
from pydantic import BaseModel

from src.application.chat.events import (
    ServerToolResultEvent,
    ServerToolStartEvent,
    TextDelta,
)
from src.application.chat.protocols import (
    LLMRequest,
    LLMResponse,
    LLMStreamEvent,
    ToolUseBlock,
)
from src.domain.entities.shared import JsonDict

# Tool-result clearing (context editing, beta): once the conversation passes the
# trigger, the oldest tool results are cleared server-side, keeping the most
# recent 3. clear_at_least makes each cache invalidation buy meaningful savings.
_CONTEXT_MANAGEMENT_BETA = "context-management-2025-06-27"
_CONTEXT_MANAGEMENT: BetaContextManagementConfigParam = {
    "edits": [
        {
            "type": "clear_tool_uses_20250919",
            "trigger": {"type": "input_tokens", "value": 30000},
            "keep": {"type": "tool_uses", "value": 3},
            "clear_at_least": {"type": "input_tokens", "value": 5000},
        }
    ]
}


def _to_message_params(messages: list[dict[str, object]]) -> list[BetaMessageParam]:
    return cast("list[BetaMessageParam]", messages)


def _to_tool_params(tools: list[dict[str, object]]) -> list[BetaToolUnionParam]:
    return cast("list[BetaToolUnionParam]", tools)


def _to_system_params(system: list[dict[str, object]]) -> list[BetaTextBlockParam]:
    return cast("list[BetaTextBlockParam]", system)


def _strip_cache_control(content: object) -> object:
    if not isinstance(content, list):
        return content
    stripped: list[object] = []
    for block in cast("list[object]", content):
        if isinstance(block, dict):
            block_dict = cast("dict[str, object]", block)
            stripped.append({
                k: v for k, v in block_dict.items() if k != "cache_control"
            })
        else:
            stripped.append(block)
    return stripped


# Block types that accept a cache_control stamp. Thinking and server-tool blocks
# reject it, as do sandbox-called tool_use blocks and their tool_results.
_STAMPABLE_BLOCK_TYPES = frozenset({"text", "tool_use", "tool_result"})


def _code_called_tool_ids(messages: list[dict[str, object]]) -> set[object]:
    """IDs of tool_use blocks invoked by the sandbox, not the model (v0.9.2)."""
    ids: set[object] = set()
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in cast("list[object]", content):
            if not isinstance(block, dict):
                continue
            block_dict = cast("dict[str, object]", block)
            if block_dict.get("type") != "tool_use":
                continue
            caller = block_dict.get("caller")
            if not isinstance(caller, dict):
                continue
            caller_dict = cast("dict[str, object]", caller)
            if caller_dict.get("type") != "direct":
                ids.add(block_dict.get("id"))
    return ids


def _is_stampable(block_dict: dict[str, object], code_called: set[object]) -> bool:
    block_type = block_dict.get("type")
    if block_type not in _STAMPABLE_BLOCK_TYPES:
        return False
    if block_type == "tool_use":
        return block_dict.get("id") not in code_called
    if block_type == "tool_result":
        return block_dict.get("tool_use_id") not in code_called
    return True


def _with_incremental_cache(
    messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Copy messages with one cache breakpoint on the last stampable block.

    The tool loop re-sends the whole growing history every turn; stamping near
    the end turns all prior turns into cache reads (tools + system carry the
    other two breakpoints). Works on copies only — the use case reuses one list
    and re-echoes raw_content on pause_turn, so stamps must never leak into or
    accumulate on the caller's dicts. Stripping stale stamps first is idempotent.
    """
    if not messages:
        return messages
    result = [
        {**message, "content": _strip_cache_control(message.get("content"))}
        for message in messages
    ]
    code_called = _code_called_tool_ids(result)
    for message in reversed(result):
        content = message["content"]
        if isinstance(content, str):
            message["content"] = [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
            return result
        if not isinstance(content, list):
            continue
        blocks = list(cast("list[object]", content))
        for i in range(len(blocks) - 1, -1, -1):
            block = blocks[i]
            if not isinstance(block, dict):
                continue
            block_dict = cast("dict[str, object]", block)
            if not _is_stampable(block_dict, code_called):
                continue
            blocks[i] = {**block_dict, "cache_control": {"type": "ephemeral"}}
            message["content"] = blocks
            return result
    return result


def _content_block_to_dict(block: BaseModel) -> dict[str, object]:
    """Serialize an Anthropic content block to a plain dict for round-tripping.

    Must preserve every block byte-faithfully: thinking blocks carry a
    ``signature`` the API validates when the turn is echoed back. Typed as
    ``BaseModel`` because the streaming path yields loosely constructed blocks
    whose extra fields still dump faithfully.
    """
    return cast("dict[str, object]", block.model_dump(mode="json", exclude_none=True))


# Server tools whose lifecycle the UI shows as a code card (v0.9.2).
_CODE_SERVER_TOOL_NAMES = frozenset({"code_execution", "bash_code_execution"})

# Sandbox output can be arbitrarily large; the UI needs only a preview.
_OUTPUT_LIMIT_CHARS = 2048


def _truncate_output(text: str) -> str:
    if len(text) <= _OUTPUT_LIMIT_CHARS:
        return text
    return f"{text[:_OUTPUT_LIMIT_CHARS]}\n[truncated]"


_CODE_RESULT_BLOCK_TYPES = frozenset({
    "code_execution_tool_result",
    "bash_code_execution_tool_result",
})


def _server_tool_result_event(block_dict: dict[str, object]) -> ServerToolResultEvent:
    tool_use_id = str(block_dict.get("tool_use_id", ""))
    content = block_dict.get("content")
    if isinstance(content, dict):
        content_dict = cast("dict[str, object]", content)
        if "stdout" in content_dict:
            return_code = content_dict.get("return_code")
            return ServerToolResultEvent(
                tool_use_id=tool_use_id,
                stdout=_truncate_output(str(content_dict.get("stdout", ""))),
                stderr=_truncate_output(str(content_dict.get("stderr", ""))),
                return_code=return_code if isinstance(return_code, int) else 0,
            )
        if "error_code" in content_dict:
            return ServerToolResultEvent(
                tool_use_id=tool_use_id,
                stdout="",
                stderr=str(content_dict["error_code"]),
                return_code=-1,
            )
    return ServerToolResultEvent(
        tool_use_id=tool_use_id, stdout="", stderr="", return_code=0
    )


class _AdapterStream:
    """Wraps Anthropic's async message stream to implement ``LLMStream``."""

    def __init__(self, stream: BetaAsyncMessageStream[None]) -> None:
        self._stream = stream
        self._container_id: str | None = None

    def __aiter__(self) -> AsyncIterator[LLMStreamEvent]:
        return self._iter_events()

    async def _iter_events(self) -> AsyncIterator[LLMStreamEvent]:
        async for event in self._stream:
            if event.type == "text":
                yield TextDelta(text=event.text)
            elif event.type == "message_delta":
                # container arrives on message_delta.delta but the accumulator
                # never copies it onto the final Message — capture it here.
                if event.delta.container is not None:
                    self._container_id = event.delta.container.id
            elif event.type == "content_block_stop":
                block = event.content_block
                if isinstance(block, SDKToolUseBlock):
                    yield ToolUseBlock(
                        id=block.id,
                        name=block.name,
                        input=cast("JsonDict", block.input),
                    )
                elif (
                    isinstance(block, BetaServerToolUseBlock)
                    and block.name in _CODE_SERVER_TOOL_NAMES
                ):
                    yield ServerToolStartEvent(
                        name=block.name,
                        tool_use_id=block.id,
                        input=cast("JsonDict", block.input),
                    )
                elif block.type in _CODE_RESULT_BLOCK_TYPES:
                    yield _server_tool_result_event(_content_block_to_dict(block))

    async def get_final_response(self) -> LLMResponse:
        final = await self._stream.get_final_message()
        tool_blocks = [
            ToolUseBlock(
                id=b.id,
                name=b.name,
                input=cast("JsonDict", b.input),
                caller=b.caller.type if b.caller else "direct",
            )
            for b in final.content
            if isinstance(b, SDKToolUseBlock)
        ]
        raw_content = [_content_block_to_dict(b) for b in final.content]
        container = final.container.id if final.container else self._container_id
        return LLMResponse(
            stop_reason=final.stop_reason or "end_turn",
            content=tool_blocks,
            raw_content=raw_content,
            container_id=container,
        )


class AnthropicAdapter:
    """Adapts ``AsyncAnthropic`` to ``LLMClientProtocol``."""

    def __init__(self, client: AsyncAnthropic) -> None:
        self._client = client

    async def aclose(self) -> None:
        """Close the underlying ``AsyncAnthropic`` (and its httpx pool).

        Named to match the connector ``aclose()`` convention even though the SDK
        exposes ``close()``; called by :func:`aclose_all_adapters` on shutdown.
        """
        await self._client.close()

    @asynccontextmanager
    async def stream(self, request: LLMRequest) -> AsyncGenerator[_AdapterStream]:
        # Adaptive thinking must be explicit — Opus 4.8 and Sonnet 5 run without
        # thinking when the parameter is omitted. The beta surface is required
        # for context_management.
        async with self._client.beta.messages.stream(
            model=request.model,
            max_tokens=request.max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": request.effort},
            system=_to_system_params(request.system),
            tools=_to_tool_params(request.tools),
            messages=_to_message_params(_with_incremental_cache(request.messages)),
            container=request.container,
            context_management=_CONTEXT_MANAGEMENT,
            betas=[_CONTEXT_MANAGEMENT_BETA],
        ) as sdk_stream:
            yield _AdapterStream(sdk_stream)


# One adapter (and httpx pool) per distinct credential. Bounded in practice by
# the number of active keys — one per per-user key plus the server fallback.
_adapters: dict[str, AnthropicAdapter] = {}


def get_anthropic_adapter_for_key(api_key: str) -> AnthropicAdapter:
    """Adapter for a specific API key, cached so each distinct key reuses one
    ``AsyncAnthropic`` (and its httpx connection pool).

    Keyed on the *credential*, not the user id: a rotated key builds a fresh
    adapter automatically, and a removed key's entry is dropped via
    :func:`evict_adapter_cache`.
    """
    adapter = _adapters.get(api_key)
    if adapter is None:
        adapter = _adapters[api_key] = AnthropicAdapter(AsyncAnthropic(api_key=api_key))
    return adapter


def evict_adapter_cache() -> None:
    """Drop cached adapters after a key is saved or removed.

    Ref-drop only (no close): a chat turn already streaming holds its adapter
    alive by refcount, so clearing here never tears down an in-flight pool. The
    dropped-but-idle client's pool is reclaimed on GC, and any survivor is closed
    on shutdown by :func:`aclose_all_adapters`. Clearing all (rather than one
    entry) needs no key bookkeeping and is cheap — rebuilds are lazy.
    """
    _adapters.clear()


async def aclose_all_adapters() -> None:
    """Close every cached ``AsyncAnthropic`` (httpx pools) — call on API shutdown.

    Safe to await at shutdown when nothing is streaming; this is the guaranteed
    close path that ``evict_adapter_cache`` deliberately leaves to GC mid-life.
    """
    for adapter in list(_adapters.values()):
        await adapter.aclose()
    _adapters.clear()


_VALIDATION_MODEL = "claude-haiku-4-5-20251001"  # cheapest current model


async def validate_anthropic_key(api_key: str) -> bool:
    """Return True if ``api_key`` can actually run a completion.

    Sends a minimal live completion (``max_tokens=1``) rather than a metadata
    probe, so a key that authenticates but has no billing/credit is rejected
    here instead of failing on the user's first real message. A bad key (401/403)
    or an unusable one (400 "credit balance too low") returns False; transport
    errors propagate so the caller can distinguish "bad key" from "couldn't
    reach Anthropic". The token cost is negligible and lands on the caller's own key.
    """
    client = AsyncAnthropic(api_key=api_key)
    try:
        await client.messages.create(
            model=_VALIDATION_MODEL,
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
    except AuthenticationError, PermissionDeniedError, BadRequestError:
        return False
    else:
        return True
    finally:
        await client.close()
