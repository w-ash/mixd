"""The keyset-sort declarations agree with the ORM models they page over.

A ``KeysetSort`` names a column and asserts two facts about it; a wrong flag
is silent at runtime (a ``nullable=False`` on a nullable column drops the
NULL tail at a page boundary), so this is where the declaration is checked.

A computed artist sort has no column to check against, so the pairing is the
other way round: the name must be absent from the model and present in the
repository's expression registry.
"""

from typing import cast, get_args

from sqlalchemy import DateTime

from src.domain.repositories.artist import (
    ARTIST_SORTS,
    COMPUTED_ARTIST_SORT_COLUMNS,
    ArtistSortBy,
)
from src.domain.repositories.keyset import KeysetSort
from src.domain.repositories.operation_run import OPERATION_RUN_SORT
from src.domain.repositories.play import PLAY_EVENT_SORT
from src.domain.repositories.track import TRACK_SORTS, TrackSortBy
from src.infrastructure.persistence.database.models import (
    DatabaseModel,
    DBArtist,
    DBOperationRun,
    DBTrack,
    DBTrackPlay,
)
from src.infrastructure.persistence.repositories.artist.core import (
    COMPUTED_SORT_COLUMNS,
)

DECLARED: list[tuple[type[DatabaseModel], KeysetSort]] = [
    *((DBTrack, sort) for sort in TRACK_SORTS.values()),
    *(
        (DBArtist, sort)
        for sort in ARTIST_SORTS.values()
        if sort.column not in COMPUTED_ARTIST_SORT_COLUMNS
    ),
    (DBTrackPlay, PLAY_EVENT_SORT),
    (DBOperationRun, OPERATION_RUN_SORT),
]


class TestTrackSortRegistry:
    def test_literal_members_and_mapping_keys_agree(self) -> None:
        members = set(get_args(cast("object", TrackSortBy.__value__)))
        assert set(TRACK_SORTS) == members


class TestArtistSortRegistry:
    def test_literal_members_and_mapping_keys_agree(self) -> None:
        members = set(get_args(cast("object", ArtistSortBy.__value__)))
        assert set(ARTIST_SORTS) == members

    def test_every_computed_sort_has_an_expression_and_no_column(self) -> None:
        for column in COMPUTED_ARTIST_SORT_COLUMNS:
            assert column in COMPUTED_SORT_COLUMNS, column
            assert column not in DBArtist.__table__.c, column

    def test_no_stored_sort_is_declared_computed(self) -> None:
        stored = {
            sort.column
            for sort in ARTIST_SORTS.values()
            if sort.column not in COMPUTED_ARTIST_SORT_COLUMNS
        }
        assert stored.isdisjoint(COMPUTED_SORT_COLUMNS)


class TestDeclaredKeysAreDistinct:
    def test_no_two_declarations_share_a_key(self) -> None:
        keys = [sort.key for _, sort in DECLARED]
        assert len(keys) == len(set(keys))


class TestDeclarationsMatchTheModels:
    def test_columns_exist_and_flags_agree(self) -> None:
        for model, sort in DECLARED:
            column = model.__table__.c[sort.column]
            assert column.nullable == sort.nullable, (model.__name__, sort.column)
            assert isinstance(column.type, DateTime) == sort.is_datetime, (
                model.__name__,
                sort.column,
            )
