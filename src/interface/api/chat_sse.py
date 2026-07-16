"""Queue-bridge SSE streaming for the chat endpoint.

Distinct from ``routes/operations.py`` on purpose: that endpoint is a GET/
EventSource stream over a per-operation queue with Last-Event-ID reconnect, for
resumable long-op progress. Chat is POST-body, ephemeral (no reconnect), and
aborts on disconnect. A background task puts items (text deltas, tool events,
or the None sentinel) into an ``asyncio.Queue``; the generator reads them and
yields SSE-formatted lines.
"""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
import json

from anthropic import APIError as AnthropicSDKError
from fastapi.responses import StreamingResponse

from src.application.chat.events import (
    ServerToolResultEvent,
    ServerToolStartEvent,
    ToolResultEvent,
    ToolStartEvent,
)
from src.application.tools.registry import TOOLS
from src.config import get_logger
from src.domain.entities.shared import JsonDict
from src.domain.exceptions import (
    AnthropicApiError,
    MaxRoundsExceededError,
    ResponseTruncatedError,
    ToolExecutionError,
)
from src.interface.api.error_codes import CHAT_ERROR_CODES

logger = get_logger(__name__)


type QueueItem = (
    str
    | ToolStartEvent
    | ToolResultEvent
    | ServerToolStartEvent
    | ServerToolResultEvent
    | None
)

# Adaptive thinking can run tens of seconds with no stream events; without
# periodic bytes the connection looks dead to the browser and any proxy in
# between. SSE comment lines (leading ":") are ignored by parsers.
_KEEPALIVE_INTERVAL_SECONDS = 15.0

# Tool kind rides on the tool_start frame so the frontend never guesses
# read-vs-write from tool names.
_TOOL_KINDS: dict[str, str] = {spec.name: spec.kind for spec in TOOLS}

# Codes shared with the HTTP path (error_codes.CHAT_ERROR_CODES) plus the
# in-stream-only exceptions that never surface as an HTTP status. Building the
# shared half from the one table keeps the two paths' code strings in lockstep.
_ERROR_CODE_MAP: dict[type[Exception], str] = {
    exc: code for exc, (code, _status) in CHAT_ERROR_CODES.items()
} | {
    ToolExecutionError: "TOOL_EXECUTION_ERROR",
    MaxRoundsExceededError: "MAX_ROUNDS_EXCEEDED",
    ResponseTruncatedError: "RESPONSE_TRUNCATED",
    AnthropicApiError: "ANTHROPIC_API_ERROR",
    AnthropicSDKError: "ANTHROPIC_API_ERROR",
}


def _map_error_code(exc: BaseException) -> str:
    for exc_type, code in _ERROR_CODE_MAP.items():
        if isinstance(exc, exc_type):
            return code
    return "INTERNAL_ERROR"


def _sse_line(payload: JsonDict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


# One queued stream item (the None sentinel is handled separately).
type StreamItem = (
    str
    | ToolStartEvent
    | ToolResultEvent
    | ServerToolStartEvent
    | ServerToolResultEvent
)


def _format_item(item: StreamItem) -> str:
    """Serialize one stream item to its SSE line."""
    if isinstance(item, ToolStartEvent):
        return _sse_line({
            "type": "tool_start",
            "name": item.name,
            "id": item.tool_use_id,
            "kind": _TOOL_KINDS.get(item.name, "read"),
        })
    if isinstance(item, ToolResultEvent):
        return _sse_line({
            "type": "tool_result",
            "name": item.name,
            "id": item.tool_use_id,
            "summary": item.summary,
            "is_error": item.is_error,
        })
    if isinstance(item, ServerToolStartEvent):
        return _sse_line({
            "type": "code_start",
            "id": item.tool_use_id,
            "command": str(item.input.get("code") or item.input.get("command") or ""),
        })
    if isinstance(item, ServerToolResultEvent):
        return _sse_line({
            "type": "code_result",
            "id": item.tool_use_id,
            "stdout": item.stdout,
            "stderr": item.stderr,
            "return_code": item.return_code,
        })
    return _sse_line({"type": "token", "text": item})


def _terminal_line(exc: BaseException | None) -> str:
    """The final SSE line: a typed error, or done."""
    if exc is not None:
        code = _map_error_code(exc)
        logger.error("chat_stream_error", error=str(exc), exc_info=exc)
        # Unmapped exceptions (INTERNAL_ERROR) must not leak internals over the
        # wire — the HTTP path hides them, so the SSE path does too.
        message = str(exc) if code != "INTERNAL_ERROR" else "An internal error occurred"
        return _sse_line({
            "type": "error",
            "code": code,
            "message": message,
        })
    return _sse_line({"type": "done"})


def stream_chat_response(
    run_fn: Callable[[asyncio.Queue[QueueItem]], Awaitable[None]],
) -> StreamingResponse:
    """Build an SSE ``StreamingResponse`` for a chat operation.

    ``run_fn`` receives a queue and puts text deltas (str), tool events, or None
    (completion sentinel) into it. None is always put — on both success and
    error paths — so the generator always terminates.
    """
    queue: asyncio.Queue[QueueItem] = asyncio.Queue()

    async def _run_with_sentinel() -> None:
        try:
            await run_fn(queue)
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(_run_with_sentinel())

    async def _drain() -> AsyncGenerator[str]:
        while True:
            try:
                item = await asyncio.wait_for(
                    queue.get(), timeout=_KEEPALIVE_INTERVAL_SECONDS
                )
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            if item is None:
                yield _terminal_line(task.exception() if task.done() else None)
                return
            yield _format_item(item)

    async def event_generator() -> AsyncGenerator[str]:
        # try/finally guarantees the background task is cancelled if the client
        # disconnects (the generator is closed mid-stream) — the CancelledError
        # then propagates the async-with cleanup down through the use-case loop.
        try:
            async for line in _drain():
                yield line
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
