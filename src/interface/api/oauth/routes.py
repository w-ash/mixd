"""Route assembly for the in-app OAuth 2.1 authorization server.

Mirrors the SDK's ``create_auth_routes`` — same handlers, same paths — but
builds the RFC 8414 metadata document itself, because ``build_metadata()``
hardcodes ``token_endpoint_auth_methods_supported`` to secret-based methods
and never sets the CIMD flag. mixd's AS must advertise:

- ``"none"`` token-endpoint auth (CIMD/public native clients hold no secret),
- ``client_id_metadata_document_supported: true`` (Anthropic clients use
  CIMD iff this flag is present, else fall back to noisy per-connection DCR),
- ``authorization_response_iss_parameter_supported: true`` (RFC 9207 — the
  consent redirect carries ``iss``).

The issuer is passed as a plain string so the metadata model's
``url_preserve_empty_path`` keeps its canonical no-trailing-slash form
(RFC 8414 issuer comparison is exact-string).
"""

from typing import cast

from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.handlers.register import RegistrationHandler
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.auth.routes import build_metadata, cors_middleware
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from pydantic import AnyHttpUrl
from starlette.routing import Route

from src.config import settings
from src.interface.api.oauth.provider import MixdOAuthProvider


def build_oauth_as_routes() -> list[Route]:
    """The AS's public endpoints: metadata, authorize, token, register."""
    provider = MixdOAuthProvider()
    registration_options = ClientRegistrationOptions(enabled=True)

    metadata = build_metadata(
        issuer_url=cast("AnyHttpUrl", settings.mcp_oauth.issuer_url),
        service_documentation_url=None,
        client_registration_options=registration_options,
        revocation_options=RevocationOptions(enabled=False),
    )
    metadata.token_endpoint_auth_methods_supported = [
        "none",
        "client_secret_post",
        "client_secret_basic",
    ]
    metadata.client_id_metadata_document_supported = True
    metadata.authorization_response_iss_parameter_supported = True

    authenticator = ClientAuthenticator(provider)
    return [
        Route(
            "/.well-known/oauth-authorization-server",
            endpoint=cors_middleware(
                MetadataHandler(metadata).handle, ["GET", "OPTIONS"]
            ),
            methods=["GET", "OPTIONS"],
        ),
        Route(
            "/authorize",
            # No CORS on authorize — clients redirect the browser here.
            endpoint=AuthorizationHandler(provider).handle,
            methods=["GET", "POST"],
        ),
        Route(
            "/token",
            endpoint=cors_middleware(
                TokenHandler(provider, authenticator).handle, ["POST", "OPTIONS"]
            ),
            methods=["POST", "OPTIONS"],
        ),
        Route(
            "/register",
            endpoint=cors_middleware(
                RegistrationHandler(provider, options=registration_options).handle,
                ["POST", "OPTIONS"],
            ),
            methods=["POST", "OPTIONS"],
        ),
    ]
