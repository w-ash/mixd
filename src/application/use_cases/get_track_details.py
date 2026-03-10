"""Use case for retrieving full track details with assembled metadata.

Gathers data from multiple repositories (tracks, likes, plays, playlists)
within a single UoW scope. The key differentiator is the `playlists` field
answering "which playlists contain this track?".
"""

from datetime import datetime

from attrs import Factory, define

from src.config.constants import MappingOrigin
from src.domain.entities import Playlist, Track, TrackLike
from src.domain.repositories.interfaces import (
    FullMappingInfo,
    PlayAggregationResult,
    UnitOfWorkProtocol,
)


@define(frozen=True, slots=True)
class GetTrackDetailsCommand:
    track_id: int


@define(frozen=True, slots=True)
class ConnectorMappingInfo:
    """Connector mapping details for display with full provenance."""

    connector_name: str
    connector_track_id: str
    mapping_id: int = 0
    match_method: str = ""
    confidence: int = 0
    origin: str = MappingOrigin.AUTOMATIC
    is_primary: bool = False
    connector_track_title: str = ""
    connector_track_artists: list[str] = Factory(list[str])


@define(frozen=True, slots=True)
class LikeInfo:
    """Like status for a single service."""

    is_liked: bool
    liked_at: datetime | None


@define(frozen=True, slots=True)
class PlaySummary:
    """Aggregated play statistics."""

    total_plays: int
    first_played: datetime | None
    last_played: datetime | None


@define(frozen=True, slots=True)
class PlaylistSummary:
    """Lightweight playlist reference for track detail views."""

    id: int
    name: str
    description: str | None


@define(frozen=True, slots=True)
class TrackDetailsResult:
    """Assembled track detail view with data from multiple repositories."""

    track: Track
    connector_mappings: list[ConnectorMappingInfo]
    like_status: dict[str, LikeInfo]
    play_summary: PlaySummary
    playlists: list[PlaylistSummary]


def _build_connector_mappings(
    full_mappings: list[FullMappingInfo],
) -> list[ConnectorMappingInfo]:
    """Build connector mapping info from full repository data."""
    return [
        ConnectorMappingInfo(
            mapping_id=m["mapping_id"],
            connector_name=m["connector_name"],
            connector_track_id=m["connector_track_id"],
            match_method=m["match_method"],
            confidence=m["confidence"],
            origin=m["origin"],
            is_primary=m["is_primary"],
            connector_track_title=m["connector_track_title"],
            connector_track_artists=m["connector_track_artists"],
        )
        for m in full_mappings
    ]


def _build_like_status(likes: list[TrackLike]) -> dict[str, LikeInfo]:
    """Build per-service like status from like records."""
    return {
        like.service: LikeInfo(is_liked=like.is_liked, liked_at=like.liked_at)
        for like in likes
    }


def _build_play_summary(play_agg: PlayAggregationResult, track_id: int) -> PlaySummary:
    """Build play summary from aggregation data."""
    return PlaySummary(
        total_plays=play_agg.get("total_plays", {}).get(track_id, 0),
        first_played=play_agg.get("first_played_dates", {}).get(track_id),
        last_played=play_agg.get("last_played_dates", {}).get(track_id),
    )


def _build_playlist_summaries(playlists: list[Playlist]) -> list[PlaylistSummary]:
    """Build lightweight playlist references."""
    return [
        PlaylistSummary(id=p.id or 0, name=p.name, description=p.description)
        for p in playlists
    ]


@define(slots=True)
class GetTrackDetailsUseCase:
    """Assemble full track details from multiple repositories."""

    async def execute(
        self, command: GetTrackDetailsCommand, uow: UnitOfWorkProtocol
    ) -> TrackDetailsResult:
        """Fetch track with likes, play stats, and playlist memberships.

        Args:
            command: Contains track_id to look up.
            uow: Unit of work for repository access.

        Returns:
            TrackDetailsResult with assembled metadata from 4 repositories.
        """
        track_id = command.track_id
        async with uow:
            track = await uow.get_track_repository().get_by_id(track_id)

            # Sequential: these are independent queries but SQLite serializes
            # all operations. Parallelize with TaskGroup after PostgreSQL migration.
            connector_repo = uow.get_connector_repository()
            like_repo = uow.get_like_repository()
            plays_repo = uow.get_plays_repository()
            playlist_repo = uow.get_playlist_repository()

            full_mappings = await connector_repo.get_full_mappings_for_track(track_id)
            likes = await like_repo.get_track_likes(track_id)
            play_agg = await plays_repo.get_play_aggregations(
                [track_id], ["total_plays", "last_played_dates", "first_played_dates"]
            )
            playlists = await playlist_repo.get_playlists_for_track(track_id)

            return TrackDetailsResult(
                track=track,
                connector_mappings=_build_connector_mappings(full_mappings),
                like_status=_build_like_status(likes),
                play_summary=_build_play_summary(play_agg, track_id),
                playlists=_build_playlist_summaries(playlists),
            )
