"""Storage helpers for the in-app OAuth 2.1 authorization server (v0.9.5).

Consumed directly by ``src/interface/api/oauth/`` — the same
infrastructure-helper carve-out as connector OAuth (``TokenStorage``) and the
assistant key (``infrastructure/chat/credentials``): AS token machinery is a
credential surface, not domain data, so it deliberately bypasses
``execute_use_case()``. Each helper opens its own short session
(``get_session`` commits on exit); the AS tables carry no RLS (migration 039),
so no ``user_context`` is involved — the /token endpoint has no session user.

Codes and refresh tokens are stored hashed (SHA-256): a database leak must
not yield redeemable credentials. Rotation keeps the revoked generation and
its ``family_id`` so a replayed old token is *evidence* — it deletes the whole
family instead of failing silently.
"""

from datetime import UTC, datetime, timedelta
import hashlib
from uuid import UUID, uuid7

from attrs import define
from sqlalchemy import delete, select, update

from src.domain.entities.shared import JsonDict
from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.database.db_models import (
    DBOAuthAuthorizationCode,
    DBOAuthAuthorizationRequest,
    DBOAuthClient,
    DBOAuthRefreshToken,
)

_REQUEST_TTL = timedelta(minutes=15)


def token_hash(value: str) -> str:
    """SHA-256 hex digest — the at-rest form of codes and refresh tokens."""
    return hashlib.sha256(value.encode()).hexdigest()


@define(frozen=True, slots=True)
class StoredClient:
    client_id: str
    kind: str
    client_info: JsonDict
    updated_at: datetime


@define(frozen=True, slots=True)
class StoredAuthorizationRequest:
    request_id: UUID
    client_id: str
    client_name: str | None
    params: JsonDict
    created_at: datetime


@define(frozen=True, slots=True)
class StoredAuthorizationCode:
    code_hash: str
    client_id: str
    user_id: str
    email: str
    scopes: str
    code_challenge: str
    redirect_uri: str
    redirect_uri_provided_explicitly: bool
    resource: str | None
    expires_at: datetime


@define(frozen=True, slots=True)
class StoredRefreshToken:
    token_hash: str
    family_id: UUID
    client_id: str
    user_id: str
    email: str
    scopes: str
    expires_at: datetime
    revoked_at: datetime | None


# --- clients -----------------------------------------------------------------


async def get_client(client_id: str) -> StoredClient | None:
    async with get_session() as session:
        row = await session.scalar(
            select(DBOAuthClient).where(DBOAuthClient.client_id == client_id)
        )
        if row is None:
            return None
        return StoredClient(
            client_id=row.client_id,
            kind=row.kind,
            client_info=row.client_info,
            updated_at=row.updated_at,
        )


async def upsert_client(client_id: str, kind: str, client_info: JsonDict) -> None:
    async with get_session() as session:
        existing = await session.scalar(
            select(DBOAuthClient).where(DBOAuthClient.client_id == client_id)
        )
        if existing is None:
            session.add(
                DBOAuthClient(client_id=client_id, kind=kind, client_info=client_info)
            )
        else:
            existing.client_info = client_info
            existing.kind = kind


# --- authorization requests (pending consent) --------------------------------


async def create_authorization_request(
    client_id: str, client_name: str | None, params: JsonDict
) -> UUID:
    request_id = uuid7()
    now = datetime.now(UTC)
    async with get_session() as session:
        await session.execute(
            delete(DBOAuthAuthorizationRequest).where(
                DBOAuthAuthorizationRequest.created_at < now - _REQUEST_TTL
            )
        )
        session.add(
            DBOAuthAuthorizationRequest(
                id=request_id,
                client_id=client_id,
                client_name=client_name,
                params=params,
            )
        )
    return request_id


async def get_authorization_request(
    request_id: UUID,
) -> StoredAuthorizationRequest | None:
    cutoff = datetime.now(UTC) - _REQUEST_TTL
    async with get_session() as session:
        row = await session.scalar(
            select(DBOAuthAuthorizationRequest).where(
                DBOAuthAuthorizationRequest.id == request_id,
                DBOAuthAuthorizationRequest.created_at >= cutoff,
            )
        )
        if row is None:
            return None
        return StoredAuthorizationRequest(
            request_id=row.id,
            client_id=row.client_id,
            client_name=row.client_name,
            params=row.params,
            created_at=row.created_at,
        )


async def delete_authorization_request(request_id: UUID) -> None:
    async with get_session() as session:
        await session.execute(
            delete(DBOAuthAuthorizationRequest).where(
                DBOAuthAuthorizationRequest.id == request_id
            )
        )


# --- authorization codes ------------------------------------------------------


async def create_authorization_code(code: StoredAuthorizationCode) -> None:
    async with get_session() as session:
        session.add(
            DBOAuthAuthorizationCode(
                code_hash=code.code_hash,
                client_id=code.client_id,
                user_id=code.user_id,
                email=code.email,
                scopes=code.scopes,
                code_challenge=code.code_challenge,
                redirect_uri=code.redirect_uri,
                redirect_uri_provided_explicitly=code.redirect_uri_provided_explicitly,
                resource=code.resource,
                expires_at=code.expires_at,
            )
        )


def _code_from_row(row: DBOAuthAuthorizationCode) -> StoredAuthorizationCode:
    return StoredAuthorizationCode(
        code_hash=row.code_hash,
        client_id=row.client_id,
        user_id=row.user_id,
        email=row.email,
        scopes=row.scopes,
        code_challenge=row.code_challenge,
        redirect_uri=row.redirect_uri,
        redirect_uri_provided_explicitly=row.redirect_uri_provided_explicitly,
        resource=row.resource,
        expires_at=row.expires_at,
    )


async def load_authorization_code(code_hash: str) -> StoredAuthorizationCode | None:
    async with get_session() as session:
        row = await session.scalar(
            select(DBOAuthAuthorizationCode).where(
                DBOAuthAuthorizationCode.code_hash == code_hash
            )
        )
        return None if row is None else _code_from_row(row)


async def consume_authorization_code(code_hash: str) -> StoredAuthorizationCode | None:
    """Atomically claim a code — exactly one /token call can win it."""
    async with get_session() as session:
        row = await session.scalar(
            delete(DBOAuthAuthorizationCode)
            .where(DBOAuthAuthorizationCode.code_hash == code_hash)
            .returning(DBOAuthAuthorizationCode)
            .execution_options(synchronize_session=False)
        )
        return None if row is None else _code_from_row(row)


# --- refresh tokens -----------------------------------------------------------


async def create_refresh_token(token: StoredRefreshToken) -> None:
    async with get_session() as session:
        session.add(_refresh_row(token))


def _refresh_row(token: StoredRefreshToken) -> DBOAuthRefreshToken:
    return DBOAuthRefreshToken(
        token_hash=token.token_hash,
        family_id=token.family_id,
        client_id=token.client_id,
        user_id=token.user_id,
        email=token.email,
        scopes=token.scopes,
        expires_at=token.expires_at,
        revoked_at=token.revoked_at,
    )


def _refresh_from_row(row: DBOAuthRefreshToken) -> StoredRefreshToken:
    return StoredRefreshToken(
        token_hash=row.token_hash,
        family_id=row.family_id,
        client_id=row.client_id,
        user_id=row.user_id,
        email=row.email,
        scopes=row.scopes,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
    )


async def load_refresh_token(hash_value: str) -> StoredRefreshToken | None:
    async with get_session() as session:
        row = await session.scalar(
            select(DBOAuthRefreshToken).where(
                DBOAuthRefreshToken.token_hash == hash_value
            )
        )
        return None if row is None else _refresh_from_row(row)


async def rotate_refresh_token(old_hash: str, replacement: StoredRefreshToken) -> bool:
    """Mark the old generation revoked and insert the new one, atomically.

    Returns False when the old token was already rotated/gone (the caller
    treats that as replay — see ``revoke_refresh_family``).
    """
    now = datetime.now(UTC)
    async with get_session() as session:
        result = await session.execute(
            update(DBOAuthRefreshToken)
            .where(
                DBOAuthRefreshToken.token_hash == old_hash,
                DBOAuthRefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
            .returning(DBOAuthRefreshToken.token_hash)
            .execution_options(synchronize_session=False)
        )
        if result.one_or_none() is None:
            return False
        session.add(_refresh_row(replacement))
        return True


async def revoke_refresh_family(family_id: UUID) -> None:
    """Delete every generation of a family — replay evidence response."""
    async with get_session() as session:
        await session.execute(
            delete(DBOAuthRefreshToken).where(
                DBOAuthRefreshToken.family_id == family_id
            )
        )


async def delete_refresh_token(hash_value: str) -> None:
    async with get_session() as session:
        await session.execute(
            delete(DBOAuthRefreshToken).where(
                DBOAuthRefreshToken.token_hash == hash_value
            )
        )
