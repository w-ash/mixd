"""Integration tests for PlaylistRepository.get_playlists_for_track(user_id="default").

Verifies that we can find all playlists containing a specific track,
which powers the Track Detail page's "appears in playlists" section.
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.persistence.database.db_models import (
    DBPlaylist,
    DBPlaylistTrack,
    DBTrack,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


async def _setup_track_in_playlists(
    session: AsyncSession,
) -> tuple[int, list[int]]:
    """Create a track and put it in two playlists, plus a third playlist without it.

    Returns (track_id, [playlist_id_with_track, playlist_id_with_track, playlist_id_without]).
    """
    now = datetime.now(UTC)

    # Create track
    track = DBTrack(
        title="Shared Track",
        artists={"names": ["Test Artist"]},
        created_at=now,
        updated_at=now,
    )
    session.add(track)
    await session.flush()
    await session.refresh(track)

    # Create playlists
    playlist_ids = []
    for name in ["Playlist A", "Playlist B", "Playlist C"]:
        p = DBPlaylist(
            name=name,
            track_count=0,
            created_at=now,
            updated_at=now,
        )
        session.add(p)
        await session.flush()
        await session.refresh(p)
        playlist_ids.append(p.id)

    # Add track to first two playlists only
    for pid in playlist_ids[:2]:
        pt = DBPlaylistTrack(
            playlist_id=pid,
            track_id=track.id,
            sort_key="a00000000",
            created_at=now,
            updated_at=now,
        )
        session.add(pt)
    await session.flush()

    return track.id, playlist_ids


class TestGetPlaylistsForTrack:
    """get_playlists_for_track() returns correct playlists."""

    async def test_returns_playlists_containing_track(
        self, db_session: AsyncSession
    ) -> None:
        track_id, playlist_ids = await _setup_track_in_playlists(db_session)

        uow = get_unit_of_work(db_session)
        playlist_repo = uow.get_playlist_repository()
        playlists = await playlist_repo.get_playlists_for_track(
            track_id, user_id="default"
        )

        names = {p.name for p in playlists}
        assert names == {"Playlist A", "Playlist B"}
        assert len(playlists) == 2

    async def test_excludes_playlists_without_track(
        self, db_session: AsyncSession
    ) -> None:
        track_id, playlist_ids = await _setup_track_in_playlists(db_session)

        uow = get_unit_of_work(db_session)
        playlist_repo = uow.get_playlist_repository()
        playlists = await playlist_repo.get_playlists_for_track(
            track_id, user_id="default"
        )

        names = {p.name for p in playlists}
        assert "Playlist C" not in names

    async def test_no_playlists_returns_empty(self, db_session: AsyncSession) -> None:
        now = datetime.now(UTC)
        track = DBTrack(
            title="Orphan Track",
            artists={"names": ["Nobody"]},
            created_at=now,
            updated_at=now,
        )
        db_session.add(track)
        await db_session.flush()
        await db_session.refresh(track)

        uow = get_unit_of_work(db_session)
        playlist_repo = uow.get_playlist_repository()
        playlists = await playlist_repo.get_playlists_for_track(
            track.id, user_id="default"
        )

        assert playlists == []
