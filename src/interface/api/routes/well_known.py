"""Well-known discovery documents for the remote MCP surface (v0.9.5).

Mounted without a prefix, and only when ``settings.mcp_oauth.enabled`` —
external OAuth/MCP clients fetch these unauthenticated. ``NeonAuthMiddleware``
guards only ``/api/``, so these are public by construction; they carry only
public-key material and metadata.
"""

from fastapi import APIRouter

from src.config import settings
from src.domain.exceptions import NotFoundError
from src.interface.api.oauth.keys import get_signing_material

router = APIRouter(tags=["well-known"])


@router.get("/.well-known/jwks.json")
async def jwks() -> dict[str, list[dict[str, str]]]:
    """Public JWKS (RFC 7517) for mixd-issued MCP access tokens.

    Registered unconditionally (OpenAPI-schema stability); 404s when the
    remote-MCP surface is disabled instead of 500ing on a missing key.
    """
    if not settings.mcp_oauth.enabled:
        raise NotFoundError("Remote MCP is not enabled on this deployment")
    return {"keys": [dict(get_signing_material().public_jwk)]}
