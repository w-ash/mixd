"""Shared Pydantic v2 schemas for API responses.

Provides the standard response envelopes used across all endpoints:
paginated lists and generic wrappers.
"""

from pydantic import BaseModel, ConfigDict


class PaginatedResponse[T](BaseModel):
    """Generic paginated list response.

    Uses Python 3.12+ generic syntax for type-safe pagination
    across all list endpoints.
    """

    model_config = ConfigDict(from_attributes=True)

    data: list[T]
    total: int | None = None
    limit: int
    offset: int
    next_cursor: str | None = None
