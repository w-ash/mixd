"""Declarative foundation shared by every model module in this package.

Architecture:
- DatabaseModel: Single DeclarativeBase foundation with ``type_annotation_map``
  that resolves ``Mapped[JsonDict]`` to ``postgresql.JSONB`` automatically.
  This means JSONB columns can use the domain ``JsonDict`` alias instead of
  ``dict[str, Any]`` — the type flows all the way from Python to the column.
- TimestampMixin: Provides created_at/updated_at for audit trails
- BaseEntity: DatabaseModel + TimestampMixin, the base for almost every table

All entities use hard deletes for simplicity and performance.
Data recovery relies on external API re-import and database backups.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, ClassVar, cast
import uuid as uuid_mod

from sqlalchemy import (
    DateTime,
    MetaData,
    inspect,
)
from sqlalchemy.dialects import postgresql as pg_dialect
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import (
    NO_VALUE,
    DeclarativeBase,
    InstrumentedAttribute,
    Mapped,
    mapped_column,
)

from src.domain.entities.shared import JsonDict

# Type aliases to avoid import name conflicts between stdlib uuid and SQLAlchemy UUID
UuidType = uuid_mod.UUID
PgUuidCol = pg_dialect.UUID
PgJsonb = pg_dialect.JSONB
PgArray = pg_dialect.ARRAY

# Define naming convention for constraints (SQLAlchemy 2.0 best practice)
convention = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",  # Index
    "uq": "uq_%(table_name)s_%(column_0_label)s",  # Unique constraint
    "ck": "ck_%(table_name)s_%(constraint_name)s",  # Check constraint
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",  # Foreign key
    "pk": "pk_%(table_name)s",  # Primary key
}

# Create metadata with naming convention (single source of truth)
metadata = MetaData(naming_convention=convention)


def vocabulary_check(column_name: str, vocabulary: frozenset[str]) -> str:
    """Render an ``IN`` predicate over a domain vocabulary.

    Sorted so the DDL text is deterministic: the migration that created the
    constraint inlines the same sorted list, and a constraint whose text drifts
    between the two is a schema the tests never run against.
    """
    members = ", ".join(f"'{value}'" for value in sorted(vocabulary))
    return f"{column_name} IN ({members})"


class DatabaseModel(AsyncAttrs, DeclarativeBase):
    """Foundation model for all database entities.

    Single DeclarativeBase inheritance following SQLAlchemy 2.0 best practices.
    All database models inherit from this class to ensure consistent metadata handling.

    The ``type_annotation_map`` routes ``Mapped[JsonDict]`` to ``postgresql.JSONB``
    so JSONB columns get typed metadata without every ``mapped_column`` needing
    an explicit ``PgJsonb`` argument. SQLAlchemy 2.0.37+ matches union types by
    content (excluding ``None``), so ``Mapped[JsonDict | None]`` also resolves
    via this single entry. See: docs.sqlalchemy.org/en/20/orm/declarative_tables.
    """

    metadata: ClassVar[MetaData] = metadata

    # SQLAlchemy stubs declare type_annotation_map as dict[Any, Any]; the runtime
    # match is by key shape (JsonDict here), the value side typing is informational.
    # ``datetime`` maps to timestamptz so a bare ``Mapped[datetime]`` matches the
    # migrations (which have always used ``DateTime(timezone=True)``) instead of
    # SQLAlchemy's naive default.
    type_annotation_map: ClassVar[dict[Any, Any]] = {  # pyright: ignore[reportExplicitAny]  # SQLAlchemy stub shape
        JsonDict: PgJsonb,
        datetime: DateTime(timezone=True),
    }

    id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True), primary_key=True, default=uuid_mod.uuid7, sort_order=-1
    )

    def loaded_list[T](
        self, attribute: InstrumentedAttribute[Sequence[T]], item_type: type[T]
    ) -> list[T]:
        """Read an eager-loaded ``*``-to-many relationship as a typed list.

        Zero I/O: ``AttributeState.loaded_value`` is a plain dict lookup against
        the instance state (``state.dict.get(key, NO_VALUE)``), so this can never
        emit SQL or hit a greenlet boundary. Returns ``[]`` when the relationship
        was not eager-loaded — a forgotten ``selectinload`` degrades gracefully
        instead of lazy-loading. ``item_type`` narrows ``loaded_value`` (typed
        ``Any`` in the stubs) and filters out any element of the wrong type.

        Pass the ORM descriptor directly: ``model.loaded_list(DBPlaylist.tracks,
        DBPlaylistTrack)``.
        """
        value = cast(object, inspect(self).attrs[attribute.key].loaded_value)
        if value is NO_VALUE or not isinstance(value, list):
            return []
        return [
            item for item in cast("list[object]", value) if isinstance(item, item_type)
        ]

    def loaded_one[T](
        self, attribute: InstrumentedAttribute[T], item_type: type[T]
    ) -> T | None:
        """Read an eager-loaded ``*``-to-one relationship as a typed value-or-None.

        The to-one counterpart of :meth:`loaded_list`. Zero I/O; returns ``None``
        when the relationship was not eager-loaded (``loaded_value`` is ``NO_VALUE``,
        which fails the ``isinstance`` check) or holds the wrong type.

        Pass the ORM descriptor directly: ``mapping.loaded_one(
        DBTrackMapping.connector_track, DBConnectorTrack)``.
        """
        value = cast(object, inspect(self).attrs[attribute.key].loaded_value)
        return value if isinstance(value, item_type) else None


class TimestampMixin:
    """Provides created_at/updated_at timestamps for all entities.

    Mixin pattern following SQLAlchemy 2.0 declarative mixins best practices.
    Applied to all entities for audit trail.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


class BaseEntity(DatabaseModel, TimestampMixin):
    """Base class for all database entities.

    All entities inherit from this class for consistent timestamp behavior.
    Uses hard deletes for simplicity and performance.
    """

    __abstract__: ClassVar[bool] = True
