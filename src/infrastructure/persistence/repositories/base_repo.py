"""Repository layer for database operations with SQLAlchemy 2.0 best practices."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
import functools
import inspect as pyinspect
import operator
from typing import Any, NoReturn, Protocol, TypeVar, cast

from attrs import define
from sqlalchemy import Select, delete, func, insert, inspect, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, selectinload
from sqlalchemy.sql import ColumnElement

from src.config import get_logger

# Import needed for relationship chains in eager loading
from src.infrastructure.persistence.database.db_models import DatabaseModel
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

# Type variables with proper constraints
TDBModel = TypeVar("TDBModel", bound=DatabaseModel)
TDomainModel = TypeVar("TDomainModel")

logger = get_logger(__name__)

# -------------------------------------------------------------------------
# COMMON UTILITIES
# -------------------------------------------------------------------------


def _normalize_to_list(result: Any) -> list[Any]:
    """Normalize a result to a list (helper for safe_fetch_relationship)."""
    if result is None:
        return []
    return result if isinstance(result, list) else [result]


async def safe_fetch_relationship(db_model: Any, rel_name: str) -> list[Any]:
    """Helper to safely load relationships using AsyncAttrs.awaitable_attrs.

    This function uses a single, consistent approach for safely accessing
    relationship attributes in async context using SQLAlchemy 2.0 best practices.

    Returns:
        Always returns a list for consistent handling. For single-entity relationships,
        callers should access the first element in the list. For empty results, the list
        will be empty.
    """
    try:
        # Standard SQLAlchemy 2.0 pattern: use awaitable_attrs
        if hasattr(db_model, "awaitable_attrs"):
            result = await getattr(db_model.awaitable_attrs, rel_name)
            return _normalize_to_list(result)
        # Simple fallback for non-AsyncAttrs models
        elif hasattr(db_model, rel_name):
            return _normalize_to_list(getattr(db_model, rel_name))
        return []
    except Exception:
        return []


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
    def get_default_relationships() -> list[str | Any]:
        """Get default relationships to load for this model."""
        return []

    @staticmethod
    async def map_collection(
        db_models: list[TDBModel],
    ) -> list[TDomainModel]:
        """Map a collection of DB models to domain models."""
        ...


def has_session_support(mapper: Any) -> bool:
    """Modern Python 3.13 type guard for session-aware mappers.

    Uses hasattr() for runtime detection but provides type safety.
    This is the recommended approach for optional protocol methods in 2025.
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
        """Default implementation returns None for None input."""
        if not db_model:
            return None
        raise NotImplementedError("Subclasses must implement to_domain")

    @staticmethod
    def to_db(domain_model: TDomainModel) -> TDBModel:
        """Default implementation raises NotImplementedError."""
        raise NotImplementedError("Subclasses must implement to_db")

    @staticmethod
    def get_default_relationships() -> list[str | Any]:
        """Define relationships to load for this model."""
        return ["mappings", "mappings.connector_track"]

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

        domain_models = []
        for db_model in db_models:
            domain_model = await cls.to_domain(db_model)
            if domain_model:
                domain_models.append(domain_model)

        return domain_models


class SimpleMapperFactory[TDBModel: DatabaseModel, TDomainModel]:
    """Factory to create simple mappers for 1:1 field mappings.

    This eliminates boilerplate for simple mappers that just copy fields between
    database and domain models without complex transformations or relationship handling.

    Usage:
        TrackLikeMapper = SimpleMapperFactory.create(DBTrackLike, TrackLike)
    """

    @staticmethod
    def create(
        db_class: type[TDBModel], domain_class: type[TDomainModel]
    ) -> type[BaseModelMapper[TDBModel, TDomainModel]]:
        """Create a mapper class for the given DB and domain classes."""
        import attrs

        @define(frozen=True, slots=True)
        class GeneratedMapper(BaseModelMapper[TDBModel, TDomainModel]):
            """Auto-generated mapper for simple 1:1 field mappings."""

            @staticmethod
            def get_default_relationships() -> list[str]:
                """Simple mappers typically don't need relationships loaded."""
                return []

            @staticmethod
            async def to_domain(db_model: TDBModel) -> TDomainModel:
                """Convert database model to domain model using attrs field mapping."""
                if not db_model:
                    return None

                if attrs.has(domain_class):
                    # Get all field names from the domain class
                    field_names = [field.name for field in attrs.fields(domain_class)]

                    # Extract values from db_model for each field
                    kwargs = {}
                    for field_name in field_names:
                        if hasattr(db_model, field_name):
                            kwargs[field_name] = getattr(db_model, field_name)

                    return domain_class(**kwargs)
                else:
                    raise NotImplementedError(
                        f"Domain class {domain_class} must use attrs.define"
                    )

            @staticmethod
            def to_db(domain_model: TDomainModel) -> TDBModel:
                """Convert domain model to database model using attrs field mapping."""
                if attrs.has(type(domain_model)):
                    # Get all attributes from the domain model
                    domain_dict = attrs.asdict(domain_model, recurse=False)

                    # Filter to only include fields that exist on the database class
                    db_kwargs = {
                        key: value
                        for key, value in domain_dict.items()
                        if hasattr(db_class, key)
                    }

                    return db_class(**db_kwargs)
                else:
                    raise NotImplementedError(
                        f"Domain model {type(domain_model)} must use attrs.define"
                    )

        return GeneratedMapper


class BaseRepository[TDBModel: DatabaseModel, TDomainModel]:
    """Base repository for database operations with SQLAlchemy 2.0 best practices."""

    def __init__(
        self,
        session: AsyncSession,
        model_class: type[TDBModel],
        mapper: ModelMapper[TDBModel, TDomainModel],
    ) -> None:
        """Initialize repository with session and model mappings."""
        self.session = session
        self.model_class = model_class
        self.mapper = mapper

    # -------------------------------------------------------------------------
    # RELATIONSHIP UTILITIES
    # -------------------------------------------------------------------------

    def _extract_relationship_names(self, rel_items: list[str | Any]) -> list[str]:
        """Extract string names from relationship list (handles strings and selectinload objects).

        This utility handles the common pattern of extracting relationship attribute names
        from a list that may contain either string names or selectinload() objects.

        Args:
            rel_items: List of relationship specifications (strings or selectinload objects)

        Returns:
            List of relationship attribute names as strings
        """
        rel_names = []
        for rel_item in rel_items:
            if isinstance(rel_item, str):
                rel_names.append(rel_item)
            # For selectinload objects, extract the attribute name
            elif hasattr(rel_item, "path") and rel_item.path:
                # Get the first path element (the direct relationship)
                path_element = rel_item.path[0]
                if hasattr(path_element, "key"):
                    rel_names.append(path_element.key)
                elif hasattr(path_element, "property") and hasattr(
                    path_element.property, "key"
                ):
                    rel_names.append(path_element.property.key)
        return rel_names

    def _build_relationship_options(
        self, rel_items: list[str | Any], skip_nested: bool = True
    ) -> list[Any]:
        """Build selectinload options from relationship specifications.

        This utility builds a list of selectinload options for use with session.get()
        or query options, handling both string names and existing selectinload objects.

        Args:
            rel_items: List of relationship specifications (strings or selectinload objects)
            skip_nested: If True, skip relationships with dots in their names (default: True)

        Returns:
            List of selectinload options ready for use with SQLAlchemy queries
        """
        options = []
        for rel_item in rel_items:
            if isinstance(rel_item, str):
                rel_name = rel_item
                # Skip nested relationships if requested
                if skip_nested and "." in rel_name:
                    continue

                # Only add relationships that actually exist on this model class
                if (
                    hasattr(self.model_class, rel_name)
                    and rel_name in inspect(self.model_class).relationships
                ):
                    options.append(selectinload(getattr(self.model_class, rel_name)))
            else:
                # It's already a selectinload object, just use it directly
                options.append(rel_item)
        return options

    # -------------------------------------------------------------------------
    # SELECT STATEMENT BUILDERS
    # -------------------------------------------------------------------------

    def select(self, *columns: Any) -> Select[tuple[Any, ...]]:
        """Create select statement for records."""
        return select(*columns) if columns else select(self.model_class)

    def select_by_id(self, id_: int) -> Select[tuple[TDBModel]]:
        """Create select statement for a record by ID."""
        return select(self.model_class).where(self.model_class.id == id_)

    def select_by_ids(self, ids: list[int]) -> Select[tuple[TDBModel]]:
        """Create select statement for multiple records by ID."""
        if not ids:
            # Return empty result statement
            return select(self.model_class).where(func.false())
        return select(self.model_class).where(self.model_class.id.in_(ids))

    def order_by(
        self, stmt: Select[tuple[TDBModel]], field: str, ascending: bool = True
    ) -> Select[tuple[TDBModel]]:
        """Add ordering to a select statement."""
        order_col = getattr(self.model_class, field)
        return stmt.order_by(order_col if ascending else order_col.desc())

    def with_relationship(
        self,
        stmt: Select[tuple[TDBModel]],
        *relationships: str | InstrumentedAttribute | Any,
    ) -> Select[tuple[TDBModel]]:
        """Add relationship loading options to select statement.

        Supports SQLAlchemy 2.1 best practices with mixed types:
        - str: Simple relationship name (e.g., "mappings")
        - InstrumentedAttribute: Direct SQLAlchemy attribute
        - selectinload objects: Pre-constructed loader options
        """
        options = []
        for rel in relationships:
            if isinstance(rel, str):
                # Simple string relationship name
                options.append(selectinload(getattr(self.model_class, rel)))
            elif hasattr(
                rel, "__module__"
            ) and "sqlalchemy.orm.strategy_options" in str(rel.__class__.__module__):
                # Pre-constructed SQLAlchemy loader option (selectinload, joinedload, etc.)
                options.append(rel)
            else:
                # InstrumentedAttribute - wrap in selectinload
                options.append(selectinload(rel))
        return stmt.options(*options)

    def with_default_relationships(
        self, stmt: Select[tuple[TDBModel]]
    ) -> Select[tuple[TDBModel]]:
        """Add default relationships for this model."""
        rels = self.mapper.get_default_relationships()
        if not rels:
            return stmt
        return self.with_relationship(stmt, *rels)

    def count(
        self, conditions: dict[str, Any] | list[ColumnElement] | None = None
    ) -> Select:
        """Create a count statement for records matching conditions."""
        stmt = select(func.count(self.model_class.id))

        # Apply additional conditions
        if conditions:
            match conditions:
                case dict():
                    for field, value in conditions.items():
                        stmt = stmt.where(getattr(self.model_class, field) == value)
                case list():
                    for condition in conditions:
                        stmt = stmt.where(condition)

        return stmt

    # -------------------------------------------------------------------------
    # DIRECT DATABASE OPERATIONS (non-decorated helpers)
    # -------------------------------------------------------------------------

    async def _execute_query(
        self,
        stmt: Select[tuple[TDBModel]],
    ) -> list[TDBModel]:
        """Execute a query and return all results directly."""
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def _execute_query_one(
        self,
        stmt: Select[tuple[TDBModel]],
    ) -> TDBModel | None:
        """Execute a query and return the first result directly."""
        result = await self.session.execute(stmt)
        return (
            result.scalar_one_or_none()
        )  # Use scalar_one_or_none for cleaner handling

    # -------------------------------------------------------------------------
    # DECORATED DATABASE OPERATIONS
    # -------------------------------------------------------------------------

    @db_operation("execute_select_one")
    async def execute_select_one(
        self,
        stmt: Select[tuple[TDBModel]],
    ) -> TDBModel | None:
        """Execute select and return first result."""
        try:
            return await self._execute_query_one(stmt)
        except Exception as e:
            if "concurrent operations are not permitted" in str(e):
                # Handle concurrent session access
                logger.warning("Detected concurrent session access, retrying operation")
                await asyncio.sleep(0.1)
                return await self._execute_query_one(stmt)
            raise

    # -------------------------------------------------------------------------
    # CORE CRUD OPERATIONS
    # -------------------------------------------------------------------------

    @db_operation("get_by_id")
    async def get_by_id(
        self,
        id_: int,
        load_relationships: list[str] | None = None,
    ) -> TDomainModel:
        """Get entity by ID - degenerate case of batch operation."""
        # Single operations are just batch([single_item]) - batch-first principle
        results = await self.get_by_ids([id_], load_relationships)

        if not results:
            raise ValueError(f"Entity with ID {id_} not found")

        return results[0]

    @db_operation("get_by_ids")
    async def get_by_ids(
        self,
        ids: list[int],
        load_relationships: list[str] | None = None,
    ) -> list[TDomainModel]:
        """Get multiple entities by IDs - batch-first primary implementation."""
        if not ids:
            return []

        stmt = self.select_by_ids(ids)

        if load_relationships:
            stmt = self.with_relationship(stmt, *load_relationships)
        else:
            stmt = self.with_default_relationships(stmt)

        db_entities = await self._execute_query(stmt)

        # Use session-aware mapping for each entity (single code path)
        domain_models = []
        for db_entity in db_entities:
            if not db_entity:
                continue

            if has_session_support(self.mapper):
                domain_model = await cast(Any, self.mapper).to_domain_with_session(
                    db_entity, self.session
                )
            else:
                domain_model = await self.mapper.to_domain(db_entity)

            if domain_model:
                domain_models.append(domain_model)

        return domain_models

    @db_operation("find_by")
    async def find_by(
        self,
        conditions: dict[str, Any] | list[ColumnElement],
        load_relationships: list[str] | None = None,
        limit: int | None = None,
        order_by: tuple[str, bool] | None = None,
    ) -> list[TDomainModel]:
        """Find entities matching conditions."""
        # Build the query directly with SQLAlchemy 2.0 syntax
        stmt = select(self.model_class)

        # Apply conditions
        match conditions:
            case dict():
                for field, value in conditions.items():
                    stmt = stmt.where(getattr(self.model_class, field) == value)
            case list():
                for condition in conditions:
                    stmt = stmt.where(condition)

        # Apply relationship loading
        if load_relationships:
            options = [
                selectinload(getattr(self.model_class, rel))
                for rel in load_relationships
            ]
            stmt = stmt.options(*options)
        else:
            default_rels = self.mapper.get_default_relationships()
            if default_rels:
                options = [
                    selectinload(getattr(self.model_class, rel)) for rel in default_rels
                ]
                stmt = stmt.options(*options)

        # Apply ordering if specified
        if order_by:
            field, ascending = order_by
            column = getattr(self.model_class, field)
            stmt = stmt.order_by(column if ascending else column.desc())

        # Apply limit
        if limit is not None:
            stmt = stmt.limit(limit)

        # Execute query directly
        db_entities = await self._execute_query(stmt)

        # Use the mapper's map_collection method
        return await self.mapper.map_collection(db_entities)

    @db_operation("find_one_by")
    async def find_one_by(
        self,
        conditions: dict[str, Any] | list[ColumnElement],
        load_relationships: list[str] | None = None,
    ) -> TDomainModel | None:
        """Find a single entity matching conditions or None if not found."""
        # For direct ID lookups, use session.get instead of query
        if isinstance(conditions, dict) and len(conditions) == 1 and "id" in conditions:
            # Use session.get with explicit eager loading for better performance
            db_entity = await self.session.get(
                self.model_class,
                conditions["id"],
                options=[
                    selectinload(getattr(self.model_class, rel))
                    for rel in (
                        load_relationships or self.mapper.get_default_relationships()
                    )
                    if hasattr(self.model_class, rel)
                ],
            )

            if not db_entity:
                return None

            if has_session_support(self.mapper):
                return await cast(Any, self.mapper).to_domain_with_session(
                    db_entity, self.session
                )
            else:
                return await self.mapper.to_domain(db_entity)

        # For other conditions, use a query
        stmt = select(self.model_class)

        # Apply conditions
        match conditions:
            case dict():
                for field, value in conditions.items():
                    stmt = stmt.where(getattr(self.model_class, field) == value)
            case list():
                for condition in conditions:
                    stmt = stmt.where(condition)

        # Load relationships
        rel_names = load_relationships or self.mapper.get_default_relationships()

        rel_options = [
            selectinload(getattr(self.model_class, rel))
            for rel in rel_names
            if hasattr(self.model_class, rel)
        ]

        if rel_options:
            stmt = stmt.options(*rel_options)

        # Limit to one result and execute
        stmt = stmt.limit(1)
        result = await self.session.execute(stmt)
        db_entity = result.scalar_one_or_none()

        if not db_entity:
            return None

        if has_session_support(self.mapper):
            return await cast(Any, self.mapper).to_domain_with_session(
                db_entity, self.session
            )
        else:
            return await self.mapper.to_domain(db_entity)

    @db_operation("update")
    async def update(
        self,
        id_: int,
        updates: dict[str, Any] | TDomainModel,
    ) -> TDomainModel:
        """Update entity using UPDATE ... RETURNING with optimized relationship loading.

        Uses identity map pattern for efficient relationship loading instead of
        session.refresh(), reducing queries while maintaining UoW semantics.
        """
        # Get values to update
        if isinstance(updates, dict):
            values = {**updates}
            if "updated_at" not in values:
                values["updated_at"] = datetime.now(UTC)
        else:
            # Convert domain model to db model
            update_db = self.mapper.to_db(updates)

            # Get a list of column names from the model class
            columns = [c.key for c in inspect(self.model_class).columns]

            # Only include attributes that are actual columns in the table
            values = {
                k: getattr(update_db, k)
                for k in columns
                if hasattr(update_db, k)
                and getattr(update_db, k) is not None
                and k != "id"
            }
            values["updated_at"] = datetime.now(UTC)

        # Execute update with RETURNING
        stmt = (
            update(self.model_class)
            .where(self.model_class.id == id_)
            .values(**values)
            .returning(self.model_class)
        )

        result = await self.session.execute(stmt)
        updated_entity = result.scalar_one_or_none()

        if not updated_entity:
            raise ValueError(f"Entity with ID {id_} not found or already deleted")

        # Load relationships efficiently via identity map
        await self._load_relationships_via_identity_map([updated_entity])

        if has_session_support(self.mapper):
            return await cast(Any, self.mapper).to_domain_with_session(
                updated_entity, self.session
            )
        else:
            return await self.mapper.to_domain(updated_entity)

    @db_operation("delete")
    async def delete(self, id_: int) -> int:
        """Delete entity with ORM-enabled DELETE."""
        stmt = (
            delete(self.model_class)
            .where(self.model_class.id == id_)
            .returning(self.model_class.id)
            .execution_options(synchronize_session=False)
        )

        result = await self.session.execute(stmt)
        deleted_ids = result.scalars().all()

        if not deleted_ids:
            raise ValueError(f"Entity with ID {id_} not found")

        return len(deleted_ids)

    # -------------------------------------------------------------------------
    # RELATIONSHIP LOADING OPTIMIZATION
    # -------------------------------------------------------------------------

    async def _load_relationships_via_identity_map(
        self,
        db_entities: list[TDBModel],
    ) -> None:
        """Load relationships using selectinload + identity map pattern.

        CRITICAL: This leverages SQLAlchemy's identity map to return
        THE SAME object instances but with relationships populated.
        Works across repository boundaries in Unit of Work.

        This optimization reduces queries from O(N×R) to O(1+R) where:
        - N = number of entities
        - R = number of relationships

        For 100 entities with 3 relationships:
        - Before: 401 queries (1 + 100 + 300)
        - After: 5 queries (1 + 1 + 3)

        See tests:
        - tests/unit/infrastructure/persistence/test_identity_map_behavior.py
        - tests/unit/infrastructure/persistence/test_bulk_uow_patterns.py

        Args:
            db_entities: List of database entities already in session

        Returns:
            None (mutates entities via identity map)
        """
        # Early return if no entities or no relationships to load
        if not db_entities or not self.mapper.get_default_relationships():
            return

        # Filter out entities without IDs (shouldn't happen, but defensive)
        entity_ids = [e.id for e in db_entities if e.id is not None]
        if not entity_ids:
            return

        # Build query with relationship options
        stmt = select(self.model_class).where(self.model_class.id.in_(entity_ids))
        stmt = self.with_default_relationships(stmt)

        # Execute - SQLAlchemy populates relationships on original objects
        await self.session.execute(stmt)

    # -------------------------------------------------------------------------
    # TRANSACTION MANAGEMENT
    # -------------------------------------------------------------------------

    @db_operation("transaction")
    async def execute_transaction[T](
        self,
        operation: Callable[[], T | Awaitable[T]],
    ) -> T:
        """Execute operation within a transaction. Returns operation result."""
        # Start a savepoint (nested transaction)
        async with self.session.begin_nested():
            if pyinspect.iscoroutinefunction(operation):
                # If it's an async function, await it directly
                return await operation()

            # For non-async functions, we need to handle both regular and awaitable returns
            result = operation()  # Don't explicitly type this variable

            # Use is_coroutine instead of is_awaitable for better Pylance compatibility
            if asyncio.iscoroutine(result):
                return await result

            # If it's not a coroutine, it must be T directly
            return cast(T, result)

    # -------------------------------------------------------------------------
    # GET OR CREATE PATTERN
    # -------------------------------------------------------------------------

    @db_operation("upsert")
    async def upsert(
        self,
        lookup_attrs: dict[str, Any],
        create_attrs: dict[str, Any] | None = None,
    ) -> TDomainModel:
        """Upsert an entity using a two-phase approach to avoid implicit IO and greenlet issues.

        This implementation follows SQLAlchemy 2.0 best practices for async by:
        1. Using a two-phase approach to avoid complex lazy loading chains
        2. Using explicit eager loading with selectinload for relationships
        3. Using session.get with options for fetching entities with relationships
        4. Never relying on implicit lazy loading of relationships
        """
        # Combine lookup and create attributes for the insert operation
        insert_values = {**lookup_attrs}
        if create_attrs:
            insert_values.update(create_attrs)

        def _raise_update_retrieval_error() -> NoReturn:
            raise ValueError("Failed to retrieve entity after update")

        def _raise_create_retrieval_error() -> NoReturn:
            raise ValueError("Failed to retrieve entity after create")

        # Add timestamps
        now = datetime.now(UTC)
        if "created_at" not in insert_values:
            insert_values["created_at"] = now
        if "updated_at" not in insert_values:
            insert_values["updated_at"] = now

        try:
            # Phase 1: Try to find existing entity with lookup attributes
            # This avoids the complex lazy loading chains that cause greenlet issues
            lookup_query = select(self.model_class.id)

            # Add lookup conditions
            for field, value in lookup_attrs.items():
                lookup_query = lookup_query.where(
                    getattr(self.model_class, field) == value
                )

            # Execute query to get ID only
            result = await self.session.execute(lookup_query)
            existing_id = result.scalar_one_or_none()

            if existing_id:
                # Entity exists, update it by ID
                update_values = {
                    k: v
                    for k, v in insert_values.items()
                    if k != "created_at" and k not in lookup_attrs
                }
                update_values["updated_at"] = now  # Always update timestamp

                # Execute update
                await self.session.execute(
                    update(self.model_class)
                    .where(self.model_class.id == existing_id)
                    .values(**update_values)
                )

                # Fetch updated entity with basic eager loading of direct relationships only
                options = self._build_relationship_options(
                    self.mapper.get_default_relationships()
                )

                # Use session.get with eager loading - this is the recommended pattern
                # for safely loading entities in an async context
                db_entity = await self.session.get(
                    self.model_class, existing_id, options=options
                )

                # Convert to domain model
                if db_entity is None:
                    _raise_update_retrieval_error()
                if has_session_support(self.mapper):
                    return await cast(Any, self.mapper).to_domain_with_session(
                        db_entity, self.session
                    )
                else:
                    return await self.mapper.to_domain(db_entity)
            else:
                # Phase 2: Entity doesn't exist, create it
                # Use simple insert instead of complex on_conflict_do_update
                stmt = (
                    insert(self.model_class)
                    .values(**insert_values)
                    .returning(self.model_class.id)
                )
                result = await self.session.execute(stmt)
                new_id = result.scalar_one()

                # Fetch newly created entity with basic eager loading of direct relationships only
                options = self._build_relationship_options(
                    self.mapper.get_default_relationships()
                )

                # Use session.get with eager loading for all needed relationships
                db_entity = await self.session.get(
                    self.model_class, new_id, options=options
                )

                # Convert to domain model
                if db_entity is None:
                    _raise_create_retrieval_error()
                if has_session_support(self.mapper):
                    return await cast(Any, self.mapper).to_domain_with_session(
                        db_entity, self.session
                    )
                else:
                    return await self.mapper.to_domain(db_entity)

        except Exception as e:
            logger.error(f"Upsert error: {e}")
            raise

    @db_operation("bulk_upsert")
    async def bulk_upsert(
        self,
        entities: list[dict[str, Any]],
        lookup_keys: list[str],
        return_models: bool = True,
    ) -> list[TDomainModel] | int:
        """Perform bulk upsert optimized for SQLite.

        Uses SQLAlchemy 2.0 INSERT ... ON CONFLICT with RETURNING clause,
        followed by efficient relationship loading via identity map pattern.

        Performance: O(1+R) queries regardless of entity count, where R is
        the number of relationships. For 100 entities with 3 relationships:
        - This implementation: 5 queries
        - Naive approach: 401 queries (80x improvement)

        Args:
            entities: List of dictionaries with entity attributes
            lookup_keys: Keys to use for looking up existing entities
            return_models: Whether to return domain models or count

        Returns:
            List of domain models or count of affected rows
        """
        if not entities:
            return [] if return_models else 0

        # Add timestamps to all entities
        now = datetime.now(UTC)
        for entity in entities:
            if "created_at" not in entity:
                entity["created_at"] = now
            if "updated_at" not in entity:
                entity["updated_at"] = now

        try:
            # SQLite-specific bulk upsert
            stmt = sqlite_insert(self.model_class).values(entities)

            # Determine which columns to update (exclude lookup keys and id)
            all_keys = set(
                functools.reduce(
                    operator.iadd, [list(entity.keys()) for entity in entities], []
                )
            )
            update_keys = all_keys - set(lookup_keys) - {"id"}

            # Create update_dict using the excluded values
            update_dict = {
                key: getattr(stmt.excluded, key)
                for key in update_keys
                if hasattr(stmt.excluded, key)
            }

            # Add the ON CONFLICT clause
            stmt = stmt.on_conflict_do_update(
                index_elements=[getattr(self.model_class, k) for k in lookup_keys],
                set_=update_dict,
            )

            # Add RETURNING clause if needed
            if return_models:
                stmt = stmt.returning(self.model_class)

            # Execute the statement
            result = await self.session.execute(stmt)

            if return_models:
                db_entities = list(result.scalars().all())

                # Load relationships efficiently via identity map
                await self._load_relationships_via_identity_map(db_entities)

                return await self.mapper.map_collection(db_entities)
            else:
                return len(entities)

        except Exception as e:
            logger.debug(f"SQLite bulk upsert failed, using individual upserts: {e}")

            # Fall back to individual upserts
            results = []
            count = 0

            for entity_dict in entities:
                lookup_dict = {
                    k: entity_dict[k] for k in lookup_keys if k in entity_dict
                }
                create_dict = {
                    k: v for k, v in entity_dict.items() if k not in lookup_keys
                }

                entity = await self.upsert(lookup_dict, create_dict)
                count += 1

                if return_models:
                    results.append(entity)

            return results if return_models else count
