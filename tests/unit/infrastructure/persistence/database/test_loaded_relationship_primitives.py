"""Unit tests for DatabaseModel.loaded_list / loaded_one.

These primitives read SQLAlchemy ``InstanceState`` (a plain dict lookup against
``state.dict``), so they need NO database — every case runs on transient
instances. The load-bearing guarantee is structural: an *unloaded* relationship
returns ``[]`` / ``None`` rather than emitting a lazy SELECT, which is what lets
mappers stay pure (await-free) over eager-loaded state.
"""

from src.infrastructure.persistence.database.db_models import (
    DBPlaylist,
    DBPlaylistTrack,
    DBTrack,
)


class TestLoadedListUnloaded:
    """An unloaded *-to-many relationship degrades to [] with zero I/O."""

    def test_transient_instance_returns_empty(self):
        # A fresh transient instance has never loaded `tracks` — no SQL is
        # emitted (the call would raise MissingGreenlet if it tried).
        playlist = DBPlaylist()
        assert playlist.loaded_list(DBPlaylist.tracks, DBPlaylistTrack) == []


class TestLoadedListLoaded:
    """A populated relationship comes back as a typed list."""

    def test_returns_assigned_collection(self):
        playlist = DBPlaylist()
        pt1 = DBPlaylistTrack()
        pt2 = DBPlaylistTrack()
        playlist.tracks = [pt1, pt2]

        result = playlist.loaded_list(DBPlaylist.tracks, DBPlaylistTrack)
        assert result == [pt1, pt2]
        assert all(isinstance(x, DBPlaylistTrack) for x in result)

    def test_empty_assigned_collection_returns_empty(self):
        playlist = DBPlaylist()
        playlist.tracks = []
        assert playlist.loaded_list(DBPlaylist.tracks, DBPlaylistTrack) == []


class TestLoadedOneUnloaded:
    """An unloaded *-to-one relationship degrades to None with zero I/O."""

    def test_transient_instance_returns_none(self):
        pt = DBPlaylistTrack()
        assert pt.loaded_one(DBPlaylistTrack.track, DBTrack) is None


class TestLoadedOneLoaded:
    """A populated to-one relationship comes back as the typed value."""

    def test_returns_assigned_value(self):
        track = DBTrack(title="x", artists={"names": ["a"]})
        pt = DBPlaylistTrack()
        pt.track = track
        assert pt.loaded_one(DBPlaylistTrack.track, DBTrack) is track

    def test_type_mismatch_returns_none(self):
        # item_type is the runtime narrowing guard: a value of the wrong type
        # (or the NO_VALUE sentinel) fails the isinstance check.
        track = DBTrack(title="x", artists={"names": ["a"]})
        pt = DBPlaylistTrack()
        pt.track = track
        assert pt.loaded_one(DBPlaylistTrack.track, DBPlaylist) is None
