"""Security response headers middleware.

Pure ASGI middleware that adds security headers to all HTTP responses:
- X-Content-Type-Options: nosniff — prevents MIME-type sniffing
- X-Frame-Options: DENY — prevents clickjacking via iframes
- Referrer-Policy: strict-origin-when-cross-origin — limits referrer leakage

HSTS deferred (Fly.io proxy handles TLS termination).
CSP deferred to v1.1.0 (requires nonce infrastructure for inline scripts).
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class SecurityHeadersMiddleware:
    """Add security headers to all HTTP responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["x-content-type-options"] = "nosniff"
                headers["x-frame-options"] = "DENY"
                headers["referrer-policy"] = "strict-origin-when-cross-origin"
            await send(message)

        await self.app(scope, receive, send_wrapper)
