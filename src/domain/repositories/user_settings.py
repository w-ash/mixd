"""User settings repository protocol."""

from collections.abc import Awaitable
from typing import Protocol

from src.domain.entities.shared import JsonDict


class UserSettingsRepositoryProtocol(Protocol):
    """Persistence for a user's settings blob.

    One JSONB row per user. Reads fill in defaults for keys the row predates,
    so a user who set their theme before a new setting existed still gets a
    complete object rather than a missing key.
    """

    def load(self, user_id: str) -> Awaitable[JsonDict]:
        """Current settings for ``user_id``, defaults applied."""
        ...

    def patch(self, updates: JsonDict, user_id: str) -> Awaitable[JsonDict]:
        """Merge ``updates`` into the stored settings; returns the merged result."""
        ...
