# pyright: reportAttributeAccessIssue=false
# Legitimate: CursorResult.rowcount is valid but invisible to pyright through generic Result[Any]
"""Prune expired OAuth CSRF state rows from the database.

Called from FastAPI lifespan on startup. The oauth_states table uses a 5-minute
TTL, but rows are only pruned lazily during new state creation. This handles
cleanup of states that expired while the server was down.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.logging import get_logger

logger = get_logger(__name__)


async def prune_expired_oauth_states(session: AsyncSession) -> int:
    """Delete expired OAuth state rows. Returns count of pruned rows."""
    from datetime import UTC, datetime

    from sqlalchemy import delete

    from src.infrastructure.persistence.database.db_models import DBOAuthState

    result = await session.execute(
        delete(DBOAuthState).where(DBOAuthState.expires_at < datetime.now(UTC))
    )
    count = result.rowcount
    if count:
        logger.info("Pruned expired OAuth states", count=count)
    return count
