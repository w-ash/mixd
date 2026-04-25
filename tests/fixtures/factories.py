"""Domain entity factory functions for tests.

Plain functions (not pytest fixtures) that build domain entities with sensible
defaults and keyword overrides. Import these instead of defining local
``_make_track`` / ``_make_connector_track`` helpers in each test file.

Usage::

    from tests.fixtures.factories import make_track, make_connector_track

    track = make_track(title="Creep", artist="Radiohead")
    ct = make_connector_track("sp_123")
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid7

import attrs

from src.domain.entities.playlist import (
    ConnectorPlaylist,
    ConnectorPlaylistItem,
    Playlist,
    PlaylistEntry,
)
from src.domain.entities.preference import PreferenceEvent, TrackPreference
from src.domain.entities.tag import TagEvent, TrackTag
from src.domain.entities.track import Artist, ConnectorTrack, Track, TrackLike
from src.domain.entities.workflow import Workflow, WorkflowDef, WorkflowTaskDef
from src.infrastructure.connectors.spotify.models import (
    SpotifyAlbum,
    SpotifyArtist,
    SpotifyTrack,
)

# ---------------------------------------------------------------------------
# Track factories
# ---------------------------------------------------------------------------


def nonexistent_id() -> str:
    """Random UUID string for not-found test cases."""
    return str(uuid7())


def make_track(
    id: UUID | None = None,
    title: str = "Test Track",
    artist: str = "Test Artist",
    user_id: str = "default",
    **kwargs,
) -> Track:
    """Build a :class:`Track` with sensible defaults.

    Any extra ``kwargs`` are forwarded to the ``Track`` constructor, so you can
    set ``duration_ms``, ``isrc``, ``connector_track_identifiers``, etc.
    """
    if id is None:
        id = uuid7()
    kwargs.setdefault("artists", [Artist(name=artist)])
    return Track(id=id, title=title, user_id=user_id, **kwargs)


def make_tracks(count: int = 3, user_id: str = "default", **kwargs) -> list[Track]:
    """Build *count* tracks with unique UUIDs."""
    return [
        make_track(title=f"Track {i}", artist=f"Artist {i}", user_id=user_id, **kwargs)
        for i in range(1, count + 1)
    ]


def make_persisted_track(**kwargs) -> Track:
    """Build a Track as if already persisted (version=1).

    Use for domain/workflow tests that pass tracks through ``require_database_tracks``
    or other invariants expecting persisted entities. Integration tests exercising
    ``save_track`` should use ``make_track`` (version=0) instead.
    """
    kwargs.setdefault("version", 1)
    return make_track(**kwargs)


def make_track_like(
    track_id: UUID | None = None,
    service: str = "spotify",
    user_id: str = "default",
    **kwargs,
) -> TrackLike:
    """Build a :class:`TrackLike` with sensible defaults."""
    if track_id is None:
        track_id = uuid7()
    return TrackLike(track_id=track_id, service=service, user_id=user_id, **kwargs)


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
    id: UUID | None = None,
    name: str = "Test Playlist",
    tracks: list[Track] | None = None,
    user_id: str = "default",
    **kwargs,
) -> Playlist:
    """Build a :class:`Playlist` via ``from_tracklist``.

    *tracks* defaults to a single track so the playlist is non-empty.
    """
    if id is None:
        id = uuid7()
    tracks = tracks if tracks is not None else [make_track(user_id=user_id)]
    playlist = Playlist.from_tracklist(name=name, tracklist=tracks, **kwargs)
    return attrs.evolve(playlist, id=id, user_id=user_id)


def make_playlist_with_entries(
    id: UUID | None = None,
    track_ids: list[UUID] | None = None,
    name: str = "Test Playlist",
    user_id: str = "default",
) -> Playlist:
    """Build a :class:`Playlist` with explicit :class:`PlaylistEntry` objects."""
    if id is None:
        id = uuid7()
    ids = track_ids or [uuid7(), uuid7(), uuid7()]
    entries = [
        PlaylistEntry(
            track=make_track(id=tid, user_id=user_id),
            added_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        for tid in ids
    ]
    return Playlist(id=id, name=name, user_id=user_id, entries=entries)


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
    id: UUID | None = None,
    definition: WorkflowDef | None = None,
    is_template: bool = False,
    source_template: str | None = None,
    user_id: str = "default",
    **kwargs,
) -> Workflow:
    """Build a :class:`Workflow` with sensible defaults."""
    if id is None:
        id = uuid7()
    return Workflow(
        id=id,
        definition=definition or make_workflow_def(),
        is_template=is_template,
        source_template=source_template,
        user_id=user_id,
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


# ---------------------------------------------------------------------------
# Preference factories
# ---------------------------------------------------------------------------


def make_track_preference(
    track_id: UUID | None = None,
    state: str = "yah",
    source: str = "manual",
    user_id: str = "default",
    preferred_at: datetime | None = None,
    **kwargs,
) -> TrackPreference:
    """Build a :class:`TrackPreference` with sensible defaults."""
    return TrackPreference(
        track_id=track_id or uuid7(),
        state=state,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        user_id=user_id,
        preferred_at=preferred_at or datetime.now(UTC),
        **kwargs,
    )


def make_preference_event(
    track_id: UUID | None = None,
    old_state: str | None = None,
    new_state: str = "yah",
    source: str = "manual",
    user_id: str = "default",
    preferred_at: datetime | None = None,
    **kwargs,
) -> PreferenceEvent:
    """Build a :class:`PreferenceEvent` with sensible defaults."""
    return PreferenceEvent(
        track_id=track_id or uuid7(),
        old_state=old_state,  # type: ignore[arg-type]
        new_state=new_state,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        user_id=user_id,
        preferred_at=preferred_at or datetime.now(UTC),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Tag factories
# ---------------------------------------------------------------------------


def make_track_tag(
    tag: str = "mood:chill",
    track_id: UUID | None = None,
    source: str = "manual",
    user_id: str = "default",
    tagged_at: datetime | None = None,
    **kwargs,
) -> TrackTag:
    """Build a :class:`TrackTag` via ``TrackTag.create`` (with normalization)."""
    return TrackTag.create(
        user_id=user_id,
        track_id=track_id or uuid7(),
        raw_tag=tag,
        tagged_at=tagged_at or datetime.now(UTC),
        source=source,  # type: ignore[arg-type]
        **kwargs,
    )


def make_tag_event(
    tag: str = "mood:chill",
    action: str = "add",
    track_id: UUID | None = None,
    source: str = "manual",
    user_id: str = "default",
    tagged_at: datetime | None = None,
    **kwargs,
) -> TagEvent:
    """Build a :class:`TagEvent` with sensible defaults."""
    return TagEvent(
        track_id=track_id or uuid7(),
        tag=tag,
        action=action,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        user_id=user_id,
        tagged_at=tagged_at or datetime.now(UTC),
        **kwargs,
    )
