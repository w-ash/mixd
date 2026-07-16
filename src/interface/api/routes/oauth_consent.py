"""Consent API for the in-app OAuth authorization server (v0.9.5).

Mounted under ``/api/v1`` so ``NeonAuthMiddleware`` gates it on the user's
existing web session — the one place in the OAuth flow where a *user* (not an
OAuth client) acts. The React consent page fetches the request summary,
renders Approve/Deny, and navigates to whichever ``redirect_url`` comes back;
the issued code is bound server-side to the session user, so the OAuth client
never chooses whose data it gets.

Direct use of the oauth provider helpers (not ``execute_use_case``) follows
the v0.6.5 credential carve-out — this is credential machinery, not domain
data.
"""

from typing import cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from src.domain.exceptions import NotFoundError
from src.interface.api.auth_gate import JWTClaims
from src.interface.api.deps import get_current_user_id
from src.interface.api.oauth.provider import (
    ConsentRequestExpiredError,
    approve_consent_request,
    deny_consent_request,
    describe_consent_request,
)

router = APIRouter(prefix="/oauth/consent", tags=["oauth"])

_EXPIRED_MESSAGE = (
    "This authorization request has expired or was already decided. "
    "Retry the connection from your MCP client."
)


class ConsentDetails(BaseModel):
    client_id: str
    client_name: str | None
    redirect_uri: str
    scopes: list[str]
    resource: str | None


class ConsentRedirect(BaseModel):
    redirect_url: str


def _session_email(request: Request) -> str:
    raw_claims = request.scope.get("auth_user")
    if isinstance(raw_claims, dict):
        return cast("JWTClaims", raw_claims).get("email", "")
    return ""


@router.get("/{request_id}")
async def get_consent_details(request_id: UUID) -> ConsentDetails:
    """What the consent card shows: who is asking, where the code will go."""
    try:
        details = await describe_consent_request(request_id)
    except ConsentRequestExpiredError as err:
        raise NotFoundError(_EXPIRED_MESSAGE) from err
    return ConsentDetails.model_validate(details)


@router.post("/{request_id}/approve")
async def approve_consent(
    request_id: UUID,
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> ConsentRedirect:
    """Issue the authorization code for the session user; return the redirect."""
    try:
        redirect_url = await approve_consent_request(
            request_id, user_id=user_id, email=_session_email(request)
        )
    except ConsentRequestExpiredError as err:
        raise NotFoundError(_EXPIRED_MESSAGE) from err
    return ConsentRedirect(redirect_url=redirect_url)


@router.post("/{request_id}/deny")
async def deny_consent(request_id: UUID) -> ConsentRedirect:
    """Consume the request and return the access_denied redirect."""
    try:
        redirect_url = await deny_consent_request(request_id)
    except ConsentRequestExpiredError as err:
        raise NotFoundError(_EXPIRED_MESSAGE) from err
    return ConsentRedirect(redirect_url=redirect_url)
