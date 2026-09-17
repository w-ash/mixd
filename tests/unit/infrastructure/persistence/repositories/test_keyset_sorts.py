"""The keyset-sort declarations agree with the ORM models they page over.

A ``KeysetSort`` names a column and asserts two facts about it; a wrong flag
is silent at runtime (a ``nullable=False`` on a nullable column drops the
NULL tail at a page boundary), so this is where the declaration is checked.
"""

from typing import cast, get_args

from sqlalchemy import DateTime

from src.domain.repositories.keyset import KeysetSort
from src.domain.repositories.operation_run import OPERATION_RUN_SORT
from src.domain.repositories.play import PLAY_EVENT_SORT
from src.domain.repositories.track import TRACK_SORTS, TrackSortBy
from src.infrastructure.persistence.database.db_models import (
    DatabaseModel,
    DBOperationRun,
    DBTrack,
    DBTrackPlay,
)

DECLARED: list[tuple[type[DatabaseModel], KeysetSort]] = [
    *((DBTrack, sort) for sort in TRACK_SORTS.values()),
    (DBTrackPlay, PLAY_EVENT_SORT),
    (DBOperationRun, OPERATION_RUN_SORT),
]


class TestTrackSortRegistry:
    def test_literal_members_and_mapping_keys_agree(self) -> None:
        members = set(get_args(cast("object", TrackSortBy.__value__)))
        assert set(TRACK_SORTS) == members


class TestDeclarationsMatchTheModels:
    def test_columns_exist_and_flags_agree(self) -> None:
        for model, sort in DECLARED:
            column = model.__table__.c[sort.column]
            assert column.nullable == sort.nullable, (model.__name__, sort.column)
            assert isinstance(column.type, DateTime) == sort.is_datetime, (
                model.__name__,
                sort.column,
            )
