"""Construction-time validation of the generic mapping repository's shape.

A ``MappingShape`` is the only thing that tells ``MappingRepository`` how to
address a table, and the two can disagree in ways the type system cannot see:
a ``live_key`` spelled over a column the incumbent read never selects, or a
``supersession=True`` shape on a table missing one of the three supersession
columns (``supersession_reason`` in particular is only ever a string key in
the assert's ``set_``, so nothing else would notice). Both must fail at
construction, naming the shape and the table — not on the first assert.

No database: the probe models are declared on their own ``DeclarativeBase``
and never created.
"""

from datetime import datetime
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from sqlalchemy import Boolean, DateTime, MetaData, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.domain.entities.shared import JsonDict
from src.infrastructure.persistence.repositories._shared.mapping import (
    MappingRepository,
    MappingShape,
)
from src.infrastructure.persistence.repositories.mappers import BaseModelMapper


class _ProbeBase(DeclarativeBase):
    metadata = MetaData()


class _MappingColumnsMixin:
    """Every column the mechanism spells the same on every table."""

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    user_id: Mapped[str] = mapped_column(String())
    connector_name: Mapped[str] = mapped_column(String(32))
    match_method: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[int]
    confidence_evidence: Mapped[JsonDict | None] = mapped_column(JSONB)
    origin: Mapped[str] = mapped_column(String(20))
    is_primary: Mapped[bool] = mapped_column(Boolean)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DBPlainProbe(_MappingColumnsMixin, _ProbeBase):
    """An album-shaped table with no supersession columns at all."""

    __tablename__ = "plain_probe"

    album_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    connector_album_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))


class DBHalfSupersedingProbe(_MappingColumnsMixin, _ProbeBase):
    """Two of the three supersession columns — the shape that used to pass."""

    __tablename__ = "half_probe"

    album_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    connector_album_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))


class _Mapper(BaseModelMapper[DBPlainProbe, DBPlainProbe]):
    pass


def _shape(
    *,
    owner_id_col: str = "album_id",
    live_key: tuple[str, ...] = ("user_id", "connector_album_id"),
    supersession: bool = False,
) -> MappingShape:
    return MappingShape(
        entity_kind="album",
        owner_id_col=owner_id_col,
        connector_id_col="connector_album_id",
        live_key=live_key,
        supersession=supersession,
    )


def _repo[DBM: _ProbeBase](
    model: type[DBM], shape: MappingShape
) -> MappingRepository[DBM, DBM]:
    return MappingRepository(
        MagicMock(),
        model_class=model,
        mapper=BaseModelMapper[DBM, DBM](),
        shape=shape,
    )


class TestLiveKeyValidation:
    def test_a_live_key_over_the_incumbent_read_columns_is_accepted(self):
        shape = _shape(live_key=("user_id", "connector_album_id", "connector_name"))
        assert shape.live_key == ("user_id", "connector_album_id", "connector_name")

    def test_a_live_key_naming_another_column_is_rejected_by_name(self):
        with pytest.raises(
            ValueError, match=r"MappingShape\('album'\).*\['album_id'\]"
        ):
            _ = _shape(live_key=("user_id", "album_id"))


class TestTableColumnValidation:
    def test_a_plain_table_with_a_plain_shape_constructs(self):
        repo = _repo(DBPlainProbe, _shape())
        assert repo.columns.superseded_at is None

    def test_a_shape_column_absent_from_the_table_names_shape_and_table(self):
        with pytest.raises(
            ValueError, match=r"'album'.*\['artist_id'\].*'plain_probe'"
        ):
            _ = _repo(DBPlainProbe, _shape(owner_id_col="artist_id"))

    def test_a_superseding_shape_on_a_table_without_the_columns_is_rejected(self):
        with pytest.raises(ValueError) as excinfo:
            _ = _repo(DBPlainProbe, _shape(supersession=True))
        message = str(excinfo.value)
        assert "'plain_probe'" in message
        for name in ("superseded_at", "superseded_by_id", "supersession_reason"):
            assert name in message

    def test_a_table_missing_only_supersession_reason_is_rejected(self):
        with pytest.raises(
            ValueError, match=r"\['supersession_reason'\].*'half_probe'"
        ):
            _ = _repo(DBHalfSupersedingProbe, _shape(supersession=True))
