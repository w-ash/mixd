# pyright: reportAny=false, reportUnknownArgumentType=false, reportExplicitAny=false
# Legitimate Any/Unknown: SQLAlchemy column expression types
"""Generic batched tuple-IN lookup for deduplication queries.

Extracts the common pattern of batching multi-column IN queries to avoid
query planner degradation with very large IN lists. PostgreSQL has no hard
limit, but the planner struggles above ~10k tuples of multiple columns.
"""

from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy import tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import select

from src.config.constants import BusinessLimits


async def find_existing_by_tuples[TKey](
    session: AsyncSession,
    columns: Sequence[Any],
    keys: list[TKey],
    *,
    row_to_key: Callable[..., TKey],
    batch_size: int = BusinessLimits.TUPLE_IN_BATCH_SIZE,
) -> set[TKey]:
    """Batch multi-column IN lookup returning a set of existing keys.

    Args:
        session: Active async database session.
        columns: DB model columns to SELECT and use in the tuple IN clause.
        keys: List of tuples matching the column order to look up.
        row_to_key: Callable that converts a result row to a hashable key.
            Receives row attributes positionally matching ``columns``.
        batch_size: Maximum tuples per IN query (query planner safety limit).

    Returns:
        Set of keys that already exist in the database.
    """
    if not keys:
        return set()

    existing: set[TKey] = set()

    for i in range(0, len(keys), batch_size):
        batch = keys[i : i + batch_size]
        stmt = select(*columns).where(tuple_(*columns).in_(batch))
        result = await session.execute(stmt)
        existing.update(row_to_key(row) for row in result.all())

    return existing
