"""Apple Music developer token provider.

Apple Music authenticates server-side calls with a developer token — an
ES256 JWT signed with a MusicKit private key (the .p8 file's PEM content,
delivered via ``APPLE_PRIVATE_KEY``), not a per-user OAuth flow. The token
is minted locally, so there is no refresh endpoint and no token storage:
the provider caches the minted token in-process and re-mints when it comes
within a margin of expiry.

Reference: https://developer.apple.com/documentation/applemusicapi/generating_developer_tokens
"""

import collections.abc
from datetime import UTC, datetime, timedelta
from typing import Final, override

from attrs import define, field
import httpx2
import jwt

from src.config import get_logger, settings
from src.infrastructure.connectors._shared.token_storage import TokenStorage

logger = get_logger(__name__).bind(service="apple_music_auth")

# Token lifetime — starting point, revisit. Apple caps developer tokens at
# 6 months; 90 days keeps comfortable headroom under that.
_DEVELOPER_TOKEN_TTL: Final = timedelta(days=90)

# Re-mint once the cached token is within this margin of its expiry.
_REMINT_MARGIN: Final = timedelta(days=1)


@define(slots=True)
class DeveloperTokenProvider:
    """Mints and caches the Apple Music developer token (ES256 JWT).

    Credentials come from ``settings.credentials`` (instance-level, not
    per-user): ``apple_team_id`` (iss claim), ``apple_key_id`` (kid header),
    and ``apple_private_key`` (the signing key). The optional
    ``apple_music_origin`` restricts where the token is accepted from via
    the ``origin`` claim (an array, per Apple's format).

    Example:
        >>> provider = DeveloperTokenProvider()
        >>> token = provider.get_token()
    """

    # repr=False on both: the token is a signed credential, and neither
    # cache field may ever surface in logs or debugger output.
    _cached_token: str | None = field(default=None, init=False, repr=False)
    _cached_exp: datetime | None = field(default=None, init=False, repr=False)

    def get_token(self) -> str:
        """Return the developer token, re-minting within the expiry margin.

        Raises:
            RuntimeError: If the Apple Music credentials are missing or
                partially configured.
        """
        now = datetime.now(UTC)
        if (
            self._cached_token is not None
            and self._cached_exp is not None
            and now < self._cached_exp - _REMINT_MARGIN
        ):
            return self._cached_token
        return self._mint(now)

    def _mint(self, now: datetime) -> str:
        """Sign a fresh developer token and cache it."""
        credentials = settings.credentials
        # The SecretStr is unwrapped only here, passed straight to jwt.encode,
        # and never stored on the provider or logged.
        private_key = credentials.apple_private_key.get_secret_value()
        missing = [
            name
            for name, value in (
                ("APPLE_TEAM_ID", credentials.apple_team_id),
                ("APPLE_KEY_ID", credentials.apple_key_id),
                ("APPLE_PRIVATE_KEY", private_key),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "Apple Music is not configured — missing "
                f"{', '.join(missing)}. Set APPLE_TEAM_ID, APPLE_KEY_ID, and "
                "APPLE_PRIVATE_KEY (the .p8 file's PEM content)."
            )

        exp = now + _DEVELOPER_TOKEN_TTL
        claims: dict[str, object] = {
            "iss": credentials.apple_team_id,
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
        }
        if credentials.apple_music_origin:
            claims["origin"] = [credentials.apple_music_origin]

        token = jwt.encode(
            claims,
            private_key,
            algorithm="ES256",
            headers={"kid": credentials.apple_key_id},
        )
        self._cached_token = token
        self._cached_exp = exp
        logger.debug("Apple Music developer token minted", expires_at=exp.isoformat())
        return token


# -------------------------------------------------------------------------
# HTTPX2 AUTH FLOW
# -------------------------------------------------------------------------


class AppleMusicDeveloperAuth(httpx2.Auth):
    """httpx2 auth flow: injects the developer-token bearer on every request.

    Deliberately simpler than ``SpotifyBearerAuth`` — the developer token is
    minted locally (no I/O, so a sync flow serves both sync and async clients)
    and there is no refresh endpoint, so a 401 has no retry leg: it means the
    signing credentials are wrong and the client surfaces it as an auth error.
    The per-user Music-User-Token header is NOT this flow's job; the API
    client injects it per-call on ``/v1/me/*`` requests.
    """

    _token_provider: DeveloperTokenProvider

    def __init__(self, token_provider: DeveloperTokenProvider) -> None:
        self._token_provider = token_provider

    @override
    def auth_flow(
        self, request: httpx2.Request
    ) -> collections.abc.Generator[httpx2.Request, httpx2.Response]:
        request.headers["Authorization"] = f"Bearer {self._token_provider.get_token()}"
        yield request


# -------------------------------------------------------------------------
# POST-CONNECT STOREFRONT BACKFILL
# -------------------------------------------------------------------------


async def backfill_storefront(storage: TokenStorage, user_id: str) -> None:
    """Best-effort storefront record after a MusicKit connect.

    Fetches the user's storefront id with the freshly stored MUT and merges
    it into the token's ``extra_data`` via the narrow ``update_extra_data``
    write (mirroring the Spotify profile backfill — never a full-token
    upsert). Any *fetch* failure — Apple down, credentials unconfigured,
    token rejected — logs and returns; it must never fail the connect.
    """
    storefront = await _fetch_storefront(user_id)
    if storefront is not None:
        await storage.update_extra_data(
            "apple_music", user_id, {"storefront": storefront}
        )


async def _fetch_storefront(user_id: str) -> str | None:
    """Best-effort storefront id lookup; any failure logs and returns None."""
    try:
        return await _fetch_storefront_impl(user_id)
    except Exception:
        logger.warning(
            "Apple Music storefront fetch failed after connect", exc_info=True
        )
        return None


async def _fetch_storefront_impl(user_id: str) -> str | None:
    """Fetch the storefront id via the API client, closing its pool after.

    The client is constructed under ``user_context`` so it binds to the
    authenticated user (it reads the MUT back from token storage). Lazy
    imports: ``client.py`` imports this module, so a top-level client
    import would be circular.
    """
    from src.infrastructure.connectors.apple_music.client import AppleMusicAPIClient
    from src.infrastructure.persistence.database.user_context import user_context

    with user_context(user_id):
        client = AppleMusicAPIClient()
    try:
        storefront = await client.get_storefront()
    finally:
        await client.aclose()
    return storefront.id if storefront else None
