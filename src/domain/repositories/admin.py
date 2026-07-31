"""Admin repository protocol — whole-database operational actions."""

from collections.abc import Awaitable, Collection, Sequence
from typing import Protocol


class AdminRepositoryProtocol(Protocol):
    """Destructive whole-schema operations for self-hosted instances.

    Deliberately takes the preserved-table set as an argument rather than
    owning it: *which* tables survive a reset is a product decision about what
    the user would hate to re-do (re-authenticating with Spotify), while
    enumerating the schema is a persistence detail.
    """

    def data_tables(self, preserved: Collection[str]) -> Sequence[str]:
        """Every table in the schema except ``preserved``.

        Derived from live ORM metadata so it auto-maintains as the schema
        evolves — a new table cannot be forgotten here.
        """
        ...

    def truncate_data_tables(
        self, preserved: Collection[str]
    ) -> Awaitable[Sequence[str]]:
        """Truncate every table except ``preserved``; returns what was truncated."""
        ...
