"""Repository layer for database operations with SQLAlchemy 2.0 best practices."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
import functools
import operator
from typing import Any, Protocol, TypeVar, cast

from attrs import define
from sqlalchemy import Select, delete, func, insert, inspect, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, selectinload
from sqlalchemy.sql import ColumnElement

from src.config import get_logger

# Import needed for relationship chains in eager loading
from src.infrastructure.persistence.database.db_models import NaradaDBBase
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

# Type variables with proper constraints
TDBModel = TypeVar("TDBModel", bound=NaradaDBBase)
TDomainModel = TypeVar("TDomainModel")

logger = get_logger(__name__)

# -------------------------------------------------------------------------
# COMMON UTILITIES
# -------------------------------------------------------------------------


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
            # Ensure consistent return type (always list)
            if result is None:
                return []
            if isinstance(result, list):
                return result
            return [result]
        # Simple fallback for non-AsyncAttrs models
        elif hasattr(db_model, rel_name):
            result = getattr(db_model, rel_name)
            if result is None:
                return []
            if isinstance(result, list):
                return result
            return [result]
        return []
    except Exception:
        return []


def filter_active(model_class: type[NaradaDBBase]) -> ColumnElement:
    """Return a filter expression for active (non-deleted) entities."""
    return model_class.is_deleted == False  # noqa: E712


class ModelMapper[TDBModel: NaradaDBBase, TDomainModel](Protocol):
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
    def get_default_relationships() -> list[str]:
        """Get default relationships to load for this model."""
        return []

    @staticmethod
    async def map_collection(
        db_models: list[TDBModel],
    ) -> list[TDomainModel]:
        """Map a collection of DB models to domain models."""
        ...


@define(frozen=True, slots=True)
class BaseModelMapper[TDBModel: NaradaDBBase, TDomainModel]:
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
    def get_default_relationships() -> list[str]:
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


class BaseRepository[TDBModel: NaradaDBBase, TDomainModel]:
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
    # SELECT STATEMENT BUILDERS
    # -------------------------------------------------------------------------

    def select(self, *columns: Any) -> Select[tuple[Any, ...]]:
        """Create select statement for active records."""
        stmt = select(*columns) if columns else select(self.model_class)
        return stmt.where(self.model_class.is_deleted == False)  # noqa: E712

    def select_by_id(self, id_: int) -> Select[tuple[TDBModel]]:
        """Create select statement for a record by ID."""
        return select(self.model_class).where(
            self.model_class.id == id_,
            self.model_class.is_deleted == False,  # noqa: E712
        )

    def select_by_ids(self, ids: list[int]) -> Select[tuple[TDBModel]]:
        """Create select statement for multiple records by ID."""
        if not ids:
            # Return empty result statement
            return select(self.model_class).where(func.false())
        return select(self.model_class).where(
            self.model_class.id.in_(ids),
            self.model_class.is_deleted == False,  # noqa: E712
        )

    def order_by(
        self, stmt: Select[tuple[TDBModel]], field: str, ascending: bool = True
    ) -> Select[tuple[TDBModel]]:
        """Add ordering to a select statement."""
        order_col = getattr(self.model_class, field)
        return stmt.order_by(order_col if ascending else order_col.desc())

    def with_relationship(
        self,
        stmt: Select[tuple[TDBModel]],
        *relationships: str | InstrumentedAttribute,
    ) -> Select[tuple[TDBModel]]:
        """Add relationship loading options to select statement."""
        options = []
        for rel in relationships:
            if isinstance(rel, str):
                options.append(selectinload(getattr(self.model_class, rel)))
            else:
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
        stmt = select(func.count(self.model_class.id)).where(
            self.model_class.is_deleted == False  # noqa: E712
        )

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
            if not db_entity or db_entity.is_deleted:
                continue
                
            if hasattr(self.mapper, 'to_domain_with_session'):
                domain_model = await self.mapper.to_domain_with_session(db_entity, self.session)  # type: ignore[attr-defined]
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
        stmt = select(self.model_class).where(
            self.model_class.is_deleted == False  # noqa: E712
        )

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

            if not db_entity or db_entity.is_deleted:
                return None

            if hasattr(self.mapper, 'to_domain_with_session'):
                return await self.mapper.to_domain_with_session(db_entity, self.session)  # type: ignore[attr-defined]  # type: ignore[attr-defined]
            else:
                return await self.mapper.to_domain(db_entity)

        # For other conditions, use a query
        stmt = select(self.model_class).where(
            self.model_class.is_deleted == False  # noqa: E712
        )

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

        if hasattr(self.mapper, 'to_domain_with_session'):
            return await self.mapper.to_domain_with_session(db_entity, self.session)  # type: ignore[attr-defined]
        else:
            return await self.mapper.to_domain(db_entity)

    @db_operation("update")
    async def update(
        self,
        id_: int,
        updates: dict[str, Any] | TDomainModel,
    ) -> TDomainModel:
        """Update entity with a unified approach."""
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
            .where(
                self.model_class.id == id_,
                self.model_class.is_deleted == False,  # noqa: E712
            )
            .values(**values)
            .returning(self.model_class)
        )

        result = await self.session.execute(stmt)
        updated_entity = result.scalar_one_or_none()

        if not updated_entity:
            raise ValueError(f"Entity with ID {id_} not found or already deleted")

        # Return updated entity with relationships
        await self.session.refresh(
            updated_entity, attribute_names=self.mapper.get_default_relationships()
        )
        if hasattr(self.mapper, 'to_domain_with_session'):
            return await self.mapper.to_domain_with_session(updated_entity, self.session)  # type: ignore[attr-defined]
        else:
            return await self.mapper.to_domain(updated_entity)

    @db_operation("soft_delete")
    async def soft_delete(self, id_: int) -> int:
        """Soft delete with execution options."""
        stmt = (
            update(self.model_class)
            .where(
                self.model_class.id == id_,
                filter_active(self.model_class),
            )
            .values(
                is_deleted=True,
                deleted_at=datetime.now(UTC),
            )
            .execution_options(synchronize_session=False)
        )
        result = await self.session.execute(stmt)

        if result.rowcount == 0:
            raise ValueError(f"Entity with ID {id_} not found or already deleted")

        return result.rowcount

    @db_operation("hard_delete")
    async def hard_delete(self, id_: int) -> int:
        """Hard delete with ORM-enabled DELETE."""
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
            if asyncio.iscoroutinefunction(operation):
                # If it's an async function, await it directly
                return await operation()

            # For non-async functions, we need to handle both regular and awaitable returns
            result = operation()  # Don't explicitly type this variable

            # Use is_coroutine instead of is_awaitable for better Pylance compatibility
            if asyncio.iscoroutine(result):
                return await result

            # If it's not a coroutine, it must be T directly
            return cast("T", result)

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

        # Add timestamps
        now = datetime.now(UTC)
        if "created_at" not in insert_values:
            insert_values["created_at"] = now
        if "updated_at" not in insert_values:
            insert_values["updated_at"] = now

        try:
            # Phase 1: Try to find existing entity with lookup attributes
            # This avoids the complex lazy loading chains that cause greenlet issues
            lookup_query = select(self.model_class.id).where(
                self.model_class.is_deleted == False  # noqa: E712
            )

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
                options = []

                # Only include direct relationships that exist on this model
                for rel_name in self.mapper.get_default_relationships():
                    # Skip any nested relationships containing dots
                    if "." in rel_name:
                        continue

                    # Only add relationships that actually exist on this model class
                    if (
                        hasattr(self.model_class, rel_name)
                        and rel_name in inspect(self.model_class).relationships
                    ):
                        options.append(
                            selectinload(getattr(self.model_class, rel_name))
                        )

                # Use session.get with eager loading - this is the recommended pattern
                # for safely loading entities in an async context
                db_entity = await self.session.get(
                    self.model_class, existing_id, options=options
                )

                # Convert to domain model
                if db_entity is None:
                    raise ValueError("Failed to retrieve entity after update")
                if hasattr(self.mapper, 'to_domain_with_session'):
                    return await self.mapper.to_domain_with_session(db_entity, self.session)  # type: ignore[attr-defined]  # type: ignore[attr-defined]
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
                options = []

                # Only include direct relationships that exist on this model
                for rel_name in self.mapper.get_default_relationships():
                    # Skip any nested relationships containing dots
                    if "." in rel_name:
                        continue

                    # Only add relationships that actually exist on this model class
                    if (
                        hasattr(self.model_class, rel_name)
                        and rel_name in inspect(self.model_class).relationships
                    ):
                        options.append(
                            selectinload(getattr(self.model_class, rel_name))
                        )

                # Use session.get with eager loading for all needed relationships
                db_entity = await self.session.get(
                    self.model_class, new_id, options=options
                )

                # Convert to domain model
                if db_entity is None:
                    raise ValueError("Failed to retrieve entity after create")
                if hasattr(self.mapper, 'to_domain_with_session'):
                    return await self.mapper.to_domain_with_session(db_entity, self.session)  # type: ignore[attr-defined]  # type: ignore[attr-defined]
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
                db_entities = result.scalars().all()

                # Refresh all entities to load relationships
                for db_entity in db_entities:
                    for rel in self.mapper.get_default_relationships():
                        await self.session.refresh(db_entity, [rel])

                return await self.mapper.map_collection(list(db_entities))
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
