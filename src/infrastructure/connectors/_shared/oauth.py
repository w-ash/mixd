"""Shared OAuth building blocks for connector auth modules.

The provider-agnostic pieces Spotify and Tidal both need: S256 PKCE
challenge computation (RFC 7636), the ``invalid_grant`` refresh-rejection
probe, and the bearer-injection httpx2 auth flow with its
one-forced-refresh-then-replay 401 recovery. ``build_auth_url`` stays
per-connector — the authorize-request param dicts differ enough that a
shared assembler would hide what each provider actually sends.
"""

import base64
import collections.abc
import hashlib
from http import HTTPStatus
from typing import Protocol, override

import httpx2

from src.infrastructure.connectors._shared.http_client import parse_json_body


def compute_pkce_challenge(code_verifier: str) -> str:
    """Compute S256 PKCE code_challenge from a code_verifier (RFC 7636)."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def is_invalid_grant(response: httpx2.Response) -> bool:
    """True if a refresh-POST body carries ``error: "invalid_grant"``.

    A body that doesn't parse as a JSON object reads as "not invalid_grant"
    — it falls through to the plain HTTPStatusError path rather than raising.
    """
    body = parse_json_body(response)
    return body is not None and body.get("error") == "invalid_grant"


class RefreshableTokenManager(Protocol):
    """What :class:`BearerAuth` needs from a connector's token manager."""

    async def get_valid_token(self) -> str:
        """Return a valid access token, refreshing proactively if needed."""
        ...

    async def force_refresh(self) -> str:
        """Force-refresh after a 401, returning the fresh access token."""
        ...


class BearerAuth(httpx2.Auth):
    """httpx2 async auth flow: injects Bearer token and retries once on 401.

    Used with a long-lived AsyncClient so token injection and the
    one-forced-refresh-then-replay are handled transparently without
    per-call boilerplate. Connectors subclass this with their own token
    manager type (``SpotifyBearerAuth``, ``TidalBearerAuth``).
    """

    _token_manager: RefreshableTokenManager

    def __init__(self, token_manager: RefreshableTokenManager) -> None:
        self._token_manager = token_manager

    @override
    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> collections.abc.AsyncGenerator[httpx2.Request, httpx2.Response]:
        token = await self._token_manager.get_valid_token()
        request.headers["Authorization"] = f"Bearer {token}"
        response = yield request

        if response.status_code == HTTPStatus.UNAUTHORIZED:
            new_token = await self._token_manager.force_refresh()
            request.headers["Authorization"] = f"Bearer {new_token}"
            yield request
