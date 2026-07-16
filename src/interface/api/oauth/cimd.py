"""Client-ID-Metadata-Document (CIMD) resolution for the in-app OAuth AS.

CIMD is the 2026-07-28 MCP spec's preferred client-registration mechanism:
the ``client_id`` IS an https URL, and the AS fetches a JSON metadata
document from it instead of holding a registration. Anthropic clients
(Claude Code/Desktop/web) use CIMD whenever the AS advertises support;
server-side resolution is unimplemented in the ``mcp`` SDK, so this module
owns it.

Security posture: the URL is attacker-chosen input, so the fetch is
SSRF-guarded (https-only, public-IP-only after resolution, no redirects,
size/time caps). The fetched document grants no authority by itself — a
token is only ever minted after the allowlist-gated user consents — so the
residual DNS-rebinding TOCTOU here is a scan primitive at worst.

Claude Code redirects to a port-agnostic localhost loopback; the SDK's
``validate_redirect_uri`` is exact-match, so ``CIMDClient`` overrides it with
RFC 8252 §7.3.3 loopback semantics (any port, same scheme/host/path).
"""

from datetime import UTC, datetime, timedelta
import ipaddress
import json
import socket
from typing import cast, override
from urllib.parse import urlparse

import httpx
from mcp.shared.auth import (
    InvalidRedirectUriError,
    OAuthClientInformationFull,
    OAuthClientMetadata,
)
from pydantic import AnyUrl, ValidationError

from src.config import get_logger
from src.domain.entities.shared import JsonDict
from src.infrastructure.persistence.repositories.oauth_as import (
    get_client,
    upsert_client,
)

logger = get_logger(__name__)

_CACHE_TTL = timedelta(hours=1)
_FETCH_TIMEOUT_SECONDS = 10
_MAX_DOCUMENT_BYTES = 64 * 1024
_HTTP_OK = 200
_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
# CIMD clients are public by definition — there is no registration step that
# could have issued a secret.
_PUBLIC_CLIENT_AUTH_METHOD = "none"


class CIMDResolutionError(Exception):
    """The client_id URL could not be resolved to valid client metadata."""


def is_cimd_client_id(client_id: str) -> bool:
    """URL-shaped client ids are CIMD; anything else is a DCR registration."""
    return client_id.startswith("https://")


class CIMDClient(OAuthClientInformationFull):
    """Client info with RFC 8252 §7.3.3 loopback redirect matching.

    Exact match first (the SDK base behavior); for loopback redirect URIs
    the port is ignored — a native client binds an ephemeral port per run,
    so the registered ``http://localhost/callback`` must match
    ``http://localhost:53682/callback``.
    """

    @override
    def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
        try:
            return super().validate_redirect_uri(redirect_uri)
        except InvalidRedirectUriError:
            if redirect_uri is not None and self._matches_loopback(redirect_uri):
                return redirect_uri
            raise

    def _matches_loopback(self, redirect_uri: AnyUrl) -> bool:
        requested = urlparse(str(redirect_uri))
        if requested.hostname not in _LOOPBACK_HOSTS:
            return False
        for registered in self.redirect_uris or []:
            reg = urlparse(str(registered))
            if (
                reg.hostname in _LOOPBACK_HOSTS
                and reg.scheme == requested.scheme
                and (reg.path or "/") == (requested.path or "/")
            ):
                return True
        return False


def _assert_public_https(url: str) -> None:
    """Reject non-https URLs and hosts that resolve to non-public addresses."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise CIMDResolutionError("client_id metadata URL must be https")
    host = parsed.hostname
    if not host:
        raise CIMDResolutionError("client_id metadata URL has no host")
    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as err:
        raise CIMDResolutionError(f"client_id host does not resolve: {host}") from err
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise CIMDResolutionError("client_id host resolves to a non-public address")


async def resolve_cimd_client(client_id: str) -> CIMDClient:
    """Fetch (or serve from cache) the metadata document behind a CIMD id.

    Raises ``CIMDResolutionError`` on any validation failure — the caller
    maps that to "client not found" so the authorize endpoint answers with a
    clean OAuth error instead of a 500.
    """
    cached = await get_client(client_id)
    if (
        cached is not None
        and cached.kind == "cimd"
        and datetime.now(UTC) - cached.updated_at < _CACHE_TTL
    ):
        return _to_client(client_id, cached.client_info)

    _assert_public_https(client_id)
    try:
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=_FETCH_TIMEOUT_SECONDS
        ) as http:
            response = await http.get(client_id, headers={"Accept": "application/json"})
    except httpx.HTTPError as err:
        raise CIMDResolutionError(f"metadata fetch failed: {err}") from err
    if response.status_code != _HTTP_OK:
        raise CIMDResolutionError(f"metadata fetch returned {response.status_code}")
    if len(response.content) > _MAX_DOCUMENT_BYTES:
        raise CIMDResolutionError("metadata document too large")

    try:
        parsed = cast("object", json.loads(response.content))
    except ValueError as err:
        raise CIMDResolutionError("metadata document is not JSON") from err
    if not isinstance(parsed, dict):
        raise CIMDResolutionError("metadata document must be a JSON object")
    document = cast("JsonDict", parsed)

    # CIMD invariant: the document must claim the URL it is served from.
    if document.get("client_id") != client_id:
        raise CIMDResolutionError("metadata document client_id must equal its own URL")

    client = _to_client(client_id, document)
    await upsert_client(client_id, "cimd", document)
    logger.info("cimd_client_resolved", client_id=client_id)
    return client


def _to_client(client_id: str, document: JsonDict) -> CIMDClient:
    try:
        metadata = OAuthClientMetadata.model_validate({
            k: v for k, v in document.items() if k != "client_id"
        })
    except ValidationError as err:
        raise CIMDResolutionError(f"invalid client metadata: {err}") from err
    data: dict[str, object] = {"client_id": client_id}
    data.update(cast("dict[str, object]", metadata.model_dump()))
    data["token_endpoint_auth_method"] = _PUBLIC_CLIENT_AUTH_METHOD
    return CIMDClient.model_validate(data)
