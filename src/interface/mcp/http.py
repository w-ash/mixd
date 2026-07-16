"""Remote MCP transport: resource-server auth wiring for Streamable-HTTP.

mixd's ``/mcp`` endpoint is an OAuth 2.1 **resource server** (2026-07-28 MCP
authorization spec): every request carries a bearer JWT minted by mixd's own
in-app authorization server, validated locally (signature, ``exp``,
``iss``/``aud`` pinning via ``verify_access_token``) plus the ``ALLOWED_EMAILS``
allowlist. The SDK middleware stack owns the protocol mechanics:

    AuthenticationMiddleware(BearerAuthBackend)  — parses Bearer, verifies
      → AuthContextMiddleware                    — contextvar for handlers
        → RequireAuthMiddleware                  — 401/403 + WWW-Authenticate
          → StreamableHTTPSessionManager.handle_request

Identity is per-request: tool handlers resolve the acting user from the SDK's
auth contextvar (``resolve_request_user_id``), replacing the stdio path's
process-wide ``user_id`` binding. Verified against ``mcp==2.0.0b1``: the
modern request path runs handlers inline or in a task group created inside
the request's middleware context, so the contextvar propagates (RE-VERIFY at
the stable-v2 bump).
"""

import jwt as pyjwt
from mcp.server.auth.middleware.auth_context import (
    AuthContextMiddleware,
    get_access_token,
)
from mcp.server.auth.middleware.bearer_auth import (
    BearerAuthBackend,
    RequireAuthMiddleware,
)
from mcp.server.auth.provider import AccessToken
from pydantic import AnyHttpUrl
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.types import ASGIApp

from src.config import get_logger, settings
from src.domain.exceptions import ToolExecutionError
from src.interface.api.auth_gate import parse_allowed_emails
from src.interface.api.oauth.tokens import verify_access_token

logger = get_logger(__name__)


class MixdTokenVerifier:
    """SDK ``TokenVerifier`` over mixd's local JWT validation + allowlist.

    Returning ``None`` (rather than raising) is the SDK contract for "not
    authenticated" — ``BearerAuthBackend`` then leaves ``scope["user"]`` unset
    and ``RequireAuthMiddleware`` emits the 401 challenge.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = verify_access_token(token)
        except pyjwt.InvalidTokenError as exc:
            logger.warning("mcp_token_rejected", error=str(exc))
            return None
        allowed = parse_allowed_emails(settings.server.allowed_emails)
        email = claims.get("email", "")
        if allowed is not None and email not in allowed:
            logger.warning("mcp_email_not_allowed", sub=claims.get("sub"))
            return None
        scope_claim = claims.get("scope", "")
        return AccessToken(
            token=token,
            client_id=claims.get("client_id", ""),
            scopes=scope_claim.split() if scope_claim else [],
            expires_at=claims.get("exp"),
            resource=claims.get("aud"),
            subject=claims.get("sub"),
            claims={"iss": claims.get("iss"), "email": email},
        )


def resolve_request_user_id() -> str:
    """The acting user for the current MCP request, from the validated token.

    Called per tool call by the server's call handler. Raising here (rather
    than returning a default) is deliberate: an unauthenticated request can
    never reach a tool (``RequireAuthMiddleware`` rejects it first), so a
    missing token means broken wiring — fail loudly, never fall back to a
    shared identity.
    """
    token = get_access_token()
    if token is None or not token.subject:
        raise ToolExecutionError("No authenticated user on this MCP request")
    return token.subject


def resource_metadata_url() -> str:
    """RFC 9728 metadata URL advertised in 401 challenges.

    Uses the SDK's derivation (well-known prefix + the resource's path,
    e.g. ``…/.well-known/oauth-protected-resource/mcp``) so the challenge
    header and the served route always agree.
    """
    from mcp.server.auth.routes import build_resource_metadata_url

    return str(build_resource_metadata_url(AnyHttpUrl(settings.mcp_oauth.resource_uri)))


def build_mcp_asgi_app(handle_request: ASGIApp) -> ASGIApp:
    """Wrap the session manager's ASGI entry in the SDK auth stack.

    Returned as a middleware *instance* (not a function) deliberately:
    Starlette's ``Route`` treats a non-function endpoint as a raw ASGI app,
    which is how the exact-path ``/mcp`` route serves it — a ``Mount`` would
    miss the bare ``/mcp`` POST that every MCP client sends (verified against
    Starlette's matching; the R2 spike).
    """
    protected: ASGIApp = RequireAuthMiddleware(
        handle_request,
        required_scopes=[],
        resource_metadata_url=AnyHttpUrl(resource_metadata_url()),
    )
    return AuthenticationMiddleware(
        AuthContextMiddleware(protected), backend=BearerAuthBackend(MixdTokenVerifier())
    )


def transport_allowed_hosts() -> list[str]:
    """Expand configured hosts into the transport's exact + wildcard-port forms.

    ``TransportSecurityMiddleware`` matches the Host header exactly or via a
    trailing ``:*`` pattern; behind the Fly proxy the header may or may not
    carry a port, so both forms are required per host.
    """
    hosts: list[str] = []
    for host in settings.mcp_oauth.allowed_hosts:
        hosts.extend((host, f"{host}:*"))
    return hosts


def transport_allowed_origins() -> list[str]:
    """Origins accepted by the DNS-rebinding guard.

    Native MCP clients send no Origin header (which passes); browser-based
    tooling (MCP Inspector) sends its localhost origin. Accept https origins
    for the configured hosts plus localhost dev/tooling origins on any port.
    """
    origins: list[str] = []
    for host in settings.mcp_oauth.allowed_hosts:
        if host in ("localhost", "127.0.0.1"):
            origins.extend((f"http://{host}:*", f"https://{host}:*"))
        else:
            origins.append(f"https://{host}")
    return origins
