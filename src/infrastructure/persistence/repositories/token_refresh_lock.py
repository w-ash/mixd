"""Single-flight token refresh, shared across processes.

Serializes token refresh per (user, service) with a Postgres advisory
xact lock, then double-checks the stored token under the lock. Built for
services that rotate refresh tokens (Tidal): the provider treats a
concurrent second refresh with the same refresh token as replay and
revokes the grant. Service-agnostic through two completion signals: a
loser adopts the stored token when EITHER the refresh token changed
(rotation) OR ``extra_data["refreshed_at"]`` — stamped by every
:meth:`TokenRefreshGuard.save` — is newer than the loser's own entry
time, so a provider that answers a refresh WITHOUT rotating still
dedupes concurrent flights instead of letting the loser re-POST.

The guard runs on a dedicated short session from the standalone
``get_session()`` path, the same seam ``DatabaseTokenStorage`` uses:
token refresh happens inside httpx2 auth flows, outside any UoW.

The lock is held across the caller's refresh POST. That is deliberate
and bounded: the POST is capped by the httpx client timeout, the lock is
transaction-scoped (``pg_advisory_xact_lock``) so commit, rollback, and
connection loss all release it, and a waiting entrant is capped by the
caller-supplied ``lock_timeout_seconds`` (set transaction-locally,
overriding the connection's 10s default), failing typed
(:class:`TokenRefreshContendedError`) instead of with a raw database
error when a slow winner outlasts it.

Known loss mode (post-POST, pre-commit): an exception between the
winner's successful refresh POST and its commit rolls back the rotated
pair while the provider has already invalidated the old refresh token
upstream. The stored token is then permanently dead — the next refresh
POSTs it, gets ``invalid_grant``, compare-and-deletes the row, and the
user must reconnect. Accepted degradation: the window is one local
commit wide, and the failure converges on the reconnect flow rather
than a wedged grant.

Pool occupancy: the guard's session holds one pooled connection for the
whole flight — advisory lock, re-read, the caller's refresh POST, and
the save. The caller's ``invalid_grant`` handling additionally opens
nested short sessions (load/delete through ``DatabaseTokenStorage``)
while the guard's connection is still checked out, so one in-flight
refresh can briefly occupy two pooled connections. Bounded by the httpx
timeout and the lock timeout, but worth remembering when sizing the
pool.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import time
from typing import Final
import zlib

from attrs import define
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.config.constants import TokenConstants
from src.domain.exceptions import TokenRefreshContendedError
from src.infrastructure.connectors._shared.token_storage import StoredToken
from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.database.user_context import user_context
from src.infrastructure.persistence.repositories.token_storage import (
    DatabaseTokenStorage,
)

logger = get_logger(__name__)

_INT4_SIGN = 2**31
_INT4_WRAP = 2**32

# Postgres SQLSTATE for ``lock_timeout`` expiry (lock_not_available).
_LOCK_NOT_AVAILABLE_SQLSTATE: Final = "55P03"


def refresh_lock_keys(service: str, user_id: str) -> tuple[int, int]:
    """Derive the advisory-lock key pair for one (service, user).

    Pure and deterministic. The class half is the fixed token-seam
    constant; the obj half is ``crc32("service:user_id")`` coerced into
    signed int4 range, matching the two-int overload of
    ``pg_advisory_xact_lock``.
    """
    raw = zlib.crc32(f"{service}:{user_id}".encode())
    obj_id = raw - _INT4_WRAP if raw >= _INT4_SIGN else raw
    return (TokenConstants.REFRESH_LOCK_CLASS, obj_id)


def _is_lock_timeout(error: DBAPIError) -> bool:
    """Whether a wrapped driver error is Postgres's lock_timeout expiry."""
    sqlstate: object = getattr(error.orig, "sqlstate", None)
    return sqlstate == _LOCK_NOT_AVAILABLE_SQLSTATE


def _refreshed_at(stored: StoredToken) -> float | None:
    """The completion stamp ``guard.save`` wrote, if the token carries one."""
    value = (stored.get("extra_data") or {}).get("refreshed_at")
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _flight_already_completed(
    stored: StoredToken, current_refresh_token: str, entered_at: float
) -> bool:
    """Whether another flight finished a refresh after this entrant arrived.

    Two signals, either sufficient: the stored refresh token no longer
    matches the one this entrant would POST (rotation happened), or the
    ``refreshed_at`` stamp is newer than this entrant's entry time (a
    refresh completed — possibly without rotating — while it waited).
    """
    if stored.get("refresh_token") != current_refresh_token:
        return True
    stamp = _refreshed_at(stored)
    return stamp is not None and stamp > entered_at


@define
class TokenRefreshGuard:
    """What one entrant sees under the lock.

    ``rotated_token`` is ``None`` when this entrant won the flight:
    perform the refresh POST and call :meth:`save`. Otherwise another
    flight already completed a refresh — use ``rotated_token`` and skip
    the POST.
    """

    rotated_token: StoredToken | None
    _session: AsyncSession
    _service: str
    _user_id: str
    _storage: DatabaseTokenStorage

    async def save(self, new_token: StoredToken) -> None:
        """Persist the refreshed token through the lock-holding session.

        Stamps ``extra_data["refreshed_at"]`` (Unix seconds) so entrants
        that arrived during this flight detect completion even when the
        provider answered without rotating the refresh token.
        """
        extra_data = dict(new_token.get("extra_data") or {})
        extra_data["refreshed_at"] = time.time()
        to_save = new_token.copy()
        to_save["extra_data"] = extra_data
        await self._storage.save_with_session(
            self._session, self._service, self._user_id, to_save
        )


@asynccontextmanager
async def single_flight_token_refresh(
    service: str,
    user_id: str,
    *,
    current_refresh_token: str,
    lock_timeout_seconds: float,
) -> AsyncGenerator[TokenRefreshGuard]:
    """Serialize one token refresh per (user, service).

    Opens a dedicated session, applies ``lock_timeout_seconds`` as the
    transaction-local ``lock_timeout``, takes the blocking advisory xact
    lock, then re-reads the stored token under ``user_context(user_id)``.
    A concurrent entrant waits at the lock; when it enters, the re-read
    shows the winner's completed refresh and its ``rotated_token`` is set.

    ``lock_timeout_seconds`` should exceed the caller's refresh-POST
    timeout (request timeout plus headroom) so a healthy winner is always
    waited out; a waiter that outlives it raises
    :class:`TokenRefreshContendedError` (transient) instead of an untyped
    database error.

    Commit on clean exit and rollback on exception both release the
    lock, so a failed flight never strands later entrants.
    """
    class_id, obj_id = refresh_lock_keys(service, user_id)
    storage = DatabaseTokenStorage()
    entered_at = time.time()
    with user_context(user_id):
        async with get_session() as session:
            _ = await session.execute(
                text("SELECT set_config('lock_timeout', :timeout_ms, true)"),
                {"timeout_ms": str(int(lock_timeout_seconds * 1000))},
            )
            try:
                _ = await session.execute(
                    text("SELECT pg_advisory_xact_lock(:class_id, :obj_id)"),
                    {"class_id": class_id, "obj_id": obj_id},
                )
            except DBAPIError as error:
                if _is_lock_timeout(error):
                    raise TokenRefreshContendedError(service) from error
                raise
            stored = await storage.load_with_session(session, service, user_id)
            rotated: StoredToken | None = None
            if stored is not None and _flight_already_completed(
                stored, current_refresh_token, entered_at
            ):
                logger.info(
                    "Token refresh already completed by a concurrent flight; "
                    "skipping POST",
                    service=service,
                )
                rotated = stored
            yield TokenRefreshGuard(rotated, session, service, user_id, storage)
