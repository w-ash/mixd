"""HTTP caching middleware — ETags, Cache-Control, and Server-Timing.

Pure ASGI middleware (not BaseHTTPMiddleware) for better performance and
correct contextvars propagation. Adds:

- **Weak ETags** from MD5 of response body (GET only)
- **304 Not Modified** when ``If-None-Match`` matches
- **Cache-Control** headers based on endpoint path
- **Server-Timing** header for API response time measurement
"""

# pyright: reportAny=false
# Legitimate Any: ASGI Message dicts are untyped per Starlette's type stubs

import hashlib
import time

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Path prefix → Cache-Control value (longest prefix first — first match wins)
_CACHE_POLICIES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        [
            ("/api/v1/workflows/nodes", "max-age=86400, stale-while-revalidate=604800"),
            ("/api/v1/stats/", "max-age=30, stale-while-revalidate=300"),
            ("/api/v1/connectors", "max-age=300, stale-while-revalidate=600"),
            ("/api/v1/settings", "max-age=60, stale-while-revalidate=300"),
            ("/api/v1/health", "no-cache"),
            ("/api/v1/tracks", "max-age=10, stale-while-revalidate=60"),
            ("/api/v1/playlists", "max-age=10, stale-while-revalidate=60"),
            ("/api/v1/workflows", "max-age=10, stale-while-revalidate=60"),
        ],
        key=lambda p: -len(p[0]),
    )
)

_DEFAULT_POLICY = "max-age=10, stale-while-revalidate=30"


def _get_cache_policy(path: str) -> str:
    """Return Cache-Control value for a given path."""
    for prefix, policy in _CACHE_POLICIES:
        if path.startswith(prefix):
            return policy
    return _DEFAULT_POLICY


class CachingMiddleware:
    """Pure ASGI middleware for HTTP caching headers.

    Adds ETag, Cache-Control, and Server-Timing to GET responses.
    Skips SSE streams and non-GET requests.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path = scope.get("path", "")

        # Only process GET requests on API paths
        if method != "GET" or not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return

        # Extract If-None-Match from request headers
        if_none_match = Headers(scope=scope).get("if-none-match")

        start = time.monotonic()
        response_headers: MutableHeaders | None = None
        body_parts: list[bytes] = []
        initial_message: Message | None = None
        is_streaming = False

        async def send_wrapper(message: Message) -> None:
            nonlocal response_headers, initial_message, is_streaming

            if message["type"] == "http.response.start":
                initial_message = message
                response_headers = MutableHeaders(scope=message)

                # Detect SSE — skip caching for streaming responses
                content_type = response_headers.get("content-type", "")
                if "text/event-stream" in content_type:
                    is_streaming = True
                    _add_server_timing(response_headers, start)
                    await send(message)
                return

            if message["type"] == "http.response.body":
                if is_streaming:
                    await send(message)
                    return

                body = message.get("body", b"")
                more_body = message.get("more_body", False)
                body_parts.append(body)

                if (
                    not more_body
                    and response_headers is not None
                    and initial_message is not None
                ):
                    # Final body chunk — compute ETag and send
                    full_body = b"".join(body_parts)

                    etag = f'W/"{hashlib.md5(full_body).hexdigest()}"'  # noqa: S324
                    response_headers["etag"] = etag
                    response_headers["cache-control"] = _get_cache_policy(path)
                    _add_server_timing(response_headers, start)

                    # Check If-None-Match → 304
                    if if_none_match and if_none_match == etag:
                        initial_message["status"] = 304
                        response_headers["content-length"] = "0"
                        await send(initial_message)
                        await send({"type": "http.response.body", "body": b""})
                    else:
                        await send(initial_message)
                        await send({"type": "http.response.body", "body": full_body})

        await self.app(scope, receive, send_wrapper)


def _add_server_timing(headers: MutableHeaders, start: float) -> None:
    """Add Server-Timing header with total response time."""
    elapsed_ms = (time.monotonic() - start) * 1000
    headers["server-timing"] = f"total;dur={elapsed_ms:.1f}"


class StaticCacheMiddleware:
    """Pure ASGI middleware that adds immutable cache headers to /assets/ paths.

    Vite produces hashed filenames for JS/CSS bundles, so browsers can
    cache them indefinitely.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith("/assets/"):
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["cache-control"] = "public, max-age=31536000, immutable"
            await send(message)

        await self.app(scope, receive, send_wrapper)
