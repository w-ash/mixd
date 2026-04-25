"""Async DB-row seeders for tests that bypass the repository layer.

Prefer ``tests.fixtures.factories`` (e.g. ``make_track``) when the test flows
through a repository; use these helpers only to plant rows at the DB layer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.persistence.database.db_models import (
    DBConnectorPlaylist,
    DBConnectorTrack,
    DBTrack,
)


def _uid() -> str:
    return uuid4().hex[:8]


async def seed_db_track(
    session: AsyncSession,
    *,
    title: str | None = None,
    artist: str = "Test Artist",
    user_id: str = "default",
    **overrides,
) -> DBTrack:
    uid = _uid()
    kwargs: dict[str, object] = {
        "title": title or f"Track {uid}",
        "artists": {"names": [artist]},
        "user_id": user_id,
    }
    kwargs.update(overrides)
    track = DBTrack(**kwargs)
    session.add(track)
    await session.flush()
    return track


async def seed_db_connector_track(
    session: AsyncSession,
    *,
    connector_name: str = "spotify",
    artist: str = "Test Artist",
    **overrides,
) -> DBConnectorTrack:
    uid = _uid()
    kwargs: dict[str, object] = {
        "connector_name": connector_name,
        "connector_track_identifier": f"{connector_name}_ct_{uid}",
        "title": f"CT {uid}",
        "artists": {"names": [artist]},
        "raw_metadata": {},
        "last_updated": datetime.now(UTC),
    }
    kwargs.update(overrides)
    ct = DBConnectorTrack(**kwargs)
    session.add(ct)
    await session.flush()
    return ct


async def seed_db_connector_playlist(
    session: AsyncSession,
    *,
    connector_name: str = "spotify",
    **overrides,
) -> DBConnectorPlaylist:
    uid = _uid()
    kwargs: dict[str, object] = {
        "connector_name": connector_name,
        "connector_playlist_identifier": f"{connector_name}_pl_{uid}",
        "name": f"Playlist {uid}",
        "raw_metadata": {},
        "last_updated": datetime.now(UTC),
    }
    kwargs.update(overrides)
    pl = DBConnectorPlaylist(**kwargs)
    session.add(pl)
    await session.flush()
    return pl
