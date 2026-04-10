"""Cross-table read-only stats repository for dashboard aggregation.

Replaces 8 sequential COUNT queries with 5 efficient queries:
1. Scalar sub-selects for totals (tracks, plays, playlists, liked)
2. GROUP BY plays by service
3. GROUP BY likes by service
4. GROUP BY tracks by connector
5. GROUP BY playlists by connector

Not parallelizable — AsyncSession supports one query at a time.
"""

from typing import cast

from sqlalchemy import distinct, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.repositories.interfaces import DashboardAggregates
from src.infrastructure.persistence.database.db_models import (
    DBPlaylist,
    DBPlaylistMapping,
    DBTrack,
    DBTrackLike,
    DBTrackMapping,
    DBTrackPlay,
)
from src.infrastructure.persistence.repositories.repo_decorator import db_operation


class StatsRepository:
    """Cross-table read-only aggregation queries for the dashboard."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @db_operation("get_dashboard_aggregates")
    async def get_dashboard_aggregates(self, *, user_id: str) -> DashboardAggregates:
        """Compute all dashboard counts in 5 round trips, scoped to user.

        Every sub-select is filtered by user_id. For transitively-scoped
        tables (track_mappings, playlist_mappings), we join through the
        user-scoped parent table.
        """
        # --- Query 1: scalar totals via sub-selects --------------------------
        totals_stmt = select(
            select(func.count(DBTrack.id))
            .where(DBTrack.user_id == user_id)
            .label("total_tracks"),
            select(func.count(DBTrackPlay.id))
            .where(DBTrackPlay.user_id == user_id)
            .label("total_plays"),
            select(func.count(DBPlaylist.id))
            .where(DBPlaylist.user_id == user_id)
            .label("total_playlists"),
            select(func.count(distinct(DBTrackLike.track_id)))
            .where(DBTrackLike.is_liked == true(), DBTrackLike.user_id == user_id)
            .label("total_liked"),
        )
        totals_row = (await self._session.execute(totals_stmt)).one()

        # --- Query 2: service breakdowns (plays + likes) ---------------------
        plays_by_svc_stmt = (
            select(
                DBTrackPlay.service,
                func.count(DBTrackPlay.id),
            )
            .where(DBTrackPlay.user_id == user_id)
            .group_by(DBTrackPlay.service)
        )
        # SQLAlchemy Row[tuple] field access loses column-level typing in stubs;
        # cast the row sequence once to a typed iterable of (str, int).
        plays_rows = cast(
            "list[tuple[str, int]]",
            (await self._session.execute(plays_by_svc_stmt)).all(),
        )
        plays_by_connector = {str(svc): int(cnt) for svc, cnt in plays_rows}

        liked_by_svc_stmt = (
            select(
                DBTrackLike.service,
                func.count(distinct(DBTrackLike.track_id)),
            )
            .where(DBTrackLike.is_liked == true(), DBTrackLike.user_id == user_id)
            .group_by(DBTrackLike.service)
        )
        liked_rows = cast(
            "list[tuple[str, int]]",
            (await self._session.execute(liked_by_svc_stmt)).all(),
        )
        liked_by_connector = {str(svc): int(cnt) for svc, cnt in liked_rows}

        # --- Query 3: connector breakdowns (tracks + playlists) --------------
        # track_mappings is user-scoped, so filter directly
        tracks_by_conn_stmt = (
            select(
                DBTrackMapping.connector_name,
                func.count(distinct(DBTrackMapping.track_id)),
            )
            .where(DBTrackMapping.user_id == user_id)
            .group_by(DBTrackMapping.connector_name)
        )
        tracks_rows = cast(
            "list[tuple[str, int]]",
            (await self._session.execute(tracks_by_conn_stmt)).all(),
        )
        tracks_by_connector = {str(name): int(cnt) for name, cnt in tracks_rows}

        # playlist_mappings is transitively scoped via playlist FK — join through
        playlists_by_conn_stmt = (
            select(
                DBPlaylistMapping.connector_name,
                func.count(DBPlaylistMapping.id),
            )
            .join(DBPlaylist, DBPlaylistMapping.playlist_id == DBPlaylist.id)
            .where(DBPlaylist.user_id == user_id)
            .group_by(DBPlaylistMapping.connector_name)
        )
        playlists_rows = cast(
            "list[tuple[str, int]]",
            (await self._session.execute(playlists_by_conn_stmt)).all(),
        )
        playlists_by_connector = {str(name): int(cnt) for name, cnt in playlists_rows}

        return DashboardAggregates(
            total_tracks=int(totals_row.total_tracks),  # pyright: ignore[reportAny]  # SQLAlchemy Row dynamic field
            total_plays=int(totals_row.total_plays),  # pyright: ignore[reportAny]
            total_playlists=int(totals_row.total_playlists),  # pyright: ignore[reportAny]
            total_liked=int(totals_row.total_liked),  # pyright: ignore[reportAny]
            tracks_by_connector=tracks_by_connector,
            liked_by_connector=liked_by_connector,
            plays_by_connector=plays_by_connector,
            playlists_by_connector=playlists_by_connector,
        )
