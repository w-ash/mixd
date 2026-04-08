"""Database-backed token storage for hosted deployment.

Uses standalone get_session() (not UoW) because token refresh happens inside
httpx auth flows (SpotifyBearerAuth.async_auth_flow) which have no UoW context.
Each operation is a single-row read/upsert — no multi-table transaction needed.

All methods require ``user_id`` for per-user token isolation (v0.6.3).
Each operation wraps its session in ``user_context(user_id)`` so the RLS
``after_begin`` event handler sets ``SET LOCAL app.user_id`` as defense-in-depth.
"""

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.config import get_logger
from src.infrastructure.connectors._shared.token_storage import StoredToken
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
        token["extra_data"] = row.extra_data
    return token


class DatabaseTokenStorage:
    """Database-backed token storage for hosted deployment.

    Creates its own short-lived session for each operation because token
    operations happen outside the UoW lifecycle (e.g., inside httpx auth flows).
    """

    async def load_token(self, service: str, user_id: str) -> StoredToken | None:
        with user_context(user_id):
            async with get_session() as session:
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
        now = datetime.now(UTC)
        values = {
            "service": service,
            "user_id": user_id,
            "token_type": token_data.get("token_type", "oauth2"),
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

        with user_context(user_id):
            async with get_session() as session:
                await session.execute(stmt)

    async def delete_token(self, service: str, user_id: str) -> None:
        with user_context(user_id):
            async with get_session() as session:
                await session.execute(
                    delete(DBOAuthToken).where(
                        DBOAuthToken.service == service,
                        DBOAuthToken.user_id == user_id,
                    )
                )
