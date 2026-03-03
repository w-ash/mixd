"""Shared Pydantic v2 schemas for API responses.

Provides the standard response envelopes used across all endpoints:
paginated lists, error responses, and generic wrappers.
"""

from pydantic import BaseModel, ConfigDict


class ErrorDetail(BaseModel):
    """Structured error information returned in error responses."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    message: str
    details: dict[str, str] | None = None


class ErrorResponse(BaseModel):
    """Standard error envelope wrapping an ErrorDetail."""

    model_config = ConfigDict(from_attributes=True)

    error: ErrorDetail


class PaginatedResponse[T](BaseModel):
    """Generic paginated list response.

    Uses Python 3.12+ generic syntax for type-safe pagination
    across all list endpoints.
    """

    model_config = ConfigDict(from_attributes=True)

    data: list[T]
    total: int
    limit: int
    offset: int
