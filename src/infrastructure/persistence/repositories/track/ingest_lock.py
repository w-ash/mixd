"""Per-user serialization of the two canonical-track writers.

Two paths create rows in ``tracks`` and both key on the same user-scoped
unique indexes (``uq_tracks_user_isrc``, ``_mbid``, ``_spotify_id``): the
fire-and-forget play-import resolver, through
``TrackRepository.save_tracks``, and the workflow's
``TrackConnectorRepository.ingest_external_tracks_bulk``. Left to race,
one blocks on the other's uncommitted index entries until ``lock_timeout``
and the workflow node fails with 55P03.

Both take this advisory lock before any row lock, so there is no cycle for
a deadlock to form on: the loser queues at the lock instead of inside the
index, and enters once the winner's transaction ends.
"""

from typing import Final
import zlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.constants import TrackConstants

_INT4_SIGN: Final = 2**31
_INT4_WRAP: Final = 2**32


def ingest_lock_keys(user_id: str) -> tuple[int, int]:
    """Derive the advisory-lock key pair for one user's track ingest.

    Pure and deterministic, mirroring ``refresh_lock_keys``: the class half
    is the fixed ingest-seam constant, the obj half is ``crc32(user_id)``
    coerced into signed int4 range for the two-int overload of
    ``pg_advisory_xact_lock``.
    """
    raw = zlib.crc32(user_id.encode())
    obj_id = raw - _INT4_WRAP if raw >= _INT4_SIGN else raw
    return (TrackConstants.INGEST_LOCK_CLASS, obj_id)


async def acquire_user_track_ingest_lock(session: AsyncSession, user_id: str) -> None:
    """Block until this session owns the user's canonical-track ingest lock.

    Transaction-scoped: commit, rollback and connection loss all release it,
    so a failed writer never strands the next one. Callers are free to take
    it inside a larger open transaction — the lock is simply held to that
    transaction's end, which is what makes the whole write, not just one
    statement, exclusive.

    A caller that waits longer than the connection's ``lock_timeout`` fails
    with 55P03, the same classification the seam already handles.
    """
    class_id, obj_id = ingest_lock_keys(user_id)
    _ = await session.execute(
        text("SELECT pg_advisory_xact_lock(:class_id, :obj_id)"),
        {"class_id": class_id, "obj_id": obj_id},
    )
