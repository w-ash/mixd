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

import pytest

from src.interface.api.caching import (
    _DEFAULT_POLICY,
    _MAX_ETAG_BODY_BYTES,
    CachingMiddleware,
    _get_cache_policy,
)

_STATIC_CATALOG_POLICY = "max-age=86400, stale-while-revalidate=604800"

# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------


class _ChunkedApp:
    """Inner ASGI app that emits a fixed sequence of body chunks."""

    def __init__(
        self,
        chunks: list[bytes],
        *,
        content_type: str = "application/json",
        status: int = 200,
    ) -> None:
        self.chunks = chunks
        self.content_type = content_type
        self.status = status

    async def __call__(self, scope: dict, receive: object, send: Any) -> None:
        await send({
            "type": "http.response.start",
            "status": self.status,
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
    status: int = 200,
) -> _ResponseCapture:
    """Drive CachingMiddleware over a chunked app and capture what it sends."""
    middleware = CachingMiddleware(
        _ChunkedApp(chunks, content_type=content_type, status=status)
    )
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
        assert capture.header("cache-control") == "private, no-cache"
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

        assert capture.header("cache-control") == "private, no-cache"
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


# ---------------------------------------------------------------------------
# Non-200 responses — no validator, no policy
# ---------------------------------------------------------------------------


class TestNonSuccessResponses:
    """An error body is not a representation of the resource.

    Stamping it with an ETag would let a client revalidate its way back to the
    failure, and a Cache-Control header would invite a cache to keep it.
    """

    @pytest.mark.parametrize("status", [401, 404, 422, 500])
    async def test_error_status_gets_timing_only(self, status: int) -> None:
        body = b'{"error": {"code": "NOT_FOUND"}}'
        capture = await _run([body], status=status)

        assert capture.status == status
        assert capture.body == body
        assert capture.header("etag") is None
        assert capture.header("cache-control") is None
        assert capture.header("server-timing") is not None

    async def test_if_none_match_is_ignored_on_error_status(self) -> None:
        body = b'{"error": {"code": "NOT_FOUND"}}'
        scope = _scope(headers=[(b"if-none-match", _weak_etag(body).encode())])

        capture = await _run([body], scope=scope, status=404)

        # No 304 shortcut off an error body — the client gets the error itself.
        assert capture.status == 404
        assert capture.body == body

    async def test_success_still_gets_the_full_treatment(self) -> None:
        body = b'{"data": []}'
        capture = await _run([body], status=200)

        assert capture.header("etag") == _weak_etag(body)
        assert capture.header("cache-control") == "private, no-cache"


# ---------------------------------------------------------------------------
# Cache policy table
# ---------------------------------------------------------------------------

# Every user-scoped, write-invalidated GET family — i.e. everything the API
# serves apart from the exceptions below. A regression here is the v0.11.3.2 /
# v0.11.7 / v0.12.1 bug: a post-write refetch answered from the browser's disk
# cache, defeating Tanstack Query's invalidation.
_REVALIDATING_PATHS = [
    "/api/v1/tracks",
    "/api/v1/tracks/0193a2b4-1c2d-7e3f-8a9b-0c1d2e3f4a5b",
    "/api/v1/playlists",
    "/api/v1/playlists/12/tracks",
    "/api/v1/playlist-assignments",
    "/api/v1/tags",
    "/api/v1/workflows",
    "/api/v1/workflows/12",
    "/api/v1/workflows/active-runs",
    "/api/v1/workflows/12/runs/34",
    "/api/v1/operation-runs",
    "/api/v1/operations/0193a2b4-1c2d-7e3f-8a9b-0c1d2e3f4a5b/snapshot",
    "/api/v1/reviews",
    "/api/v1/schedules",
    "/api/v1/sync/schedules/7",
    "/api/v1/sync/likes",
    "/api/v1/sync/targets",
    "/api/v1/imports/checkpoints",
    "/api/v1/imports/spotify/history/queue",
    "/api/v1/assistant/status",
    "/api/v1/assistant/key",
    "/api/v1/connectors",
    "/api/v1/connectors/spotify/token",
    "/api/v1/settings",
    "/api/v1/artists",
    "/api/v1/artists/0193a2b4-1c2d-7e3f-8a9b-0c1d2e3f4a5b",
    "/api/v1/stats/dashboard",
    "/api/v1/plays",
    "/api/v1/plays/histogram",
]


class TestCachePolicies:
    @pytest.mark.parametrize("path", _REVALIDATING_PATHS)
    def test_route_families_revalidate_and_stay_private(self, path: str) -> None:
        assert _get_cache_policy(path) == "private, no-cache"
        assert _DEFAULT_POLICY == "private, no-cache"

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/workflows/nodes",
            "/api/v1/workflows/templates",
        ],
    )
    def test_runtime_static_catalogs_carry_a_long_ttl(self, path: str) -> None:
        """The node and template catalogs only change on deploy."""
        assert _get_cache_policy(path) == _STATIC_CATALOG_POLICY

    def test_musickit_config_is_private_no_store(self) -> None:
        """The MusicKit developer token rides this response — never cached.

        The longer prefix must win over the generic connectors family via the
        longest-prefix-first sort, and sibling Apple routes keep revalidating.
        """
        assert (
            _get_cache_policy("/api/v1/connectors/apple_music/musickit-config")
            == "private, no-store"
        )
        assert (
            _get_cache_policy("/api/v1/connectors/apple_music/token")
            == "private, no-cache"
        )

    def test_health_is_no_cache_without_private(self) -> None:
        """A probe answer is not user-scoped, so `private` would be noise."""
        assert _get_cache_policy("/api/v1/health") == "no-cache"
