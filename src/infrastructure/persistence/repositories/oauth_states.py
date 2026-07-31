"""Storage helper for connector-OAuth CSRF state rows (``oauth_states``).

Sibling to ``oauth_as.py`` but a different subsystem: that module is the
in-app *authorization server* (clients, codes, refresh tokens, v0.9.5); this
one is the CSRF/PKCE state for outbound **connector** OAuth flows (Spotify,
Last.fm), the v0.6.5 credential carve-out that ``interface/api/routes/auth.py``
already reaches directly.

Same house pattern as ``oauth_as.py``: the helper opens its own short session
(``get_session`` commits on exit). The table carries no RLS — the unguessable
state token is the access control — so no ``user_context`` is involved.
"""

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import delete
from sqlalchemy.engine import CursorResult

from src.config.logging import get_logger
from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.database.db_models import DBOAuthState

logger = get_logger(__name__)


async def prune_expired_states() -> int:
    """Delete expired OAuth state rows. Returns count of pruned rows.

    Rows are normally evicted opportunistically when the next state is
    created (``routes/auth.py``), so this exists for the states that expired
    while the server was down — it runs once from the FastAPI lifespan.
    """
    async with get_session() as session:
        result = await session.execute(
            delete(DBOAuthState).where(DBOAuthState.expires_at < datetime.now(UTC))
        )
        count = cast("CursorResult[object]", result).rowcount

    if count:
        logger.info("Pruned expired OAuth states", count=count)
    return count


__all__ = ["prune_expired_states"]
