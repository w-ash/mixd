"""Shared JSON:API error-body parsing for connector error classifiers.

Apple Music and Tidal both answer failures with a JSON:API ``errors[]``
envelope. Parsing is defensive at every step — an absent, non-JSON, or
unexpectedly-shaped body reads as "no error object", never raises — so an
unparseable body can only lose the upstream detail string, never block a
classifier's own message. Service-specific interpretation (Apple's MUT-403
rule, Tidal's 401 message) stays in each connector's classifier.
"""

from typing import ClassVar

import httpx2
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.infrastructure.connectors._shared.http_client import response_text


class JsonApiError(BaseModel):
    """One JSON:API error object. Every field defensive — shapes unverified."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    id: str | None = Field(default=None)
    status: str | None = Field(default=None)
    code: str | None = Field(default=None)
    title: str | None = Field(default=None)
    detail: str | None = Field(default=None)


class JsonApiErrorResponse(BaseModel):
    """JSON:API error envelope: ``{"errors": [...]}``."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    errors: list[JsonApiError] = Field(default_factory=list)


def first_json_api_error(response: httpx2.Response) -> JsonApiError | None:
    """Parse the first JSON:API error object from a response body, if any."""
    text = response_text(response)
    if not text:
        return None
    try:
        envelope = JsonApiErrorResponse.model_validate_json(text)
    except ValidationError:
        return None
    return envelope.errors[0] if envelope.errors else None


def error_detail(error: JsonApiError | None) -> str | None:
    """The most useful human-readable string on a JSON:API error object.

    ``detail`` wins over ``title``; empty strings count as absent.
    """
    if error is None:
        return None
    return error.detail or error.title or None
