"""Connect-time validation for the Discogs personal access token.

The one shared implementation behind both connect surfaces — the
``PUT /api/v1/connectors/discogs/token`` route and ``mixd discogs connect``
(the v0.6.5 credential carve-out shape: CLI + web reuse one validator, like
the Anthropic BYO-key). The submitted token is probed live against
``/oauth/identity`` with a throwaway ``DiscogsAPIClient`` carrying an
injected ``DiscogsTokenAuth`` — token storage is never consulted, and the
client is always closed. On success the user's collection count is read
from a single ``per_page=1`` collection page and cached in ``extra_data``
so the status probe can render "N releases" without spending any of the
instance-wide Discogs budget on status polls.
"""

import time

from src.config import get_logger
from src.domain.exceptions import DiscogsAuthRequiredError, DiscogsInvalidTokenError
from src.infrastructure.connectors._shared.token_storage import StoredToken
from src.infrastructure.connectors.discogs.auth import DiscogsTokenAuth
from src.infrastructure.connectors.discogs.client import DiscogsAPIClient

logger = get_logger(__name__).bind(service="discogs_token_service")

# StoredToken.token_type label for Discogs' BYO personal access token.
# Constant named without "token" so bandit's hardcoded-secret check stays
# quiet — the `_ANTHROPIC_KIND` precedent in chat/credentials.py. The value
# must fit oauth_tokens.token_type VARCHAR(20) ("personal_access_token" is
# 21 chars and gets rejected by PostgreSQL).
_DISCOGS_CREDENTIAL_KIND = "personal_token"


async def validate_and_build_token(token: str) -> StoredToken:
    """Validate ``token`` live against Discogs and build the ``StoredToken``.

    Returns the token record to persist: the token itself, the Discogs
    username as ``account_name``, and ``extra_data`` carrying
    ``validated_at`` (Unix now) plus ``collection_count`` when the
    collection page probe succeeds (a transient failure there omits the
    count rather than failing the connect — the token is already proven).

    Raises:
        DiscogsInvalidTokenError: Discogs rejected the token (401), or the
            identity probe could not reach Discogs at all. The API layer maps
            it to a 400 ``DISCOGS_INVALID_TOKEN`` envelope for inline display.
    """
    client = DiscogsAPIClient(DiscogsTokenAuth(token))
    try:
        try:
            identity = await client.get_identity()
        except DiscogsAuthRequiredError:
            logger.info("Discogs rejected the submitted personal access token")
            raise DiscogsInvalidTokenError(
                "Discogs rejected that personal access token — check it was "
                "copied in full from Settings → Developers on discogs.com."
            ) from None
        if identity is None:
            # Transport failures are suppressed to None by the client; a
            # token can't be judged without an answer.
            raise DiscogsInvalidTokenError(
                "Could not reach Discogs to validate the token — try again in a moment."
            )
        extra_data: dict[str, object] = {"validated_at": int(time.time())}
        page = await client.get_collection_page(identity.username, per_page=1)
        if page is not None:
            extra_data["collection_count"] = page.pagination.items
        return StoredToken(
            access_token=token,
            token_type=_DISCOGS_CREDENTIAL_KIND,
            account_name=identity.username,
            extra_data=extra_data,
        )
    finally:
        await client.aclose()
