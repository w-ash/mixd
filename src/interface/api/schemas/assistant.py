"""Request/response schemas for the per-user assistant credential routes.

The Anthropic key is a write-only secret: it enters via ``PUT /assistant/key``
and is never echoed back in any response (v0.9.0.1).
"""

from typing import Literal

from pydantic import BaseModel, Field

# Anthropic Console keys are ~108 chars; cap generously to reject junk paste.
_MAX_KEY_LEN = 512


class AssistantStatusResponse(BaseModel):
    """Per-user capability signal that gates the whole chat surface."""

    connected: bool
    source: Literal["user", "server"] | None = None


class ConnectKeyRequest(BaseModel):
    """A pasted Anthropic API key to validate and store for the current user."""

    api_key: str = Field(..., min_length=1, max_length=_MAX_KEY_LEN)


class ConnectKeyResponse(BaseModel):
    """Confirmation that the key was validated and stored (never echoes the key)."""

    connected: Literal[True] = True
    source: Literal["user"] = "user"


class TestKeyRequest(BaseModel):
    """Test a candidate key, or (when omitted) the user's stored key."""

    api_key: str | None = Field(default=None, max_length=_MAX_KEY_LEN)


class TestKeyResponse(BaseModel):
    """Result of a live key-validation probe (a minimal completion)."""

    ok: bool
    detail: str | None = None
