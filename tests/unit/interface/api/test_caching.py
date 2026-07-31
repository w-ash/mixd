"""Tests for CachingMiddleware — ETag/304 path and the oversized-body cutoff.

Bodies under ``_MAX_ETAG_BODY_BYTES`` are buffered, hashed into a weak ETag, and
answer ``If-None-Match`` with a 304. Bodies past it skip the ETag machinery and
stream through, which is where truncation bugs would hide — the oversized tests
assert byte-for-byte body equality and correct ``more_body`` sequencing.

Uses a pure ASGI harness (no FastAPI, no HTTP server) so chunk boundaries are
under the test's direct control.
"""

import hashlib
from typing import Any

from src.interface.api.caching import _MAX_ETAG_BODY_BYTES, CachingMiddleware

# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------


class _ChunkedApp:
    """Inner ASGI app that emits a fixed sequence of body chunks."""

    def __init__(
        self, chunks: list[bytes], *, content_type: str = "application/json"
    ) -> None:
        self.chunks = chunks
        self.content_type = content_type

    async def __call__(self, scope: dict, receive: object, send: Any) -> None:
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", self.content_type.encode())],
        })
        last = len(self.chunks) - 1
        for index, chunk in enumerate(self.chunks):
            await send({
                "type": "http.response.body",
                "body": chunk,
                "more_body": index < last,
            })


class _ResponseCapture:
    """Collects ASGI send messages for assertion."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, message: dict) -> None:
        self.messages.append(dict(message))

    @property
    def start(self) -> dict:
        return self.messages[0]

    @property
    def status(self) -> int:
        return self.start["status"]

    @property
    def body_messages(self) -> list[dict]:
        return [m for m in self.messages if m["type"] == "http.response.body"]

    @property
    def body(self) -> bytes:
        return b"".join(m.get("body", b"") for m in self.body_messages)

    def header(self, name: str) -> str | None:
        for key, value in self.start["headers"]:
            if key.lower() == name.encode():
                return value.decode()
        return None


def _scope(
    path: str = "/api/v1/tracks",
    *,
    method: str = "GET",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> dict[str, Any]:
    """Build a minimal ASGI HTTP scope dict."""
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers or [],
    }


async def _noop_receive() -> dict:
    return {"type": "http.request", "body": b""}


async def _run(
    chunks: list[bytes],
    *,
    scope: dict[str, Any] | None = None,
    content_type: str = "application/json",
) -> _ResponseCapture:
    """Drive CachingMiddleware over a chunked app and capture what it sends."""
    middleware = CachingMiddleware(_ChunkedApp(chunks, content_type=content_type))
    capture = _ResponseCapture()
    await middleware(scope or _scope(), _noop_receive, capture)
    return capture


def _weak_etag(body: bytes) -> str:
    return f'W/"{hashlib.md5(body, usedforsecurity=False).hexdigest()}"'


def _assert_stream_terminates_once(capture: _ResponseCapture) -> None:
    """Exactly one body message ends the response, and it is the last one."""
    flags = [m.get("more_body", False) for m in capture.body_messages]
    assert flags[-1] is False
    assert all(flags[:-1]), "a non-final chunk closed the response early"


# ---------------------------------------------------------------------------
# Small bodies — unchanged ETag behaviour
# ---------------------------------------------------------------------------


class TestSmallResponses:
    async def test_adds_etag_cache_control_and_server_timing(self) -> None:
        body = b'{"data": [], "total": 0}'
        capture = await _run([body])

        assert capture.status == 200
        assert capture.body == body
        assert capture.header("etag") == _weak_etag(body)
        assert (
            capture.header("cache-control") == "max-age=10, stale-while-revalidate=60"
        )
        server_timing = capture.header("server-timing")
        assert server_timing is not None
        assert server_timing.startswith("total;dur=")

    async def test_matching_if_none_match_returns_304(self) -> None:
        body = b'{"data": [], "total": 0}'
        etag = _weak_etag(body)
        scope = _scope(headers=[(b"if-none-match", etag.encode())])

        capture = await _run([body], scope=scope)

        assert capture.status == 304
        assert capture.body == b""
        assert capture.header("content-length") == "0"
        assert capture.header("etag") == etag

    async def test_stale_if_none_match_returns_full_body(self) -> None:
        body = b'{"data": [], "total": 0}'
        scope = _scope(headers=[(b"if-none-match", b'W/"stale"')])

        capture = await _run([body], scope=scope)

        assert capture.status == 200
        assert capture.body == body

    async def test_multi_chunk_under_threshold_is_hashed_whole(self) -> None:
        chunks = [b"a" * 1000, b"b" * 1000, b"c" * 1000]
        capture = await _run(chunks)

        assert capture.body == b"".join(chunks)
        assert capture.header("etag") == _weak_etag(b"".join(chunks))
        # Buffered responses go out as a single coalesced body message.
        assert len(capture.body_messages) == 1

    async def test_body_exactly_at_threshold_still_gets_etag(self) -> None:
        body = b"x" * _MAX_ETAG_BODY_BYTES
        capture = await _run([body])

        assert capture.header("etag") == _weak_etag(body)
        assert capture.body == body


# ---------------------------------------------------------------------------
# Oversized bodies — ETag skipped, body must survive intact
# ---------------------------------------------------------------------------


class TestOversizedResponses:
    async def test_single_oversized_chunk_has_no_etag_but_full_body(self) -> None:
        body = b"y" * (_MAX_ETAG_BODY_BYTES + 1)
        capture = await _run([body])

        assert capture.status == 200
        assert capture.header("etag") is None
        assert len(capture.body) == len(body)
        assert capture.body == body
        _assert_stream_terminates_once(capture)

    async def test_cache_control_and_server_timing_still_applied(self) -> None:
        body = b"y" * (_MAX_ETAG_BODY_BYTES + 1)
        capture = await _run([body], scope=_scope(path="/api/v1/playlists/1/tracks"))

        assert (
            capture.header("cache-control") == "max-age=10, stale-while-revalidate=60"
        )
        server_timing = capture.header("server-timing")
        assert server_timing is not None
        assert server_timing.startswith("total;dur=")

    async def test_multi_chunk_crossing_threshold_preserves_full_body(self) -> None:
        # Four 100 KB chunks: the running total crosses the cap on chunk three,
        # so two chunks are already buffered and two arrive afterwards.
        chunks = [bytes([65 + i]) * (100 * 1024) for i in range(4)]
        expected = b"".join(chunks)

        capture = await _run(chunks)

        assert capture.status == 200
        assert capture.header("etag") is None
        assert len(capture.body) == len(expected)
        assert capture.body == expected
        _assert_stream_terminates_once(capture)

    async def test_crossing_on_final_chunk_preserves_full_body(self) -> None:
        # The cap is crossed by the last chunk, so the flush itself must close
        # the response rather than leaving it hanging open.
        chunks = [b"p" * (_MAX_ETAG_BODY_BYTES - 10), b"q" * 11]
        expected = b"".join(chunks)

        capture = await _run(chunks)

        assert capture.body == expected
        assert capture.header("etag") is None
        _assert_stream_terminates_once(capture)

    async def test_if_none_match_is_ignored_when_oversized(self) -> None:
        body = b"z" * (_MAX_ETAG_BODY_BYTES + 1)
        scope = _scope(headers=[(b"if-none-match", _weak_etag(body).encode())])

        capture = await _run([body], scope=scope)

        # No ETag is computed, so there is nothing to match — full body wins.
        assert capture.status == 200
        assert capture.body == body


# ---------------------------------------------------------------------------
# Paths the middleware leaves alone
# ---------------------------------------------------------------------------


class TestPassthrough:
    async def test_sse_response_is_not_buffered(self) -> None:
        chunks = [b"data: one\n\n", b"data: two\n\n"]
        capture = await _run(chunks, content_type="text/event-stream")

        assert capture.header("etag") is None
        assert capture.header("server-timing") is not None
        assert capture.body == b"".join(chunks)
        # Streamed through chunk by chunk, not coalesced.
        assert len(capture.body_messages) == len(chunks)

    async def test_non_get_request_is_untouched(self) -> None:
        capture = await _run([b"{}"], scope=_scope(method="POST"))

        assert capture.header("etag") is None
        assert capture.header("cache-control") is None
        assert capture.body == b"{}"

    async def test_non_api_path_is_untouched(self) -> None:
        capture = await _run([b"<html>"], scope=_scope(path="/index.html"))

        assert capture.header("etag") is None
        assert capture.header("cache-control") is None
        assert capture.body == b"<html>"
