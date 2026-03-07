"""Domain entity factory functions for tests.

Plain functions (not pytest fixtures) that build domain entities with sensible
defaults and keyword overrides. Import these instead of defining local
``_make_track`` / ``_make_connector_track`` helpers in each test file.

Usage::

    from tests.fixtures.factories import make_track, make_connector_track

    track = make_track(id=1, title="Creep", artist="Radiohead")
    ct = make_connector_track("sp_123")
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.domain.entities.playlist import (
    ConnectorPlaylist,
    ConnectorPlaylistItem,
    Playlist,
    PlaylistEntry,
)
from src.domain.entities.track import Artist, ConnectorTrack, Track
from src.domain.entities.workflow import Workflow, WorkflowDef, WorkflowTaskDef
from src.infrastructure.connectors.spotify.models import (
    SpotifyAlbum,
    SpotifyArtist,
    SpotifyTrack,
)

# ---------------------------------------------------------------------------
# Track factories
# ---------------------------------------------------------------------------


def make_track(
    id: int | None = 1,
    title: str = "Test Track",
    artist: str = "Test Artist",
    **kwargs,
) -> Track:
    """Build a :class:`Track` with sensible defaults.

    Any extra ``kwargs`` are forwarded to the ``Track`` constructor, so you can
    set ``duration_ms``, ``isrc``, ``connector_track_identifiers``, etc.
    """
    kwargs.setdefault("artists", [Artist(name=artist)])
    return Track(id=id, title=title, **kwargs)


def make_tracks(count: int = 3, **kwargs) -> list[Track]:
    """Build *count* tracks numbered 1..count."""
    return [
        make_track(id=i, title=f"Track {i}", artist=f"Artist {i}", **kwargs)
        for i in range(1, count + 1)
    ]


# ---------------------------------------------------------------------------
# SpotifyTrack factories (Pydantic model for API responses)
# ---------------------------------------------------------------------------


def make_spotify_track(
    spotify_id: str = "test_spotify_id",
    name: str = "Test Song",
    artist_name: str = "Test Artist",
    album_name: str = "Test Album",
    duration_ms: int = 240000,
    **kwargs,
) -> SpotifyTrack:
    """Build a :class:`SpotifyTrack` Pydantic model with sensible defaults.

    Any extra ``kwargs`` are forwarded to the ``SpotifyTrack`` constructor.
    """
    kwargs.setdefault("artists", [SpotifyArtist(name=artist_name)])
    kwargs.setdefault("album", SpotifyAlbum(name=album_name))
    return SpotifyTrack(id=spotify_id, name=name, duration_ms=duration_ms, **kwargs)


# ---------------------------------------------------------------------------
# ConnectorTrack factories
# ---------------------------------------------------------------------------


def make_connector_track(
    identifier: str,
    connector_name: str = "spotify",
    *,
    title: str | None = None,
    artist: str = "Test Artist",
    **kwargs,
) -> ConnectorTrack:
    """Build a :class:`ConnectorTrack`."""
    return ConnectorTrack(
        connector_name=connector_name,
        connector_track_identifier=identifier,
        title=title or f"Song {identifier}",
        artists=[Artist(name=artist)],
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Playlist factories
# ---------------------------------------------------------------------------


def make_playlist(
    id: int = 1,
    name: str = "Test Playlist",
    tracks: list[Track] | None = None,
    **kwargs,
) -> Playlist:
    """Build a :class:`Playlist` via ``from_tracklist``.

    *tracks* defaults to a single track so the playlist is non-empty.
    """
    tracks = tracks if tracks is not None else [make_track(id=1)]
    return Playlist.from_tracklist(name=name, tracklist=tracks, **kwargs).with_id(id)


def make_playlist_with_entries(
    id: int = 1,
    track_ids: list[int] | None = None,
    name: str = "Test Playlist",
) -> Playlist:
    """Build a :class:`Playlist` with explicit :class:`PlaylistEntry` objects."""
    ids = track_ids or [1, 2, 3]
    entries = [
        PlaylistEntry(
            track=make_track(tid),
            added_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        for tid in ids
    ]
    return Playlist(id=id, name=name, entries=entries)


# ---------------------------------------------------------------------------
# ConnectorPlaylist factories
# ---------------------------------------------------------------------------


def make_connector_playlist_item(
    identifier: str,
    position: int = 0,
    **kwargs,
) -> ConnectorPlaylistItem:
    """Build a :class:`ConnectorPlaylistItem`."""
    return ConnectorPlaylistItem(
        connector_track_identifier=identifier,
        position=position,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Workflow factories
# ---------------------------------------------------------------------------


def make_workflow_def(
    id: str = "test-workflow",
    name: str = "Test Workflow",
    tasks: list[WorkflowTaskDef] | None = None,
    **kwargs,
) -> WorkflowDef:
    """Build a :class:`WorkflowDef` with sensible defaults."""
    if tasks is None:
        tasks = [
            WorkflowTaskDef(
                id="source",
                type="source.liked_tracks",
                config={"service": "spotify"},
            ),
        ]
    return WorkflowDef(id=id, name=name, tasks=tasks, **kwargs)


def make_workflow(
    id: int | None = 1,
    definition: WorkflowDef | None = None,
    is_template: bool = False,
    source_template: str | None = None,
    **kwargs,
) -> Workflow:
    """Build a :class:`Workflow` with sensible defaults."""
    return Workflow(
        id=id,
        definition=definition or make_workflow_def(),
        is_template=is_template,
        source_template=source_template,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# ConnectorPlaylist factories
# ---------------------------------------------------------------------------


def make_connector_playlist(
    items: list[ConnectorPlaylistItem] | None = None,
    connector_name: str = "spotify",
    **kwargs,
) -> ConnectorPlaylist:
    """Build a :class:`ConnectorPlaylist`."""
    kwargs.setdefault("connector_playlist_identifier", "playlist_1")
    kwargs.setdefault("name", "Test Playlist")
    return ConnectorPlaylist(
        connector_name=connector_name,
        items=items or [],
        **kwargs,
    )
