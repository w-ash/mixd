"""Database-backed token storage for hosted deployment.

Uses standalone get_session() (not UoW) because token refresh happens inside
httpx2 auth flows (SpotifyBearerAuth.async_auth_flow) which have no UoW context.
Each operation is a single-row read/upsert — no multi-table transaction needed.

All methods require ``user_id`` for per-user token isolation (v0.6.3).
Each operation wraps its session in ``user_context(user_id)`` so the RLS
``after_begin`` event handler sets ``SET LOCAL app.user_id`` as defense-in-depth.
"""

from collections.abc import Collection, Mapping
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import cast as sa_cast, delete, func, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.infrastructure.connectors._shared.token_storage import (
    OAUTH2_CREDENTIAL_KIND,
    StoredToken,
)
from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.database.db_models import DBOAuthToken
from src.infrastructure.persistence.database.user_context import user_context
from src.infrastructure.persistence.repositories.token_encryption import (
    SENSITIVE_FIELDS,
    decrypt_field,
    encrypt_field,
)

logger = get_logger(__name__)


def _unix_to_datetime(ts: int | None) -> datetime | None:
    """Convert Unix timestamp to timezone-aware datetime."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=UTC)


def _row_to_stored_token(row: DBOAuthToken) -> StoredToken:
    """Convert a database row to a StoredToken dict, decrypting sensitive fields."""
    token: StoredToken = {}
    for field_name in SENSITIVE_FIELDS:
        raw = cast(str | None, getattr(row, field_name))
        if raw:
            decrypted = decrypt_field(raw)
            if decrypted:
                token[field_name] = decrypted
    if row.token_type:
        token["token_type"] = row.token_type
    if row.expires_at:
        token["expires_at"] = int(row.expires_at.timestamp())
    if row.scope:
        token["scope"] = row.scope
    if row.account_name:
        token["account_name"] = row.account_name
    if row.extra_data:
        # JsonDict → dict[str, object]: invariance requires cast, but the data
        # is never mutated (read-only token load path). No copy needed.
        token["extra_data"] = cast("dict[str, object]", row.extra_data)
    return token


class DatabaseTokenStorage:
    """Database-backed token storage for hosted deployment.

    Creates its own short-lived session for each operation because token
    operations happen outside the UoW lifecycle (e.g., inside httpx2 auth flows).
    """

    async def load_token(self, service: str, user_id: str) -> StoredToken | None:
        with user_context(user_id):
            async with get_session() as session:
                return await self.load_with_session(session, service, user_id)

    async def load_tokens(
        self, services: Collection[str], user_id: str
    ) -> Mapping[str, StoredToken | None]:
        """Load one user's tokens for ``services`` in a single query.

        Same ``user_context`` + ``user_id`` predicate as ``load_token``, so RLS
        and the explicit scoping match row for row. Every requested service is
        present in the result; one with no stored row maps to ``None``.
        """
        tokens: dict[str, StoredToken | None] = dict.fromkeys(services)
        if not tokens:
            return tokens
        with user_context(user_id):
            async with get_session() as session:
                result = await session.execute(
                    select(DBOAuthToken).where(
                        DBOAuthToken.user_id == user_id,
                        DBOAuthToken.service.in_(list(tokens)),
                    )
                )
                for row in result.scalars():
                    tokens[row.service] = _row_to_stored_token(row)
        return tokens

    async def load_with_session(
        self, session: AsyncSession, service: str, user_id: str
    ) -> StoredToken | None:
        """Read one stored token through a caller-owned session.

        Internal seam for callers that must read inside an open transaction —
        the single-flight refresh guard (``token_refresh_lock``) re-reads the
        token under its advisory lock. The caller owns transaction scope and
        ``user_context``.
        """
        result = await session.execute(
            select(DBOAuthToken).where(
                DBOAuthToken.service == service,
                DBOAuthToken.user_id == user_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return _row_to_stored_token(row)

    async def save_token(
        self, service: str, user_id: str, token_data: StoredToken
    ) -> None:
        with user_context(user_id):
            async with get_session() as session:
                await self.save_with_session(session, service, user_id, token_data)

    async def save_with_session(
        self,
        session: AsyncSession,
        service: str,
        user_id: str,
        token_data: StoredToken,
    ) -> None:
        """Upsert one stored token through a caller-owned session.

        Internal seam for the single-flight refresh guard: the rotated token
        must land in the same transaction that holds the advisory lock. The
        caller owns transaction scope and ``user_context``. Sensitive fields
        are encrypted here, exactly as in ``save_token``.
        """
        now = datetime.now(UTC)
        values = {
            "service": service,
            "user_id": user_id,
            "token_type": token_data.get("token_type", OAUTH2_CREDENTIAL_KIND),
            "access_token": encrypt_field(token_data.get("access_token")),
            "refresh_token": encrypt_field(token_data.get("refresh_token")),
            "session_key": encrypt_field(token_data.get("session_key")),
            "expires_at": _unix_to_datetime(token_data.get("expires_at")),
            "scope": token_data.get("scope"),
            "account_name": token_data.get("account_name"),
            "extra_data": token_data.get("extra_data", {}),
            "updated_at": now,
        }

        # Set created_at only on insert
        insert_values = {**values, "created_at": now}

        update_cols = {
            k: v for k, v in values.items() if k not in ("service", "user_id")
        }

        stmt = (
            pg_insert(DBOAuthToken)
            .values(**insert_values)
            .on_conflict_do_update(
                index_elements=["user_id", "service"],
                set_=update_cols,
            )
        )

        await session.execute(stmt)

    async def update_extra_data(
        self,
        service: str,
        user_id: str,
        updates: Mapping[str, object],
        *,
        account_name: str | None = None,
    ) -> None:
        """Merge ``updates`` into ``extra_data`` without touching token columns.

        The narrow alternative to load→mutate→``save_token`` for cache-style
        writes (favorites/collection counts, reauth markers, storefront,
        account-id backfill): a single SQL UPDATE with a jsonb merge, so a
        concurrent refresh rotation can never be overwritten by a stale
        loaded token (which would resurrect a dead refresh token and kill
        the rotated grant). ``account_name`` rides the same UPDATE when a
        caller has a display name to backfill — still never a token column.
        No-op when no token row exists (a disconnect raced the caller).
        """
        values: dict[str, object] = {
            "extra_data": func.coalesce(DBOAuthToken.extra_data, sa_cast({}, JSONB)).op(
                "||"
            )(sa_cast(dict(updates), JSONB)),
            "updated_at": datetime.now(UTC),
        }
        if account_name is not None:
            values["account_name"] = account_name
        with user_context(user_id):
            async with get_session() as session:
                _ = await session.execute(
                    update(DBOAuthToken)
                    .where(
                        DBOAuthToken.service == service,
                        DBOAuthToken.user_id == user_id,
                    )
                    .values(**values)
                )

    async def delete_token(self, service: str, user_id: str) -> None:
        with user_context(user_id):
            async with get_session() as session:
                await session.execute(
                    delete(DBOAuthToken).where(
                        DBOAuthToken.service == service,
                        DBOAuthToken.user_id == user_id,
                    )
                )
