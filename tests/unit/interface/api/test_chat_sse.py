"""Unit tests for chat SSE terminal-line formatting and drain termination.

The in-stream error path must map known exceptions to their shared code and
hide internals behind INTERNAL_ERROR for unmapped ones (R1), mirroring what the
HTTP error envelope does — no ``str(exc)`` leak over the wire.

The drain loop must also stop on SIGTERM: uvicorn waits for in-flight requests
*before* running lifespan shutdown, so a chat stream parked on a slow model
round-trip would otherwise hold the whole shutdown open.
"""

import asyncio
from collections.abc import Awaitable, Callable
import json
from unittest.mock import patch

from src.domain.exceptions import ChatUnavailableError
from src.interface.api import chat_sse
from src.interface.api.chat_sse import (
    QueueItem,
    _terminal_line,
    stream_chat_response,
)

type RunFn = Callable[[asyncio.Queue[QueueItem]], Awaitable[None]]


async def _collect(
    run_fn: RunFn,
    *,
    shutting_down: bool | list[bool] = False,
) -> list[str]:
    """Drive one chat stream to completion and return its SSE lines.

    ``shutting_down`` is either a fixed answer or a per-call sequence, which is
    how a test says "the flag flips on the Nth loop iteration".
    """
    flag = (
        {"side_effect": [*shutting_down, *([True] * 10)]}
        if isinstance(shutting_down, list)
        else {"return_value": shutting_down}
    )
    with (
        patch.object(chat_sse, "_KEEPALIVE_INTERVAL_SECONDS", 0.01),
        patch.object(chat_sse, "shutdown_requested", **flag),
    ):
        response = stream_chat_response(run_fn)
        lines = [str(line) async for line in response.body_iterator]
    # Let the generator's finally-cancel of the background task actually land.
    await asyncio.sleep(0)
    return lines


def _payload(line: str) -> dict[str, object]:
    assert line.startswith("data: ")
    return json.loads(line[len("data: ") :])


class TestTerminalLine:
    def test_done_when_no_exception(self) -> None:
        assert _payload(_terminal_line(None)) == {"type": "done"}

    def test_mapped_exception_keeps_message(self) -> None:
        body = _payload(_terminal_line(ChatUnavailableError("no key configured")))
        assert body["type"] == "error"
        assert body["code"] == "CHAT_UNAVAILABLE"
        assert body["message"] == "no key configured"

    def test_unmapped_exception_hides_internals(self) -> None:
        secret = "psycopg: password=hunter2 host=internal-db"
        body = _payload(_terminal_line(RuntimeError(secret)))
        assert body["code"] == "INTERNAL_ERROR"
        # The raw exception text must not leak — generic message only.
        assert body["message"] == "An internal error occurred"
        assert "hunter2" not in body["message"]


class TestCancelledRun:
    async def test_a_cancelled_run_still_ends_with_a_terminal_frame(self) -> None:
        """``Task.exception()`` raises on a cancelled task; the drain must
        yield the sentinel's terminal frame, not abort the stream."""

        async def run_fn(_queue: asyncio.Queue[QueueItem]) -> None:
            raise asyncio.CancelledError

        lines = await _collect(run_fn, shutting_down=False)

        assert _payload(lines[-1]) == {"type": "done"}


class TestDrainTermination:
    """The drain loop's two exits: the completion sentinel, and SIGTERM."""

    async def test_sentinel_ends_the_stream_normally(self) -> None:
        async def run_fn(queue: asyncio.Queue[QueueItem]) -> None:
            await queue.put("hello")

        lines = await _collect(run_fn, shutting_down=False)

        assert _payload(lines[0]) == {"type": "token", "text": "hello"}
        assert _payload(lines[-1]) == {"type": "done"}

    async def test_shutdown_ends_a_busy_stream_that_never_idles(self) -> None:
        """Tokens arriving faster than the keepalive must not outrun the check.

        This is the regression: while the check lived in the `TimeoutError`
        branch, it was reached *least* often exactly when the stream was busiest,
        so a steadily-answering model held uvicorn's drain open indefinitely.

        Deliberately terminal-line-free: the client reports STREAM_ENDED, which
        is the truth, rather than a `done` frame claiming a complete answer.
        """

        async def run_fn(queue: asyncio.Queue[QueueItem]) -> None:
            while True:  # No sentinel, ever — and never idle enough to time out.
                await queue.put("tick")
                await asyncio.sleep(0)

        lines = await _collect(run_fn, shutting_down=[False, False, True])

        assert [_payload(line) for line in lines] == [
            {"type": "token", "text": "tick"},
            {"type": "token", "text": "tick"},
        ]

    async def test_shutdown_ends_an_idle_stream_after_a_keepalive(self) -> None:
        """The other half: a stream parked on a slow model round-trip."""
        started = asyncio.Event()

        async def run_fn(_queue: asyncio.Queue[QueueItem]) -> None:
            started.set()
            await asyncio.Event().wait()

        lines = await _collect(run_fn, shutting_down=[False, True])

        assert started.is_set()
        assert lines == [": keepalive\n\n"]
