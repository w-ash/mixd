"""Model mapper machinery: DB-model <-> domain-entity conversion.

Split out of ``base_repo.py`` so the mapper seam (the ``ModelMapper`` /
``SessionAwareMapper`` protocols, the ``BaseModelMapper`` base implementation,
and the ``SimpleMapperFactory`` for 1:1 field mappings) lives apart from the
``BaseRepository`` query/CRUD/upsert machinery. Every concrete mapper builds on
these; ``BaseRepository`` imports ``ModelMapper`` and ``has_session_support``
back from here.
"""

from collections.abc import Sequence
from typing import Protocol, TypeIs, cast, override

from attrs import define
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.interfaces import ORMOption

from src.infrastructure.persistence.database.db_models import DatabaseModel


class ModelMapper[TDBModel: DatabaseModel, TDomainModel](Protocol):
    """Protocol for bidirectional mapping between models."""

    @staticmethod
    async def to_domain(db_model: TDBModel) -> TDomainModel:
        """Convert database model to domain model."""
        ...

    @staticmethod
    def to_db(domain_model: TDomainModel) -> TDBModel:
        """Convert domain model to database model."""
        ...

    @staticmethod
    def get_default_relationships() -> Sequence[str | ORMOption]:
        """Get default relationships to load for this model.

        Each entry is either a relationship name (``"mappings"``) or a
        pre-built selectinload chain (``selectinload(DBTrack.mappings).selectinload(...)``)
        for nested relationships that can't be expressed as a simple string.
        """
        return []

    @classmethod
    async def map_collection(
        cls,
        db_models: list[TDBModel],
    ) -> list[TDomainModel]:
        """Map a collection of DB models to domain models."""
        ...


class SessionAwareMapper[TDBModel: DatabaseModel, TDomainModel](
    ModelMapper[TDBModel, TDomainModel], Protocol
):
    """Protocol for mappers that support session-aware domain conversion.

    Some mappers (e.g., TrackMapper) need a session to auto-heal missing
    relationships during mapping. This protocol enables type-safe narrowing
    via TypeIs instead of cast(Any, ...).
    """

    @staticmethod
    async def to_domain_with_session(
        db_model: TDBModel, session: AsyncSession | None = None
    ) -> TDomainModel: ...


def has_session_support[TDBModel: DatabaseModel, TDomainModel](
    mapper: ModelMapper[TDBModel, TDomainModel],
) -> TypeIs[SessionAwareMapper[TDBModel, TDomainModel]]:
    """Type guard for session-aware mappers.

    Narrows mapper type so the caller can safely call to_domain_with_session()
    without cast(Any, ...).
    """
    return hasattr(mapper, "to_domain_with_session")


@define(frozen=True, slots=True)
class BaseModelMapper[TDBModel: DatabaseModel, TDomainModel]:
    """Base implementation of ModelMapper with common functionality.

    This provides a foundation for building domain-specific mappers
    with consistent behavior and reduced boilerplate.

    Usage:
        @define(frozen=True, slots=True)
        class UserMapper(BaseModelMapper[DBUser, User]):
            @staticmethod
            async def to_domain(db_model: DBUser) -> User:
                if not db_model:
                    return None
                return User(...)

            @staticmethod
            def to_db(domain_model: User) -> DBUser:
                return DBUser(...)

            @staticmethod
            def get_default_relationships() -> list[str]:
                return ["roles", "preferences"]
    """

    @staticmethod
    async def to_domain(db_model: TDBModel) -> TDomainModel:
        """Default implementation raises NotImplementedError."""
        _ = db_model
        raise NotImplementedError("Subclasses must implement to_domain")

    @staticmethod
    def to_db(domain_model: TDomainModel) -> TDBModel:
        """Default implementation raises NotImplementedError."""
        _ = domain_model
        raise NotImplementedError("Subclasses must implement to_db")

    @staticmethod
    def get_default_relationships() -> Sequence[str | ORMOption]:
        """Define relationships to load for this model.

        Subclasses can return either string names or pre-built selectinload chains.
        Override in each concrete mapper — the base returns ``[]``.
        """
        return []

    @classmethod
    async def map_collection(
        cls,
        db_models: list[TDBModel],
    ) -> list[TDomainModel]:
        """Map a collection of DB models to domain models.

        This is a convenience method that handles None values
        and performs the mapping operation in a consistent way.

        Uses cls.to_domain to ensure the subclass implementation is called,
        not BaseModelMapper.to_domain directly.
        """
        if not db_models:
            return []

        domain_models: list[TDomainModel] = []
        for db_model in db_models:
            domain_model = await cls.to_domain(db_model)
            if domain_model:
                domain_models.append(domain_model)

        return domain_models


class SimpleMapperFactory:
    """Factory to create simple mappers for 1:1 field mappings.

    This eliminates boilerplate for simple mappers that just copy fields between
    database and domain models without complex transformations or relationship handling.

    Usage:
        TrackLikeMapper = SimpleMapperFactory.create(DBTrackLike, TrackLike)
    """

    @staticmethod
    def create[TDBModel: DatabaseModel, TDomainModel](
        db_class: type[TDBModel], domain_class: type[TDomainModel]
    ) -> type[BaseModelMapper[TDBModel, TDomainModel]]:
        """Create a mapper class for the given DB and domain classes."""
        import attrs

        @define(frozen=True, slots=True)
        class GeneratedMapper(BaseModelMapper[TDBModel, TDomainModel]):
            """Auto-generated mapper for simple 1:1 field mappings."""

            @override
            @staticmethod
            def get_default_relationships() -> Sequence[str | ORMOption]:
                """Simple mappers typically don't need relationships loaded."""
                return []

            @override
            @staticmethod
            async def to_domain(db_model: TDBModel) -> TDomainModel:
                """Convert database model to domain model using attrs field mapping."""
                if attrs.has(domain_class):
                    # Only pass fields that accept __init__ kwargs — fields with
                    # init=False (e.g. derived fields via Factory(takes_self=True))
                    # reject constructor kwargs and compute their own values.
                    field_names: list[str] = [
                        f.name
                        for f in cast(
                            "tuple[attrs.Attribute[object], ...]",
                            attrs.fields(domain_class),
                        )
                        if f.init
                    ]

                    # Extract values from db_model for each field
                    kwargs: dict[str, object] = {}
                    for field_name in field_names:
                        if hasattr(db_model, field_name):
                            kwargs[field_name] = getattr(db_model, field_name)

                    return domain_class(**kwargs)
                raise NotImplementedError(
                    f"Domain class {domain_class} must use attrs.define"
                )

            @override
            @staticmethod
            def to_db(domain_model: TDomainModel) -> TDBModel:
                """Convert domain model to database model using attrs field mapping."""
                if attrs.has(type(domain_model)):
                    # attrs.asdict returns dict[str, Any] — values are reflective.
                    domain_dict: dict[str, object] = attrs.asdict(
                        domain_model, recurse=False
                    )

                    # Filter to only include fields that exist on the database class
                    db_kwargs: dict[str, object] = {
                        key: value
                        for key, value in domain_dict.items()
                        if hasattr(db_class, key)
                    }

                    return db_class(**db_kwargs)
                raise NotImplementedError(
                    f"Domain model {type(domain_model)} must use attrs.define"
                )

        return GeneratedMapper
