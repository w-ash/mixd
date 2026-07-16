"""``OAuthAuthorizationServerProvider`` implementation for the in-app AS.

The SDK handlers own the OAuth protocol mechanics (PKCE S256 verification,
redirect_uri consistency, expiry checks, RFC 6749 error envelopes); this
provider owns what is mixd-specific:

- client resolution (CIMD URL fetch vs stored DCR registration),
- the consent detour — ``authorize`` parks the request and redirects to the
  web app, where the user approves on their existing Neon Auth session and
  ``issue_authorization_code`` mints the code bound to *that* user,
- single-use code exchange with **RFC 8707 audience enforcement** (the
  stored code's ``resource`` must be the canonical MCP resource URI),
- rotating refresh tokens with family revocation on replay,
- access-token minting via the Ed25519 token service.
"""

from datetime import UTC, datetime, timedelta
import secrets
from uuid import UUID, uuid7

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    IdentityAssertionParams,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

from src.config import get_logger, settings
from src.infrastructure.persistence.repositories import oauth_as as storage
from src.interface.api.oauth.cimd import (
    CIMDResolutionError,
    is_cimd_client_id,
    resolve_cimd_client,
)
from src.interface.api.oauth.tokens import mint_access_token, verify_access_token

logger = get_logger(__name__)


class MixdAuthorizationCode(AuthorizationCode):
    """SDK code model + the consenting user's email (for token minting)."""

    email: str = ""


class MixdRefreshToken(RefreshToken):
    """SDK refresh model + email and rotation family."""

    email: str = ""
    family_id: str = ""


class ConsentRequestExpiredError(Exception):
    """The parked authorization request is gone (expired, or already decided)."""


class MixdOAuthProvider:
    """The AS provider over mixd's Postgres-backed OAuth storage."""

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        if is_cimd_client_id(client_id):
            try:
                return await resolve_cimd_client(client_id)
            except CIMDResolutionError as err:
                logger.warning(
                    "cimd_resolution_failed", client_id=client_id, error=str(err)
                )
                return None
        stored = await storage.get_client(client_id)
        if stored is None or stored.kind != "dcr":
            return None
        return OAuthClientInformationFull.model_validate(stored.client_info)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        client_id = client_info.client_id
        if not client_id:  # pragma: no cover — the handler always sets one
            raise ValueError("registration requires a client_id")
        await storage.upsert_client(
            client_id, "dcr", client_info.model_dump(mode="json", exclude_none=True)
        )
        logger.info("oauth_client_registered", client_id=client_id)

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Park the request and send the user to the web app's consent page."""
        request_id = await storage.create_authorization_request(
            client_id=client.client_id or "",
            client_name=client.client_name,
            params=params.model_dump(mode="json"),
        )
        return f"{settings.mcp_oauth.issuer_url}/oauth/consent?request_id={request_id}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> MixdAuthorizationCode | None:
        stored = await storage.load_authorization_code(
            storage.token_hash(authorization_code)
        )
        if stored is None:
            return None
        return MixdAuthorizationCode(
            code=authorization_code,
            scopes=stored.scopes.split() if stored.scopes else [],
            expires_at=stored.expires_at.timestamp(),
            client_id=stored.client_id,
            code_challenge=stored.code_challenge,
            redirect_uri=AnyUrl(stored.redirect_uri),
            redirect_uri_provided_explicitly=stored.redirect_uri_provided_explicitly,
            resource=stored.resource,
            subject=stored.user_id,
            email=stored.email,
        )

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: MixdAuthorizationCode,
    ) -> OAuthToken:
        consumed = await storage.consume_authorization_code(
            storage.token_hash(authorization_code.code)
        )
        if consumed is None:
            raise TokenError(
                error="invalid_grant",
                error_description="authorization code already used",
            )
        # RFC 8707: the token's audience comes from the *stored* code, and it
        # must be exactly the canonical MCP resource URI — a token for any
        # other audience must never be minted.
        if consumed.resource != settings.mcp_oauth.resource_uri:
            raise TokenError(
                error="invalid_target",
                error_description=(
                    f"resource must be {settings.mcp_oauth.resource_uri} (RFC 8707)"
                ),
            )
        return await self._mint_token_pair(
            client_id=consumed.client_id,
            user_id=consumed.user_id,
            email=consumed.email,
            scopes=tuple(consumed.scopes.split()),
            family_id=uuid7(),
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> MixdRefreshToken | None:
        stored = await storage.load_refresh_token(storage.token_hash(refresh_token))
        if stored is None:
            return None
        if stored.revoked_at is not None:
            # A rotated-out token coming back is replay evidence: kill the
            # whole family so the thief's live generation dies too.
            logger.warning(
                "refresh_token_replay_detected", family_id=str(stored.family_id)
            )
            await storage.revoke_refresh_family(stored.family_id)
            return None
        return MixdRefreshToken(
            token=refresh_token,
            client_id=stored.client_id,
            scopes=stored.scopes.split() if stored.scopes else [],
            expires_at=int(stored.expires_at.timestamp()),
            subject=stored.user_id,
            email=stored.email,
            family_id=str(stored.family_id),
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: MixdRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        return await self._mint_token_pair(
            client_id=refresh_token.client_id,
            user_id=refresh_token.subject or "",
            email=refresh_token.email,
            scopes=tuple(scopes),
            family_id=UUID(refresh_token.family_id),
            rotate_from=storage.token_hash(refresh_token.token),
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        try:
            claims = verify_access_token(token)
        except Exception:
            return None
        scope_claim = claims.get("scope", "")
        return AccessToken(
            token=token,
            client_id=claims.get("client_id", ""),
            scopes=scope_claim.split() if scope_claim else [],
            expires_at=claims.get("exp"),
            resource=claims.get("aud"),
            subject=claims.get("sub"),
        )

    async def revoke_token(self, token: AccessToken | MixdRefreshToken) -> None:
        if isinstance(token, MixdRefreshToken):
            await storage.delete_refresh_token(storage.token_hash(token.token))
        # Access tokens are stateless JWTs — they expire, they can't be
        # recalled; revocation of the refresh token stops renewal.

    async def exchange_identity_assertion(
        self,
        client: OAuthClientInformationFull,
        params: IdentityAssertionParams,
    ) -> OAuthToken:
        """SEP-990 ID-JAG grant — not supported (mirrors the SDK default).

        Present because the SDK's Protocol declares it, and structural
        matching requires the member on implementers even though the
        Protocol carries a default body.
        """
        raise TokenError(
            error="unsupported_grant_type",
            error_description=(
                "The JWT bearer grant is not supported by this authorization server"
            ),
        )

    async def _mint_token_pair(
        self,
        *,
        client_id: str,
        user_id: str,
        email: str,
        scopes: tuple[str, ...],
        family_id: UUID,
        rotate_from: str | None = None,
    ) -> OAuthToken:
        cfg = settings.mcp_oauth
        access_token = mint_access_token(
            sub=user_id, email=email, client_id=client_id, scopes=scopes
        )
        new_refresh = secrets.token_urlsafe(48)
        replacement = storage.StoredRefreshToken(
            token_hash=storage.token_hash(new_refresh),
            family_id=family_id,
            client_id=client_id,
            user_id=user_id,
            email=email,
            scopes=" ".join(scopes),
            expires_at=datetime.now(UTC)
            + timedelta(seconds=cfg.refresh_token_ttl_seconds),
            revoked_at=None,
        )
        if rotate_from is None:
            await storage.create_refresh_token(replacement)
        else:
            rotated = await storage.rotate_refresh_token(rotate_from, replacement)
            if not rotated:
                # Lost a rotation race — treat like replay: family dies.
                await storage.revoke_refresh_family(family_id)
                raise TokenError(
                    error="invalid_grant",
                    error_description="refresh token is no longer valid",
                )
        return OAuthToken(
            access_token=access_token,
            expires_in=cfg.access_token_ttl_seconds,
            scope=" ".join(scopes) if scopes else None,
            refresh_token=new_refresh,
        )


# --- consent-step helpers (called by the /api/v1/oauth consent router) --------


async def describe_consent_request(request_id: UUID) -> dict[str, object]:
    """The details the consent card renders. Raises when expired/unknown."""
    request = await storage.get_authorization_request(request_id)
    if request is None:
        raise ConsentRequestExpiredError
    params = AuthorizationParams.model_validate(request.params)
    return {
        "client_id": request.client_id,
        "client_name": request.client_name,
        "redirect_uri": str(params.redirect_uri),
        "scopes": params.scopes or [],
        "resource": params.resource,
    }


async def approve_consent_request(request_id: UUID, *, user_id: str, email: str) -> str:
    """Issue the authorization code for the consenting user; return redirect URL.

    The code is bound to the Neon-Auth-authenticated user who clicked
    Approve — the OAuth client never chooses whose data it gets.
    """
    request = await storage.get_authorization_request(request_id)
    if request is None:
        raise ConsentRequestExpiredError
    await storage.delete_authorization_request(request_id)

    params = AuthorizationParams.model_validate(request.params)
    code = secrets.token_urlsafe(48)
    await storage.create_authorization_code(
        storage.StoredAuthorizationCode(
            code_hash=storage.token_hash(code),
            client_id=request.client_id,
            user_id=user_id,
            email=email,
            scopes=" ".join(params.scopes or []),
            code_challenge=params.code_challenge,
            redirect_uri=str(params.redirect_uri),
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            expires_at=datetime.now(UTC)
            + timedelta(seconds=settings.mcp_oauth.authorization_code_ttl_seconds),
        )
    )
    logger.info("oauth_consent_approved", client_id=request.client_id, user_id=user_id)
    # RFC 9207 iss: lets the client detect a mix-up attack.
    return construct_redirect_uri(
        str(params.redirect_uri),
        code=code,
        state=params.state,
        iss=settings.mcp_oauth.issuer_url,
    )


async def deny_consent_request(request_id: UUID) -> str:
    """Consume the request and build the access_denied redirect."""
    request = await storage.get_authorization_request(request_id)
    if request is None:
        raise ConsentRequestExpiredError
    await storage.delete_authorization_request(request_id)
    params = AuthorizationParams.model_validate(request.params)
    logger.info("oauth_consent_denied", client_id=request.client_id)
    return construct_redirect_uri(
        str(params.redirect_uri),
        error="access_denied",
        error_description="The user denied the authorization request",
        state=params.state,
        iss=settings.mcp_oauth.issuer_url,
    )
