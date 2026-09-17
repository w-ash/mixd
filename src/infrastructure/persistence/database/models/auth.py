"""Connector OAuth grants, CSRF state, and the in-app authorization server.

AS tables (v0.9.5): clients, parked authorization requests, codes, refresh tokens.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from src.domain.entities.shared import JsonDict
from src.infrastructure.persistence.database.models.base import (
    BaseEntity,
    DatabaseModel,
    PgJsonb,
    PgUuidCol,
    UuidType,
)


class DBOAuthToken(BaseEntity):
    """Persisted OAuth tokens and session keys for external service authentication.

    One row per user per service (UNIQUE on user_id + service). Supports both
    OAuth 2.0 tokens (Spotify: access_token + refresh_token + expires_at) and
    session-based auth (Last.fm: session_key, infinite lifetime).

    Enables cloud deployment where filesystem is ephemeral — tokens survive
    container restarts without re-authentication.
    """

    __tablename__: str = "oauth_tokens"

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
    service: Mapped[str] = mapped_column(String(32), nullable=False)
    token_type: Mapped[str] = mapped_column(String(20), nullable=False)
    access_token: Mapped[str | None] = mapped_column(String())
    refresh_token: Mapped[str | None] = mapped_column(String())
    session_key: Mapped[str | None] = mapped_column(String())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scope: Mapped[str | None] = mapped_column(String())
    account_name: Mapped[str | None] = mapped_column(String(255))
    extra_data: Mapped[JsonDict] = mapped_column(PgJsonb, default=dict)

    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("user_id", "service", name="uq_oauth_tokens_user_service"),
    )


class DBOAuthState(DatabaseModel):
    """Transient OAuth CSRF state for callback user association.

    Stores the CSRF state token, user_id, and PKCE code_verifier during
    OAuth flows. The callback handler consumes (deletes) the row atomically.
    Short-lived (5-minute TTL), no RLS needed — the unguessable state token
    is the access control. Uses DatabaseModel (not BaseEntity) since updated_at
    is unnecessary for ephemeral rows.
    """

    __tablename__: str = "oauth_states"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("state", name="uq_oauth_states_state"),
    )

    state: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(), nullable=False)
    service: Mapped[str] = mapped_column(String(32), nullable=False)
    code_verifier: Mapped[str | None] = mapped_column(String())
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class DBOAuthClient(BaseEntity):
    """An OAuth client known to the in-app authorization server (v0.9.5).

    ``kind='dcr'`` rows are RFC 7591 dynamic registrations; ``kind='cimd'``
    rows cache fetched Client-ID-Metadata documents (client_id = the https
    metadata URL). ``client_info`` holds the full RFC 7591 metadata dump.
    No RLS — AS system table with no user context at the token endpoint
    (migration 039); the CHECK on ``kind`` lives in the migration only.
    """

    __tablename__: str = "oauth_clients"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("client_id", name="uq_oauth_clients_client_id"),
    )

    client_id: Mapped[str] = mapped_column(String(), nullable=False)
    kind: Mapped[str] = mapped_column(String(), nullable=False)
    client_info: Mapped[JsonDict] = mapped_column(PgJsonb, nullable=False)


class DBOAuthAuthorizationRequest(BaseEntity):
    """An authorization request parked while the user completes consent.

    Created by the /authorize endpoint, consumed (deleted) by the consent
    API's approve/deny call. Short-lived; stale rows are evicted
    opportunistically. No RLS (migration 039).
    """

    __tablename__: str = "oauth_authorization_requests"

    client_id: Mapped[str] = mapped_column(String(), nullable=False)
    client_name: Mapped[str | None] = mapped_column(String(), nullable=True)
    params: Mapped[JsonDict] = mapped_column(PgJsonb, nullable=False)


class DBOAuthAuthorizationCode(BaseEntity):
    """An issued authorization code (hashed), single-use at exchange.

    ``code_hash`` is SHA-256 of the code string — a DB leak must not yield
    redeemable codes. Consumed via atomic DELETE…RETURNING so two racing
    /token calls can't both exchange it. No RLS (migration 039).
    """

    __tablename__: str = "oauth_authorization_codes"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("code_hash", name="uq_oauth_authorization_codes_code_hash"),
    )

    code_hash: Mapped[str] = mapped_column(String(), nullable=False)
    client_id: Mapped[str] = mapped_column(String(), nullable=False)
    user_id: Mapped[str] = mapped_column(String(), nullable=False)
    email: Mapped[str] = mapped_column(String(), nullable=False)
    scopes: Mapped[str] = mapped_column(String(), nullable=False)
    code_challenge: Mapped[str] = mapped_column(String(), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(), nullable=False)
    redirect_uri_provided_explicitly: Mapped[bool] = mapped_column(nullable=False)
    resource: Mapped[str | None] = mapped_column(String(), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DBOAuthRefreshToken(BaseEntity):
    """A rotating refresh token (hashed) with replay-revocable family.

    Rotation marks the old generation ``revoked_at`` (kept, not deleted) and
    inserts the new one under the same ``family_id`` — presenting a revoked
    token is replay evidence and deletes the whole family. No RLS
    (migration 039).
    """

    __tablename__: str = "oauth_refresh_tokens"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("token_hash", name="uq_oauth_refresh_tokens_token_hash"),
        Index("ix_oauth_refresh_tokens_family_id", "family_id"),
    )

    token_hash: Mapped[str] = mapped_column(String(), nullable=False)
    family_id: Mapped[UuidType] = mapped_column(PgUuidCol(as_uuid=True), nullable=False)
    client_id: Mapped[str] = mapped_column(String(), nullable=False)
    user_id: Mapped[str] = mapped_column(String(), nullable=False)
    email: Mapped[str] = mapped_column(String(), nullable=False)
    scopes: Mapped[str] = mapped_column(String(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
